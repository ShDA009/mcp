from mcp.server.fastmcp import FastMCP

from zephyr_mcp.client import ZephyrClient


def register_tools(mcp: FastMCP, client: ZephyrClient) -> None:
    @mcp.tool()
    def list_executions(test_run_key: str) -> dict:
        """List Zephyr Scale test executions (items) in a test run/cycle, e.g. CLOUDDEV-C667."""
        return {"executions": client.list_executions(test_run_key)}

    @mcp.tool()
    def get_execution(test_run_key: str, test_case_key: str | None = None) -> dict:
        """Get detailed test execution result(s) (with step-level results) for a test run,
        e.g. CLOUDDEV-C667. Pass test_case_key (e.g. CLOUDDEV-T1124) to get a single execution,
        or omit it to get all executions in the run."""
        return {"result": client.get_execution(test_run_key, test_case_key)}

    @mcp.tool()
    def get_test_case(test_case_key: str) -> dict:
        """Get a Zephyr Scale test case by key, e.g. CLOUDDEV-T853. Includes steps in testScript.steps."""
        return {"test_case": client.get_test_case(test_case_key)}

    @mcp.tool()
    def list_cycles(project_key: str, folder: str | None = None, max_results: int = 50) -> dict:
        """Search Zephyr Scale test runs (cycles) in a project, e.g. project_key=CLOUDDEV.
        Pass folder as a path prefix (e.g. "/Турбо" or "/Турбо/Портал") to include that folder
        and all its subfolders. When folder is set, returns a lightweight list (key, folder, name
        only, no items) for the whole project subtree — use get_cycles_batch to fetch full
        objects for specific keys from the result."""
        return {"cycles": client.list_cycles(project_key, folder, max_results)}

    @mcp.tool()
    def list_test_cases(project_key: str, folder: str | None = None, max_results: int = 50) -> dict:
        """Search Zephyr Scale test cases in a project, e.g. project_key=CLOUDDEV.
        Pass folder as a path prefix (e.g. "/Турбо" or "/Турбо/Портал") to include that folder
        and all its subfolders. When folder is set, returns a lightweight list (key, folder, name
        only, no steps) for the whole project subtree — use get_test_cases_batch to fetch full
        objects for specific keys from the result."""
        return {"test_cases": client.list_test_cases(project_key, folder, max_results)}

    @mcp.tool()
    def get_test_cases_batch(project_key: str, test_case_keys: list[str]) -> dict:
        """Get full Zephyr Scale test case objects (with steps) for a list of keys,
        e.g. project_key=CLOUDDEV, test_case_keys=["CLOUDDEV-T1", "CLOUDDEV-T2"].
        Use after list_test_cases(folder=...) to fetch full details for the folder's test cases."""
        return {"test_cases": client.get_test_cases_batch(project_key, test_case_keys)}

    @mcp.tool()
    def get_cycles_batch(project_key: str, test_run_keys: list[str]) -> dict:
        """Get full Zephyr Scale test run (cycle) objects (with items) for a list of keys,
        e.g. project_key=CLOUDDEV, test_run_keys=["CLOUDDEV-C665", "CLOUDDEV-C666"].
        Use after list_cycles(folder=...) to fetch full details for the folder's test runs."""
        return {"cycles": client.get_cycles_batch(project_key, test_run_keys)}

    @mcp.tool()
    def get_project(project_id_or_key: str) -> dict:
        """Get a Jira project by numeric id or key, e.g. "16816" or "CLOUDDEV".
        Use this to resolve a project id (e.g. from a Jira URL) to its key."""
        return {"project": client.get_project(project_id_or_key)}

    @mcp.tool()
    def list_projects() -> dict:
        """List all Jira projects available to the token (id, key, name).
        Use this to find the project_key needed by other tools."""
        return {"projects": client.list_projects()}
