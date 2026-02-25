# AGENTS.md

## Purpose
Operational instructions for future CLI/Codex agents working in this repository.

## Project Snapshot
- Telegram assistant with memory, Gemini routing/response, MCP-backed MySQL read-only tools, vision worker, and audio transcription.
- Main services:
  - `bot_gateway`
  - `mcp_server`
  - `worker`
  - `redis`
  - `mysql` (compose profile `mysql`)

## Critical Runtime Facts
1. Bot HTTP port is `8081` on host (container still `8080`).
2. MCP HTTP port is `7000` on host.
3. MySQL is `3306` on host.
4. Environment model should be `GEMINI_MODEL=gemini-2.5-flash-lite`.
5. Channel flags:
   - `BOT_ENABLE_TELEGRAM=true|false`
6. `cloudflared` is installed locally at `~/.local/bin/cloudflared` (version `2026.2.0`).

## First Commands to Run
1. `make up-mysql`
2. `docker-compose ps`
3. `curl -fsS http://localhost:8081/health`
4. `curl -fsS http://localhost:7000/health`
5. If loading a local MySQL backup dump:
   - `make import-dump DUMP_FILE=data/<archivo>.sql`

## Environment Constraint Noted
- Previous note (2026-02-20): `make up-mysql` failed in a host context where the `docker` binary was missing.
- In this environment, Docker is available via Distrobox, so run compose commands from the Distrobox shell.
- If the missing-binary issue repeats outside Distrobox:
  1. install Docker/Compose first, or
  2. run services in host mode without compose.

## Fast Debug Checklist
1. Telegram responds with generic clarification repeatedly:
- Check Gemini quota/key in logs.
- File: `bot_gateway/app/llm_gemini.py` now has explicit quota handling.

2. Product/SKU lookup fails:
- Validate DB seed data in MySQL:
  - table `products.products_catalog`
  - SKU `SKU-LACT-001`, barcode `7501000000001`
- Test MCP directly:
  - `POST /api/tool/search_products`
- Key code path:
  - `mcp_server/app/product_search.py`

3. Vision/barcode not resolving:
- Check worker status: `docker-compose ps worker`
- Check worker logs for job execution.
- If worker unhealthy, inspect healthcheck config in `docker-compose.yml`.

4. DB auth/runtime errors from MCP:
- Ensure `mcp_server` image includes `cryptography` dependency (`mcp_server/pyproject.toml`).

5. MCP intermittency/timeouts:
- Tune:
  - `MCP_TOOL_TIMEOUT_SECONDS`
  - `MCP_TOOL_MAX_RETRIES`
  - `MCP_TOOL_RETRY_BACKOFF_MS`

## Security Rules for Agents
1. Do not print or commit secrets from `.env`.
2. If secrets were exposed during troubleshooting, recommend immediate rotation.
3. Keep MCP protected with `MCP_AUTH_TOKEN`.

## Important Paths
- Transactional core: `bot_gateway/app/core/transactional_core.py`
- Backward compatibility alias: `bot_gateway/app/orchestrator.py`
- Gemini wrapper: `bot_gateway/app/llm_gemini.py`
- Channel adapters: `bot_gateway/app/channels/`
- MCP API/tool server: `mcp_server/app/server.py`
- Product search logic: `mcp_server/app/product_search.py`
- MCP client (bot side): `bot_gateway/app/core/mcp_client.py`
- DB init seed script: `ops/scripts/init_mysql_readonly.sql`
- Compose runtime: `docker-compose.yml`, `docker-compose.override.yml`

## Definition of Done for Typical Fixes
1. Service containers are up and healthy.
2. Health endpoints return OK.
3. Direct MCP product lookup works by SKU and barcode.
4. Telegram returns concrete price/SKU answer for seeded products.
5. Media flows (image/audio) work in at least one enabled channel.

## Git Workflow Rule
1. Use Git for all code changes from now on.
2. Do not commit `.env` or secrets.
3. Keep commits small and descriptive (`feat:`, `fix:`, `docs:`, `chore:`).
