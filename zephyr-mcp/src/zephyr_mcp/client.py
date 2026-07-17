import random
import time
from typing import Any

import httpx

from zephyr_mcp.config import Config

API_BASE_PATH = "/rest/atm/1.0"
JIRA_API_BASE_PATH = "/rest/api/2"
MAX_RETRIES = 3
BACKOFF_SCHEDULE = (0.5, 1.0, 2.0)
KEY_IN_BATCH_SIZE = 100


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
    ) -> Any:
        for attempt in range(MAX_RETRIES):
            try:
                response = self._http.request(method, path, params=params, json=json)
            except httpx.TimeoutException as exc:
                raise ZephyrError(f"request to Zephyr timed out: {path}") from exc
            except httpx.ConnectError as exc:
                raise ZephyrError(f"could not connect to Zephyr: {path}") from exc

            if response.status_code == 429 and attempt < MAX_RETRIES - 1:
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


def _chunk(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


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

    try:
        return response.json()
    except ValueError as exc:
        raise ZephyrError(f"invalid JSON from Zephyr: {path}") from exc
