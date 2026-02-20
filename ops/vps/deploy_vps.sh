#!/usr/bin/env bash
set -euo pipefail

HOST=""
USER=""
REMOTE_DIR="/opt/agente-paper"
WITH_MYSQL="false"
COPY_ENV="false"
IDENTITY_FILE=""

usage() {
  cat <<'EOF'
Usage:
  bash ops/vps/deploy_vps.sh --host <host> --user <user> [--dir <remote_dir>] [--with-mysql] [--copy-env] [--identity <path>]

Defaults:
  --dir /opt/agente-paper

Behavior:
  - Ships the repo contents to the VPS (tar over ssh).
  - By default does NOT copy local .env (security).
  - If remote .env is missing, creates it from .env.example.
  - Starts docker compose (optionally with mysql profile).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2;;
    --user) USER="${2:-}"; shift 2;;
    --dir) REMOTE_DIR="${2:-}"; shift 2;;
    --with-mysql) WITH_MYSQL="true"; shift 1;;
    --copy-env) COPY_ENV="true"; shift 1;;
    --identity) IDENTITY_FILE="${2:-}"; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2;;
  esac
done

if [[ -z "$HOST" || -z "$USER" ]]; then
  echo "Missing --host or --user" >&2
  usage
  exit 2
fi

SSH_TARGET="${USER}@${HOST}"

SSH_OPTS=(
  -o StrictHostKeyChecking=accept-new
  -o BatchMode=yes
)
if [[ -n "${IDENTITY_FILE}" ]]; then
  SSH_OPTS+=( -i "${IDENTITY_FILE}" -o IdentitiesOnly=yes )
fi

EXCLUDES=(
  --exclude='./.git'
  --exclude='./__pycache__'
  --exclude='./**/__pycache__'
  --exclude='./.pytest_cache'
  --exclude='./.mypy_cache'
  --exclude='./.ruff_cache'
  --exclude='./.venv'
  --exclude='./venv'
  --exclude='./node_modules'
  --exclude='./data/*.db'
  --exclude='./data/memory.db'
)

if [[ "$COPY_ENV" != "true" ]]; then
  EXCLUDES+=( --exclude='./.env' )
fi

REMOTE_CMD=$(cat <<EOF
set -euo pipefail
mkdir -p '${REMOTE_DIR}'
tar -xzf - -C '${REMOTE_DIR}'
cd '${REMOTE_DIR}'

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[remote] Created .env from .env.example."
  echo "[remote] Edit ${REMOTE_DIR}/.env with real secrets (TELEGRAM_BOT_TOKEN, GEMINI_API_KEY(S), MCP_AUTH_TOKEN, SHARE_TOKEN, allowlists)."
  echo "[remote] Re-run deploy after updating .env."
  exit 4
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "[remote] docker is not installed. Install Docker + Compose plugin, then re-run." >&2
  exit 3
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "[remote] docker compose plugin not found. Install docker-compose-plugin, then re-run." >&2
  exit 3
fi

# Production deploy should not use docker-compose.override.yml (dev bind mounts, DEBUG).
COMPOSE_FILES=(-f docker-compose.yml)

if [[ '${WITH_MYSQL}' == 'true' ]]; then
  \$COMPOSE "\${COMPOSE_FILES[@]}" --profile mysql up --build -d
else
  \$COMPOSE "\${COMPOSE_FILES[@]}" up --build -d
fi

\$COMPOSE "\${COMPOSE_FILES[@]}" ps
EOF
)

echo "[local] Deploying to ${SSH_TARGET}:${REMOTE_DIR}"
tar "${EXCLUDES[@]}" -czf - . | ssh "${SSH_OPTS[@]}" "${SSH_TARGET}" "${REMOTE_CMD}"
