from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from zephyr_mcp.client import ZephyrClient
from zephyr_mcp.config import Config


def register_tools(mcp: FastMCP, client: ZephyrClient, config: Config) -> None:
    @mcp.tool()
    def list_executions(test_run_key: str) -> dict:
        """List Zephyr Scale test executions (items) in a test run/cycle, e.g. PROJ-C667."""
        return {"executions": client.list_executions(test_run_key)}

    @mcp.tool()
    def get_execution(test_run_key: str, test_case_key: str | None = None) -> dict:
        """Get detailed test execution result(s) (with step-level results) for a test run,
        e.g. PROJ-C667. Pass test_case_key (e.g. PROJ-T1124) to get a single execution,
        or omit it to get all executions in the run."""
        return {"result": client.get_execution(test_run_key, test_case_key)}

    @mcp.tool()
    def get_test_case(test_case_key: str) -> dict:
        """Get a Zephyr Scale test case by key, e.g. PROJ-T853. Includes steps in testScript.steps."""
        return {"test_case": client.get_test_case(test_case_key)}

    @mcp.tool()
    def list_cycles(project_key: str, folder: str | None = None, max_results: int = 50) -> dict:
        """Search Zephyr Scale test runs (cycles) in a project, e.g. project_key=PROJ.
        Pass folder as a path prefix (e.g. "/Папка" or "/Папка/Подпапка") to include that folder
        and all its subfolders. When folder is set, returns a lightweight list (key, folder, name
        only, no items) for the whole project subtree — use get_cycles_batch to fetch full
        objects for specific keys from the result."""
        return {"cycles": client.list_cycles(project_key, folder, max_results)}

    @mcp.tool()
    def list_test_cases(project_key: str, folder: str | None = None, max_results: int = 50) -> dict:
        """Search Zephyr Scale test cases in a project, e.g. project_key=PROJ.
        Pass folder as a path prefix (e.g. "/Папка" or "/Папка/Подпапка") to include that folder
        and all its subfolders. When folder is set, returns a lightweight list (key, folder, name
        only, no steps) for the whole project subtree — use get_test_cases_batch to fetch full
        objects for specific keys from the result."""
        return {"test_cases": client.list_test_cases(project_key, folder, max_results)}

    @mcp.tool()
    def get_test_cases_batch(project_key: str, test_case_keys: list[str]) -> dict:
        """Get full Zephyr Scale test case objects (with steps) for a list of keys,
        e.g. project_key=PROJ, test_case_keys=["PROJ-T1", "PROJ-T2"].
        Use after list_test_cases(folder=...) to fetch full details for the folder's test cases."""
        return {"test_cases": client.get_test_cases_batch(project_key, test_case_keys)}

    @mcp.tool()
    def get_cycles_batch(project_key: str, test_run_keys: list[str]) -> dict:
        """Get full Zephyr Scale test run (cycle) objects (with items) for a list of keys,
        e.g. project_key=PROJ, test_run_keys=["PROJ-C665", "PROJ-C666"].
        Use after list_cycles(folder=...) to fetch full details for the folder's test runs."""
        return {"cycles": client.get_cycles_batch(project_key, test_run_keys)}

    @mcp.tool()
    def get_project(project_id_or_key: str) -> dict:
        """Get a Jira project by numeric id or key, e.g. "16816" or "PROJ".
        Use this to resolve a project id (e.g. from a Jira URL) to its key."""
        return {"project": client.get_project(project_id_or_key)}

    @mcp.tool()
    def list_projects() -> dict:
        """List all Jira projects available to the token (id, key, name).
        Use this to find the project_key needed by other tools."""
        return {"projects": client.list_projects()}

    if not config.allow_write:
        return

    @mcp.tool()
    def create_test_case(
        project_key: str,
        name: str,
        steps: list[dict],
        objective: str | None = None,
        precondition: str | None = None,
        folder: str | None = None,
        labels: list[str] | None = None,
    ) -> dict:
        """Create a Zephyr Scale test case, e.g. project_key=PROJ.

        steps is a list of dicts: description (required), expectedResult and
        testData (optional). Step order is taken from the list.

        Text fields (objective, precondition, and each step's description,
        expectedResult, testData) are stored and rendered as HTML. Pass ready
        HTML: <ul><li>item</li></ul> for lists, <b> for emphasis, <br/> for a
        line break. Plain text without tags is fine too; escape literal < and >
        as &lt; and &gt;.

        folder is an existing folder path, e.g. "/Папка/Подпапка". The folder is
        not created — omit it to put the test case in the project root.

        Returns the raw created test case object from the API (response shape
        not verified against the live instance — the key is likely under "key",
        but this has not been curl-confirmed the way create_folder/create_test_run were).
        """
        return client.create_test_case(
            project_key, name, steps, objective, precondition, folder, labels
        )

    @mcp.tool()
    def update_test_case(
        test_case_key: str,
        name: str | None = None,
        objective: str | None = None,
        precondition: str | None = None,
        folder: str | None = None,
        labels: list[str] | None = None,
        steps: list[dict] | None = None,
        status: str | None = None,
    ) -> dict:
        """Update a Zephyr Scale test case by key, e.g. test_case_key=PROJ-T853.

        Only the explicitly passed fields are updated; omitted fields (None) are
        left untouched. Pass an empty string to clear a text field.

        steps, when passed, replaces the whole existing step list — it appends
        nothing to the current steps. Steps are a list of dicts: description
        (required), expectedResult and testData (optional). Step order is taken
        from the list.

        Text fields (objective, precondition, and each step's description,
        expectedResult, testData) are stored and rendered as HTML. Pass ready
        HTML: <ul><li>item</li></ul> for lists, <b> for emphasis, <br/> for a
        line break. Plain text without tags is fine too; escape literal < and >
        as &lt; and &gt;.

        folder is an existing folder path, e.g. "/Папка/Подпапка". The folder is
        not created — omit it to keep the current folder.

        status is a project-defined test case status (e.g. "Draft", "Approved",
        "Deprecated"); an unrecognized value is rejected by the server with a
        readable error.
        """
        return client.update_test_case(
            test_case_key, name, objective, precondition, folder, labels, steps, status
        )

    @mcp.tool()
    def create_folder(
        project_key: str,
        path: str,
        folder_type: str = "TEST_CASE",
    ) -> dict:
        """Create a Zephyr Scale folder, e.g. project_key=PROJ.

        path is the full path from the project root with a leading slash, e.g.
        "/Папка/Подпапка", not a single folder name. The server likely does not
        create intermediate folders from the path — parent folders must already
        exist (nested creation under a non-existent parent was not verified on
        the live instance).

        folder_type is the Zephyr folder type. Only "TEST_CASE" has been verified
        on the live instance; other values such as "TEST_RUN" or "TEST_PLAN" may
        or may not be accepted.

        Returns the created numeric folder id, e.g. {"id": 2945}.
        """
        return client.create_folder(project_key, path, folder_type)

    @mcp.tool()
    def create_test_run(
        project_key: str,
        name: str,
        test_case_keys: list[str] | None = None,
        folder: str | None = None,
    ) -> dict:
        """Create a Zephyr Scale test run (cycle), e.g. project_key=PROJ.

        test_case_keys is optional: pass a list of test case keys (e.g.
        ["PROJ-T853"]) to pre-populate the run, or omit it to create an empty
        run without items.

        folder is an existing folder path, e.g. "/Папка/Подпапка". The folder is
        not created — omit it to put the test run in the project root.

        Returns the created run key, e.g. {"key": "PROJ-C818"}.
        """
        return client.create_test_run(project_key, name, test_case_keys, folder)

    @mcp.tool()
    def add_test_cases_to_run(
        test_run_key: str,
        test_case_keys: list[str],
    ) -> dict:
        """Add test cases to an existing Zephyr Scale test run, e.g. test_run_key=PROJ-C818.

        New test cases are appended to the run's existing items; duplicate test
        case keys already present in the run are silently skipped.

        Uses POST /rest/atm/1.0/testrun/{key}/testresults with an array of new
        test case keys. Verified on the live instance: PUT /testrun/{key}
        returns 500 for any payload (it does not support updating items),
        while POST /testresults with an array adds each new test case to the
        run and creates a "Not Executed" execution record for it.
        """
        return client.add_test_cases_to_run(test_run_key, test_case_keys)

    @mcp.tool()
    def add_execution_result(
        test_run_key: str,
        test_case_key: str,
        status: str,
        comment: str | None = None,
        step_statuses: list[str] | None = None,
    ) -> dict:
        """Set the execution result for a test case already in a Zephyr Scale run,
        e.g. test_run_key=PROJ-C818, test_case_key=PROJ-T853, status="Pass".

        test_case_key must already be an item of the run (added via
        create_test_run or add_test_cases_to_run) — otherwise the call fails
        with "no test execution found". Updates the test case's execution in
        place; it does not create a duplicate history record.

        status is a project-defined value (e.g. "Pass", "Fail", "Not Executed");
        an unrecognized value is rejected by the server with a readable error.

        step_statuses is optional: a list of per-step statuses in step order
        (index 0 = first step). There is no field for a per-step actual-result
        text — the live instance rejects it (confirmed: only index+status are
        accepted per step).
        """
        return client.add_execution_result(
            test_run_key, test_case_key, status, comment, step_statuses
        )

    @mcp.tool()
    def archive_test_cases(test_case_keys: list[str]) -> dict:
        """Archive Zephyr Scale test cases by key, e.g. test_case_keys=["PROJ-T853"].

        Moves the test cases to the archive: they get "archived": true and
        disappear from the regular test case lists. Keys are resolved to numeric
        ids internally via GET /rest/tests/1.0/testcase/{key}?fields=id.

        Uses POST /rest/tests/1.0/testcase/bulk/archive with an array of numeric
        ids. Verified on the live instance. Duplicate keys (case-insensitive)
        are collapsed.
        """
        return client.archive_test_cases(test_case_keys)

    @mcp.tool()
    def unarchive_test_cases(test_case_keys: list[str]) -> dict:
        """Restore archived Zephyr Scale test cases by key, e.g. test_case_keys=["PROJ-T853"].

        Reverses archive_test_cases: the test cases get "archived": false and
        reappear in the regular test case lists. Keys are resolved to numeric
        ids internally via GET /rest/tests/1.0/testcase/{key}?fields=id.

        Uses POST /rest/tests/1.0/testcase/bulk/unarchive with an array of
        numeric ids. Verified on the live instance. Duplicate keys
        (case-insensitive) are collapsed.
        """
        return client.unarchive_test_cases(test_case_keys)

    if not config.allow_delete:
        return

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
    def delete_test_runs(test_run_keys: list[str]) -> dict:
        """Delete Zephyr Scale test runs (cycles) by key, e.g. test_run_keys=["PROJ-C818"].

        Permanently deletes the test runs. Keys are resolved to numeric ids
        internally via GET /rest/tests/1.0/testrun/{key}?fields=id.

        Uses POST /rest/tests/1.0/testrun/bulk/delete with an array of numeric
        ids. Verified on the live instance: deleted runs return 404 afterwards.
        Duplicate keys (case-insensitive) are collapsed.
        """
        return client.delete_test_runs(test_run_keys)
