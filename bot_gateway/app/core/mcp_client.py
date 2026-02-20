import asyncio
import os
import random
from typing import Any

import httpx


class MCPToolClient:
    def __init__(self, base_url: str | None = None, auth_token: str | None = None, timeout_seconds: float = 15.0):
        self.base_url = base_url or os.getenv("MCP_BASE_URL", "http://mcp_server:7000")
        self.auth_token = auth_token if auth_token is not None else os.getenv("MCP_AUTH_TOKEN", "")
        self.timeout = httpx.Timeout(float(os.getenv("MCP_TOOL_TIMEOUT_SECONDS", str(timeout_seconds))))
        self.max_retries = int(os.getenv("MCP_TOOL_MAX_RETRIES", "2"))
        self.backoff_ms = int(os.getenv("MCP_TOOL_RETRY_BACKOFF_MS", "250"))

    def _is_retryable_status(self, status_code: int) -> bool:
        return status_code in (408, 429) or status_code >= 500

    async def call_tool(self, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/api/tool/{tool}"
        headers = {"Authorization": f"Bearer {self.auth_token}"}
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    r = await client.post(url, json=payload, headers=headers)
                    if r.status_code >= 400:
                        if attempt < self.max_retries and self._is_retryable_status(r.status_code):
                            sleep_ms = self.backoff_ms * (2**attempt) + random.randint(0, 120)
                            await asyncio.sleep(sleep_ms / 1000.0)
                            continue
                        return {"error": f"mcp_status_{r.status_code}", "body": r.text[:400]}
                    return r.json()
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < self.max_retries:
                    sleep_ms = self.backoff_ms * (2**attempt) + random.randint(0, 120)
                    await asyncio.sleep(sleep_ms / 1000.0)
                    continue
                return {"error": f"mcp_exception:{exc}"}
            except Exception as exc:
                return {"error": f"mcp_exception:{exc}"}
        return {"error": "mcp_exception:exhausted_retries"}
