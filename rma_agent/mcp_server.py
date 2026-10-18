"""MCP server exposing the RMA investigation tools over stdio.

Any MCP client (Claude Desktop, Claude Code, or rma_agent/agent.py) can
connect to it. Run directly with: python -m rma_agent.mcp_server
"""

import functools

from mcp.server.mcpserver import MCPServer

from rma_agent.investigate import TOOLS

MAX_IDS = 25


def _compact(value):
    """Trim long RMA id lists so tool results stay readable for the model."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "rma_ids" and len(v) > MAX_IDS:
                out[k] = v[:MAX_IDS]
                out["rma_ids_truncated"] = len(v) - MAX_IDS
            else:
                out[k] = _compact(v)
        return out
    if isinstance(value, list):
        return [_compact(v) for v in value]
    return value


def _compacted(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return _compact(fn(*args, **kwargs))
    return wrapper


server = MCPServer(
    name="rma-reconciliation",
    instructions=(
        "Tools for investigating RMA, receiving, transfer, and inventory discrepancies "
        "across a 3PL returns network. Start with network_overview."
    ),
)

for fn in TOOLS:
    server.add_tool(_compacted(fn))


if __name__ == "__main__":
    server.run("stdio")
