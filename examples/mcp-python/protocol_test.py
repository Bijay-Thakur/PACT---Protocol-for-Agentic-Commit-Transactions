"""Independent official Python MCP client against the PACT stdio facade.

The HTTP target is a disposable in-process stub with a test-only credential.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[2]
KEY = "pact_protocol_test_only"


class Stub(BaseHTTPRequestHandler):
    calls = 0

    def do_GET(self):
        self.answer()

    def do_POST(self):
        self.answer()

    def answer(self):
        type(self).calls += 1
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            status, body = 401, {"error": {"code": "UNAUTHENTICATED"}}
        elif self.path.startswith("/api/v1/contracts"):
            status, body = 200, {"contracts": []}
        elif self.path.startswith("/api/v1/workflows"):
            status, body = 200, {"workflows": []}
        else:
            status, body = 409, {"error": {"code": "SPECIFICATION_CLOSED", "message": "stub rejection"}}
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


async def run_session(port: int) -> None:
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "SystemRoot", "SYSTEMROOT"}}
    env.update({"PYTHONUNBUFFERED": "1", "PACT_MCP_API_KEY": KEY,
                "PACT_API_URL": f"http://127.0.0.1:{port}"})
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"],
                                   cwd=ROOT / "backend", env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"pact_list_workflows", "pact_list_contracts", "pact_begin",
                    "pact_revise", "pact_withdraw", "pact_request_commit"} <= names
            assert (await session.call_tool("pact_list_workflows")).structured_content["ok"] is True
            assert (await session.call_tool("pact_list_contracts")).structured_content["ok"] is True
            rejected = await session.call_tool("pact_revise", {
                "transaction_id": "00000000-0000-0000-0000-000000000000", "reason": "test"})
            assert rejected.structured_content["code"] == "SPECIFICATION_CLOSED"
            malformed = await session.call_tool("pact_revise", {"transaction_id": "bad"})
            assert malformed.is_error


async def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        await run_session(server.server_port)
        await run_session(server.server_port)
        assert Stub.calls == 6
        print(json.dumps({"status": "PASS", "sessions": 2, "http_calls": Stub.calls,
                          "boundary": "independent Python MCP protocol with HTTP stub"}))
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


if __name__ == "__main__":
    asyncio.run(main())
