import asyncio
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP

from zephyr_mcp.config import Config
from zephyr_mcp.tools import register_tools

READ_ONLY_TOOLS = {
    "list_executions",
    "get_execution",
    "get_test_case",
    "list_cycles",
    "list_test_cases",
    "get_test_cases_batch",
    "get_cycles_batch",
    "get_project",
    "list_projects",
}


WRITE_TOOLS = {
    "create_test_case",
    "create_folder",
    "create_test_run",
    "add_test_cases_to_run",
    "update_test_case",
    "add_execution_result",
    "archive_test_cases",
    "unarchive_test_cases",
}


def _tools(*, allow_write: bool, allow_delete: bool = False) -> dict:
    mcp = FastMCP("test")
    config = Config(
        base_url="https://jira.example.com",
        api_token="t",
        allow_write=allow_write,
        allow_delete=allow_delete,
    )
    register_tools(mcp, SimpleNamespace(), config)
    return {tool.name: tool for tool in asyncio.run(mcp.list_tools())}


def test_write_tool_absent_when_flag_disabled():
    assert set(_tools(allow_write=False)) == READ_ONLY_TOOLS


def test_write_tool_registered_when_flag_enabled():
    assert set(_tools(allow_write=True)) == READ_ONLY_TOOLS | WRITE_TOOLS


def test_delete_tool_absent_without_write_flag():
    assert set(_tools(allow_write=False, allow_delete=True)) == READ_ONLY_TOOLS


def test_delete_tool_registered_as_destructive_when_both_flags_enabled():
    tools = _tools(allow_write=True, allow_delete=True)
    assert set(tools) == READ_ONLY_TOOLS | WRITE_TOOLS | {"delete_test_runs"}
    assert tools["delete_test_runs"].annotations.destructiveHint is True
