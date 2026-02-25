#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT_DIR"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "ERROR: docker compose/docker-compose is required."
  exit 1
fi

dump_file="${1:-}"
if [[ -z "$dump_file" ]]; then
  mapfile -t candidates < <(find data -maxdepth 1 -type f \( -name "*.sql" -o -name "*.dump" -o -name "*.sql.gz" \) | sort)
  if [[ "${#candidates[@]}" -eq 0 ]]; then
    echo "ERROR: no dump file found in data/ (*.sql, *.dump, *.sql.gz)."
    exit 1
  fi
  if [[ "${#candidates[@]}" -gt 1 ]]; then
    echo "ERROR: multiple dump files found. Pass one explicitly:"
    printf '  %s\n' "${candidates[@]}"
    echo "Example: make import-dump DUMP_FILE=data/your_dump.sql"
    exit 1
  fi
  dump_file="${candidates[0]}"
fi

if [[ ! -f "$dump_file" ]]; then
  echo "ERROR: dump file not found: $dump_file"
  exit 1
fi

echo "Using dump: $dump_file"
echo "Starting mysql service (profile=mysql) if needed..."
"${COMPOSE[@]}" --profile mysql up -d mysql >/dev/null

echo "Importing dump into mysql container..."
if [[ "$dump_file" == *.gz ]]; then
  gzip -dc "$dump_file" | "${COMPOSE[@]}" exec -T mysql mysql -uroot -proot
else
  "${COMPOSE[@]}" exec -T mysql mysql -uroot -proot < "$dump_file"
fi

echo "Import completed."
echo "Quick check:"
"${COMPOSE[@]}" exec -T mysql mysql -uroot -proot -e "SHOW DATABASES;"
