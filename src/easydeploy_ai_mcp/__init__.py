"""EasyDeploy AI MCP server — REST tools for the EasyDeploy public API."""

# Set before the server import: the server reports it as serverInfo.version.
__version__ = "0.4.3"

from easydeploy_ai_mcp.server import EDA_MCP_TOOL_NAMES, mcp

__all__ = ["mcp", "EDA_MCP_TOOL_NAMES", "__version__"]
