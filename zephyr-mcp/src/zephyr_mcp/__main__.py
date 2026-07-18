from mcp.server.fastmcp import FastMCP

from zephyr_mcp.client import ZephyrClient
from zephyr_mcp.config import load_config
from zephyr_mcp.tools import register_tools


def main() -> None:
    # Лёгкий режим самопроверки для установочных скриптов: `zephyr-mcp --help`
    # печатает справку и завершается ДО load_config() (иначе SystemExit об
    # отсутствии env-переменных не дал бы вывести справку) и не открывая
    # stdio-сессию. Используется в install/setup.sh / setup.ps1.
    import sys

    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(
            "zephyr-mcp — MCP server for Zephyr Scale / ATM (stdio transport).\n"
            "Run without arguments to start the MCP stdio server.\n"
            "Required env: ZEPHYR_BASE_URL, ZEPHYR_API_TOKEN."
        )
        return

    cfg = load_config()
    client = ZephyrClient(cfg)

    mcp = FastMCP("zephyr-squad")
    register_tools(mcp, client)

    mcp.run()


if __name__ == "__main__":
    main()
