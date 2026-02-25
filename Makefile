.PHONY: up up-mysql down logs ps build smoke fmt import-dump

COMPOSE := $(shell if docker compose version >/dev/null 2>&1; then echo "docker compose"; elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; else echo "docker compose"; fi)

up:
	$(COMPOSE) up --build -d

up-mysql:
	$(COMPOSE) --profile mysql up --build -d

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

ps:
	$(COMPOSE) ps

build:
	$(COMPOSE) build

smoke:
	bash ops/scripts/smoke_test.sh

import-dump:
	bash ops/scripts/import_mysql_dump.sh "$(DUMP_FILE)"
