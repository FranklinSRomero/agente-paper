import os

from fastapi import Header, HTTPException


MCP_AUTH_TOKEN = os.getenv("MCP_AUTH_TOKEN", "")


def require_token(authorization: str | None = Header(default=None)) -> None:
    if not MCP_AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="MCP_AUTH_TOKEN missing")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1]
    if token != MCP_AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="invalid token")
