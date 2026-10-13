"""Expose the HR tools as an MCP server (stdio).

Run with:  python -m app.tools.mcp_server

This lets other agent frameworks (Claude Desktop, Google ADK, LangGraph) call
the same tools. Mentioning MCP compliance is explicitly in the Angel One JD.
"""
from __future__ import annotations
import asyncio
import json
from typing import Any

try:
    from mcp.server.stdio import stdio_server
    from mcp.server import Server
    import mcp.types as mt
    _MCP_OK = True
except ImportError:
    _MCP_OK = False

from . import hrms


async def main() -> None:
    if not _MCP_OK:
        raise SystemExit("mcp package not installed. pip install mcp")

    server = Server("hr-copilot-tools")

    @server.list_tools()
    async def list_tools() -> list[mt.Tool]:
        return [
            mt.Tool(
                name="get_leave_balance",
                description="Fetch an employee's leave balances for a year.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "employee_id": {"type": "string"},
                        "year": {"type": "integer"},
                    },
                    "required": ["employee_id"],
                },
            ),
            mt.Tool(
                name="get_employee_profile",
                description="Fetch an employee's basic profile.",
                inputSchema={
                    "type": "object",
                    "properties": {"employee_id": {"type": "string"}},
                    "required": ["employee_id"],
                },
            ),
            mt.Tool(
                name="create_hr_ticket",
                description="Open an HR ticket on behalf of an employee.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "employee_id": {"type": "string"},
                        "category": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["employee_id", "category", "body"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[mt.TextContent]:
        result = await hrms.call_tool(name, arguments)
        return [mt.TextContent(type="text", text=json.dumps(result, default=str))]

    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
