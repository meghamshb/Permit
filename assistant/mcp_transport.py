"""Shared local MCP transport. No providers, permissions or model dispatch policy.

C can use this transport for B's browser and D's sources. Adapters authorize calls
before reaching here. No raw tool output is logged or persisted by the transport.
"""

from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any


class StdioMCPClient:
    def __init__(self, command: str, args: list[str], cwd: str | Path | None = None):
        self.command = command
        self.args = args
        self.cwd = str(cwd) if cwd is not None else None
        self._stack: AsyncExitStack | None = None
        self._session: Any = None

    async def __aenter__(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        try:
            streams = await self._stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(command=self.command, args=self.args, cwd=self.cwd)
                )
            )
            self._session = await self._stack.enter_async_context(ClientSession(*streams))
            await self._session.initialize()
            return self
        except BaseException:
            await self._stack.aclose()
            self._stack = None
            raise

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._session is None:
            raise RuntimeError("MCP transport is not connected.")
        result = await self._session.call_tool(name, arguments)
        return result.model_dump(mode="json", by_alias=True)

    async def list_tools(self) -> list[dict[str, Any]]:
        if self._session is None:
            raise RuntimeError("MCP transport is not connected.")
        result = await self._session.list_tools()
        return [tool.model_dump(mode="json", by_alias=True) for tool in result.tools]

    async def __aexit__(self, exc_type, exc, traceback):
        self._session = None
        if self._stack is not None:
            await self._stack.__aexit__(exc_type, exc, traceback)
            self._stack = None
