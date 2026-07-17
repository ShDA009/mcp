from mcp.server.fastmcp import FastMCP

from zephyr_mcp.client import ZephyrClient
from zephyr_mcp.config import load_config
from zephyr_mcp.tools import register_tools


def main() -> None:
    cfg = load_config()
    client = ZephyrClient(cfg)

    mcp = FastMCP("zephyr-squad")
    register_tools(mcp, client)

    mcp.run()


if __name__ == "__main__":
    main()
