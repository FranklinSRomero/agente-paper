#!/usr/bin/env bash
set -euo pipefail

BOT_URL="${BOT_URL:-http://localhost:8080/health}"
MCP_URL="${MCP_URL:-http://localhost:7000/health}"

curl -fsS "$BOT_URL" >/dev/null
curl -fsS "$MCP_URL" >/dev/null

echo "smoke ok"
