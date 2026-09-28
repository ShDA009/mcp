import random
import re
import time
from typing import Any

import httpx

from zephyr_mcp.config import Config

API_BASE_PATH = "/rest/atm/1.0"
TESTS_API_BASE_PATH = "/rest/tests/1.0"
JIRA_API_BASE_PATH = "/rest/api/2"
MAX_RETRIES = 3
BACKOFF_SCHEDULE = (0.5, 1.0, 2.0)
KEY_IN_BATCH_SIZE = 100
KEY_PATTERNS = {
    "testcase": re.compile(r"[A-Z][A-Z0-9_]*-T\d+"),
    "testrun": re.compile(r"[A-Z][A-Z0-9_]*-C\d+"),
}


class ZephyrError(Exception):
    pass


class ZephyrClient:
    def __init__(self, cfg: Config):
        # Zephyr Scale uses Bearer auth with PAT
        self._http = httpx.Client(
            base_url=cfg.base_url,
            headers={
                "Authorization": f"Bearer {cfg.api_token}",
                "Accept": "application/json",
            },
            timeout=15.0,
            follow_redirects=False,
        )

    def list_executions(self, test_run_key: str) -> Any:
        test_run = self._request("GET", API_BASE_PATH + f"/testrun/{test_run_key}")
        if test_run is None:
            raise ZephyrError(f"empty response from Zephyr for test run: {test_run_key}")
        return test_run.get("items", [])

    def get_execution(self, test_run_key: str, test_case_key: str | None = None) -> Any:
        results = self._request("GET", API_BASE_PATH + f"/testrun/{test_run_key}/testresults")
        if test_case_key is None:
            return results
        matches = [r for r in results if r.get("testCaseKey") == test_case_key]
        if not matches:
            raise ZephyrError(f"not found: test case {test_case_key} in test run {test_run_key}")
        return matches[0]

    def get_test_case(self, test_case_key: str) -> Any:
        return self._request("GET", API_BASE_PATH + f"/testcase/{test_case_key}")

    def create_test_case(
        self,
        project_key: str,
        name: str,
        steps: list[dict],
        objective: str | None = None,
        precondition: str | None = None,
        folder: str | None = None,
        labels: list[str] | None = None,
    ) -> Any:
        payload = _build_test_case_payload(
            project_key, name, steps, objective, precondition, folder, labels
        )
        return self._request("POST", API_BASE_PATH + "/testcase", json=payload, retry_on_429=False)

    def update_test_case(
        self,
        test_case_key: str,
        name: str | None = None,
        objective: str | None = None,
        precondition: str | None = None,
        folder: str | None = None,
        labels: list[str] | None = None,
        steps: list[dict] | None = None,
        status: str | None = None,
    ) -> Any:
        payload = _build_test_case_update_payload(
            name, objective, precondition, folder, labels, steps, status
        )
        self._request(
            "PUT",
            API_BASE_PATH + f"/testcase/{test_case_key}",
            json=payload,
            retry_on_429=False,
        )
        return {"key": test_case_key, "updated": True}

    def create_folder(
        self,
        project_key: str,
        path: str,
        folder_type: str = "TEST_CASE",
    ) -> Any:
        validated_path = _validate_folder_path(path)
        payload: dict[str, Any] = {
            "projectKey": project_key,
            "name": validated_path,
            "type": folder_type,
        }
        return self._request("POST", API_BASE_PATH + "/folder", json=payload, retry_on_429=False)

    def create_test_run(
        self,
        project_key: str,
        name: str,
        test_case_keys: list[str] | None = None,
        folder: str | None = None,
    ) -> Any:
        payload = _build_test_run_payload(project_key, name, test_case_keys, folder)
        return self._request("POST", API_BASE_PATH + "/testrun", json=payload, retry_on_429=False)

    def add_test_cases_to_run(
        self,
        test_run_key: str,
        test_case_keys: list[str],
    ) -> Any:
        """Append test cases to an existing run, deduplicating by testCaseKey.

        Uses POST /testrun/{key}/testresults with an array of new test case
        keys. Verified on the live instance: PUT /testrun/{key} returns 500
        for any payload (it does not support updating items), while POST
        /testrun/{key}/testresults with an array adds each new test case to
        the run and creates a "Not Executed" execution record for it.
        """
        if not test_case_keys:
            raise ZephyrError("test run add has nothing to add")
        existing_items = self.list_executions(test_run_key)
        # POST /testresults creates a new execution per array element, so duplicates
        # (within the input or differing only by case) must be dropped here.
        existing_keys = {
            item["testCaseKey"].upper()
            for item in existing_items
            if item.get("testCaseKey")
        }
        new_keys = [
            key
            for key in dict.fromkeys(key.upper() for key in test_case_keys)
            if key not in existing_keys
        ]
        if not new_keys:
            return {"key": test_run_key, "updated": True, "added": 0}
        payload = [{"testCaseKey": key} for key in new_keys]
        self._request(
            "POST",
            API_BASE_PATH + f"/testrun/{test_run_key}/testresults",
            json=payload,
            retry_on_429=False,
        )
        return {"key": test_run_key, "updated": True, "added": len(new_keys)}

    def add_execution_result(
        self,
        test_run_key: str,
        test_case_key: str,
        status: str,
        comment: str | None = None,
        step_statuses: list[str] | None = None,
    ) -> Any:
        """Update the existing execution result for a test case already in a run.

        PUTs to the single-execution endpoint, which updates the test case's
        current execution in place (no duplicate history record, confirmed on
        the live instance). test_case_key must already be an item of the run
        (added via create_test_run/add_test_cases_to_run) — otherwise the
        server returns "No test execution found on test run ... for the
        informed parameters."
        """
        payload = _build_execution_result_payload(status, comment, step_statuses)
        return self._request(
            "PUT",
            API_BASE_PATH + f"/testrun/{test_run_key}/testcase/{test_case_key}/testresult",
            json=payload,
            retry_on_429=False,
        )

    def archive_test_cases(self, test_case_keys: list[str]) -> Any:
        """Archive test cases by key (moves them to the archive, hidden from lists).

        Uses POST /rest/tests/1.0/testcase/bulk/archive with an array of numeric
        ids. Keys are resolved to numeric ids via GET /rest/tests/1.0/testcase/{key}.
        Verified on the live instance: archived test cases get "archived": true
        and disappear from the ATM search.
        """
        if not test_case_keys:
            raise ZephyrError("test case archive has nothing to archive")
        keys = _dedup_keys(test_case_keys)
        ids = [self._get_numeric_id("testcase", key) for key in keys]
        self._request(
            "POST",
            TESTS_API_BASE_PATH + "/testcase/bulk/archive",
            json=ids,
            retry_on_429=False,
        )
        return {"archived": keys}

    def unarchive_test_cases(self, test_case_keys: list[str]) -> Any:
        """Restore archived test cases by key.

        Uses POST /rest/tests/1.0/testcase/bulk/unarchive with an array of
        numeric ids. Verified on the live instance: the test case gets
        "archived": false and reappears in the ATM search.
        """
        if not test_case_keys:
            raise ZephyrError("test case unarchive has nothing to restore")
        keys = _dedup_keys(test_case_keys)
        ids = [self._get_numeric_id("testcase", key) for key in keys]
        self._request(
            "POST",
            TESTS_API_BASE_PATH + "/testcase/bulk/unarchive",
            json=ids,
            retry_on_429=False,
        )
        return {"unarchived": keys}

    def delete_test_runs(self, test_run_keys: list[str]) -> Any:
        """Delete test runs (cycles) by key.

        Uses POST /rest/tests/1.0/testrun/bulk/delete with an array of numeric
        ids. Keys are resolved to numeric ids via GET /rest/tests/1.0/testrun/{key}.
        Verified on the live instance: deleted runs return 404 afterwards.
        """
        if not test_run_keys:
            raise ZephyrError("test run delete has nothing to delete")
        keys = _dedup_keys(test_run_keys)
        ids = [self._get_numeric_id("testrun", key) for key in keys]
        self._request(
            "POST",
            TESTS_API_BASE_PATH + "/testrun/bulk/delete",
            json=ids,
            retry_on_429=False,
        )
        return {"deleted": keys}

    def _get_numeric_id(self, resource: str, key: str) -> int:
        """Resolve a test case/run key to its numeric id via /rest/tests/1.0/.

        The ATM API works with keys, but the bulk archive/delete endpoints under
        /rest/tests/1.0/ expect numeric ids. GET /rest/tests/1.0/{resource}/{key}
        with fields=id returns {"id": <number>, "key": ...}.
        """
        # The resolved id feeds bulk archive/delete, so reject anything that is
        # not a plain key of the expected type before it reaches the URL path.
        if not KEY_PATTERNS[resource].fullmatch(key):
            raise ZephyrError(f"invalid {resource} key: {key!r}")
        result = self._request(
            "GET",
            TESTS_API_BASE_PATH + f"/{resource}/{key}",
            params={"fields": "id"},
        )
        if not result or "id" not in result:
            raise ZephyrError(f"could not resolve numeric id for {key}")
        numeric_id = result["id"]
        if not isinstance(numeric_id, int) or isinstance(numeric_id, bool):
            raise ZephyrError(f"could not resolve numeric id for {key}")
        if str(result.get("key", "")).upper() != key.upper():
            raise ZephyrError(f"resolved {resource} key mismatch: asked {key}, got {result.get('key')!r}")
        return numeric_id

    def list_cycles(self, project_key: str, folder: str | None = None, max_results: int = 50) -> Any:
        if folder is None:
            query = f'projectKey = "{_escape_jql(project_key)}"'
            return self._request(
                "GET",
                API_BASE_PATH + "/testrun/search",
                params={"query": query, "maxResults": max_results},
            )
        return self._list_by_folder_prefix(API_BASE_PATH + "/testrun/search", project_key, folder)

    def list_test_cases(self, project_key: str, folder: str | None = None, max_results: int = 50) -> Any:
        if folder is None:
            query = f'projectKey = "{_escape_jql(project_key)}"'
            return self._request(
                "GET",
                API_BASE_PATH + "/testcase/search",
                params={"query": query, "maxResults": max_results},
            )
        return self._list_by_folder_prefix(API_BASE_PATH + "/testcase/search", project_key, folder)

    def _list_by_folder_prefix(self, search_path: str, project_key: str, folder: str) -> Any:
        # ATM's `folder =` only matches exact leaf paths, and doesn't support prefix/contains
        # matching, so a plain query can't fetch a subtree. Fetching full objects for the whole
        # project is too slow (~54s for 2428 test cases, vs ~9s for a key+folder+name projection).
        # So folder search returns only this lightweight projection; callers who need full
        # objects for specific keys should use get_test_cases_batch/get_cycles_batch.
        query = f'projectKey = "{_escape_jql(project_key)}"'
        results = self._request(
            "GET",
            search_path,
            params={"query": query, "maxResults": 5000, "fields": "key,folder,name"},
        )
        return _filter_by_folder_prefix(results, folder)

    def get_test_cases_batch(self, project_key: str, test_case_keys: list[str]) -> Any:
        return self._get_by_keys_batch(API_BASE_PATH + "/testcase/search", project_key, test_case_keys)

    def get_cycles_batch(self, project_key: str, test_run_keys: list[str]) -> Any:
        return self._get_by_keys_batch(API_BASE_PATH + "/testrun/search", project_key, test_run_keys)

    def _get_by_keys_batch(self, search_path: str, project_key: str, keys: list[str]) -> Any:
        # A single `key IN (...)` query with ~660+ keys hits HTTP 414 (URI too long, confirmed
        # against the real instance). Batch into chunks that stay comfortably under that limit.
        results = []
        for batch in _chunk(keys, KEY_IN_BATCH_SIZE):
            keys_list = ", ".join(f'"{_escape_jql(k)}"' for k in batch)
            query = f'projectKey = "{_escape_jql(project_key)}" AND key IN ({keys_list})'
            results.extend(
                self._request(
                    "GET",
                    search_path,
                    params={"query": query, "maxResults": len(batch)},
                )
            )
        return results

    def get_project(self, project_id_or_key: str) -> Any:
        return self._request("GET", JIRA_API_BASE_PATH + f"/project/{project_id_or_key}")

    def list_projects(self) -> Any:
        projects = self._request("GET", JIRA_API_BASE_PATH + "/project")
        return [
            {"id": p.get("id"), "key": p.get("key"), "name": p.get("name")}
            for p in projects
        ]

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        retry_on_429: bool = True,
    ) -> Any:
        for attempt in range(MAX_RETRIES):
            try:
                response = self._http.request(method, path, params=params, json=json)
            except httpx.TimeoutException as exc:
                raise ZephyrError(f"request to Zephyr timed out: {path}") from exc
            except httpx.ConnectError as exc:
                raise ZephyrError(f"could not connect to Zephyr: {path}") from exc

            # Non-idempotent writes (e.g. POST /testcase) must not be retried on 429:
            # the request may have already succeeded server-side before the 429 was
            # returned (e.g. by a fronting proxy), and a retry would create a duplicate.
            if response.status_code == 429 and retry_on_429 and attempt < MAX_RETRIES - 1:
                time.sleep(_retry_delay(response, attempt))
                continue

            return _handle_response(response, path)

        raise ZephyrError("rate limited by Zephyr, retries exhausted")


