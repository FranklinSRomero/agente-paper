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
target_db="${DUMP_DATABASE:-products}"
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
echo "Target database: $target_db"
echo "Starting mysql service (profile=mysql) if needed..."
"${COMPOSE[@]}" --profile mysql up -d mysql >/dev/null

echo "Waiting for MySQL to be ready..."
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T mysql mysqladmin ping -h localhost -uroot -proot >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

if ! "${COMPOSE[@]}" exec -T mysql mysqladmin ping -h localhost -uroot -proot >/dev/null 2>&1; then
  echo "ERROR: MySQL did not become ready in time."
  exit 1
fi

echo "Importing dump into mysql container..."
mysql_cmd=(mysql -uroot -proot --init-command="SET SESSION innodb_strict_mode=OFF;" "$target_db")
needs_rowfmt_patch=0
if [[ "$dump_file" == *.gz ]]; then
  if gzip -dc "$dump_file" | rg -q "ROW_FORMAT=COMPACT"; then
    needs_rowfmt_patch=1
  fi
else
  if rg -q "ROW_FORMAT=COMPACT" "$dump_file"; then
    needs_rowfmt_patch=1
  fi
fi

if [[ "$needs_rowfmt_patch" -eq 1 ]]; then
  echo "Compatibility mode: converting ROW_FORMAT=COMPACT -> ROW_FORMAT=DYNAMIC during import."
fi

if [[ "$dump_file" == *.gz ]]; then
  if [[ "$needs_rowfmt_patch" -eq 1 ]]; then
    gzip -dc "$dump_file" | sed 's/ROW_FORMAT=COMPACT/ROW_FORMAT=DYNAMIC/g' | "${COMPOSE[@]}" exec -T mysql "${mysql_cmd[@]}"
  else
    gzip -dc "$dump_file" | "${COMPOSE[@]}" exec -T mysql "${mysql_cmd[@]}"
  fi
else
  if [[ "$needs_rowfmt_patch" -eq 1 ]]; then
    sed 's/ROW_FORMAT=COMPACT/ROW_FORMAT=DYNAMIC/g' "$dump_file" | "${COMPOSE[@]}" exec -T mysql "${mysql_cmd[@]}"
  else
    "${COMPOSE[@]}" exec -T mysql "${mysql_cmd[@]}" < "$dump_file"
  fi
fi

echo "Import completed."
echo "Quick check:"
"${COMPOSE[@]}" exec -T mysql mysql -uroot -proot -e "SHOW DATABASES;"