def _filter_by_folder_prefix(items: list[dict], folder: str | None) -> list[dict]:
    if folder is None:
        return items
    prefix = folder.rstrip("/")
    return [
        item
        for item in items
        if (item_folder := item.get("folder")) is not None
        and (item_folder == prefix or item_folder.startswith(prefix + "/"))
    ]


def _escape_jql(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _build_steps(steps: list[dict]) -> list[dict]:
    # The live ATM instance rejects an "index" field in StepDTO on write
    # (POST/PUT /testcase), so step order is conveyed by list order only and
    # the server assigns indexes itself.
    built_steps = []
    for index, step in enumerate(steps):
        description = step.get("description")
        if not description:
            raise ZephyrError(f"step {index} has no description")
        built: dict[str, Any] = {"description": description}
        if step.get("expectedResult"):
            built["expectedResult"] = step["expectedResult"]
        if step.get("testData"):
            built["testData"] = step["testData"]
        built_steps.append(built)
    return built_steps


def _validate_folder_path(path: str) -> str:
    if not path:
        raise ZephyrError("folder path must not be empty")
    if path == "/":
        raise ZephyrError("folder path must not be just '/'")
    if not path.startswith("/"):
        raise ZephyrError(f"folder path must start with '/': {path}")
    if "\\" in path:
        raise ZephyrError("folder path must not contain backslashes")
    return path


def _build_test_case_payload(
    project_key: str,
    name: str,
    steps: list[dict],
    objective: str | None,
    precondition: str | None,
    folder: str | None,
    labels: list[str] | None,
) -> dict:
    # ATM stores step-by-step scripts flat in testScript.steps; the
    # stepByStepScript wrapper belongs to /rest/tests/1.0 and is rejected here.
    # status, priority and majorVersion are filled in by the server.
    if not steps:
        raise ZephyrError("test case needs at least one step")

    payload: dict[str, Any] = {"projectKey": project_key, "name": name}

    if objective:
        payload["objective"] = objective
    if precondition:
        payload["precondition"] = precondition
    if folder:
        payload["folder"] = _validate_folder_path(folder)
    if labels:
        payload["labels"] = labels

    built_steps = _build_steps(steps)
    payload["testScript"] = {"type": "STEP_BY_STEP", "steps": built_steps}
    return payload


def _build_test_case_update_payload(
    name: str | None,
    objective: str | None,
    precondition: str | None,
    folder: str | None,
    labels: list[str] | None,
    steps: list[dict] | None,
    status: str | None = None,
) -> dict:
    if all(
        field is None
        for field in (name, objective, precondition, folder, labels, steps, status)
    ):
        raise ZephyrError("test case update has nothing to change")

    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if objective is not None:
        payload["objective"] = objective
    if precondition is not None:
        payload["precondition"] = precondition
    if folder is not None:
        payload["folder"] = _validate_folder_path(folder)
    if labels is not None:
        payload["labels"] = labels
    if status is not None:
        payload["status"] = status

    if steps is not None:
        built_steps = _build_steps(steps)
        payload["testScript"] = {"type": "STEP_BY_STEP", "steps": built_steps}

    return payload


def _build_test_run_payload(
    project_key: str,
    name: str,
    test_case_keys: list[str] | None,
    folder: str | None,
) -> dict:
    payload: dict[str, Any] = {"projectKey": project_key, "name": name}
    if test_case_keys:
        payload["items"] = [{"testCaseKey": key} for key in test_case_keys]
    if folder:
        payload["folder"] = _validate_folder_path(folder)
    return payload


def _build_execution_result_payload(
    status: str,
    comment: str | None,
    step_statuses: list[str] | None,
) -> dict:
    if not status:
        raise ZephyrError("execution result needs a status")

    payload: dict[str, Any] = {"status": status}
    if comment:
        payload["comment"] = comment
    if step_statuses:
        # TestScriptResultDTO on the live instance only accepts index+status;
        # an actual-result/comment field per step does not exist (confirmed:
        # both "actualResult" and "result" are rejected with 500 Unrecognized field).
        payload["scriptResults"] = [
            {"index": index, "status": step_status}
            for index, step_status in enumerate(step_statuses)
        ]
    return payload


def _chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _dedup_keys(keys: list[str]) -> list[str]:
    """Deduplicate keys case-insensitively, preserving first-seen order."""
    return list(dict.fromkeys(key.upper() for key in keys))


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return float(retry_after)
        except ValueError:
            pass
    return BACKOFF_SCHEDULE[attempt] + random.uniform(0, 0.25)


def _handle_response(response: httpx.Response, path: str) -> Any:
    status = response.status_code

    if status in (401, 403):
        raise ZephyrError("bad credentials or no access to this project")
    if status == 404:
        raise ZephyrError(f"not found: {path}")
    if status == 429:
        raise ZephyrError("rate limited by Zephyr, retries exhausted")
    if status >= 400:
        snippet = response.text[:512]
        raise ZephyrError(f"zephyr API error {status}: {snippet}")

    if not response.text.strip():
        return None

    try:
        return response.json()
    except ValueError as exc:
        raise ZephyrError(f"invalid JSON from Zephyr: {path}") from exc
