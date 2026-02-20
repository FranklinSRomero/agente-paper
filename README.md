# AI Telegram Assistant (MVP Hardenizado)

Asistente de Telegram autohospedado con:
- Gemini (Developer API) para enrutado estructurado + respuesta.
- Integracion oficial de WhatsApp Cloud API (webhook + Graph API).
- Memoria global persistente por `telegram_user_id`.
- Aislamiento multi-tenant y politica anti-fuga en grupos.
- MCP server para MySQL read-only + introspeccion + busqueda.
- Reporteria de ventas desde MCP (resumen diario, top productos, categoria mas vendida).
- Pipeline de vision (QR/barcode + OCR fallback).
- Cola Redis/RQ para tareas pesadas.

## Arquitectura
- `bot_gateway`: Core transaccional + adapters de canal + authz + memory.
- `mcp_server`: tools MySQL solo lectura + guardrails + auth token.
- `worker`: jobs de vision/OCR.
- `redis`: broker de jobs.
- `mysql` (opcional con profile `mysql`) o MySQL externo.

### Modularizacion interna (`bot_gateway`)
- `app/core/transactional_core.py`:
  - Core transaccional del agente (routing, memoria, decision, respuesta).
  - Integracion con modelo barato (Gemini) y composicion final de respuesta.
- `app/core/mcp_client.py`:
  - Cliente MCP dedicado para conectar con tools de DB/POS (read-only).
- `app/channels/`:
  - `telegram_adapter.py` (activo): entrada/salida Telegram y registro de handlers.
  - `whatsapp_adapter.py` (activo): webhook oficial WhatsApp Cloud + Graph API.
  - `discord_adapter.py` (placeholder): contrato para eventos Discord.
  - `telegram_signals.py`: manejo de estados de plataforma (typing) para operaciones largas.
- `app/tools/`:
  - `vision_service.py`: tool de analisis de imagen (barcode/QR/OCR via worker).
  - `audio_transcriber.py`: tool de transcripcion de audio desacoplada del canal.

## Requisitos
- Docker + Docker Compose plugin.
- Token de bot Telegram.
- API key Gemini.
- Configurar ambas keys antes de iniciar:
  - `GEMINI_API_KEY` o `GEMINI_API_KEYS` (lista separada por comas para rotacion/failover)
  - `TELEGRAM_BOT_TOKEN` (tambien soporta `TELEGRAM_BOT_API` o `telegram_bot_api`)

## Setup rapido
1. Copiar env:
```bash
cp .env.example .env
```
2. Editar `.env` con credenciales.
3. Levantar stack sin MySQL interno:
```bash
make up
```
4. Levantar stack con MySQL interno:
```bash
make up-mysql
```
5. Verificar:
```bash
make smoke
```

Si no tienes Docker instalado (ejemplo: host Bazzite sin binario `docker`), `make up`/`make up-mysql` fallaran hasta instalar Docker/Compose.

## Canales de entrada
- Telegram (polling): habilitado por `BOT_ENABLE_TELEGRAM=true`.
- WhatsApp Cloud API (webhook): habilitar con `BOT_ENABLE_WHATSAPP=true`.

### WhatsApp Cloud API
Configura en `.env`:
- `WHATSAPP_PHONE_NUMBER_ID`
- `WHATSAPP_ACCESS_TOKEN`
- `WHATSAPP_VERIFY_TOKEN`
- opcionales: `WHATSAPP_GRAPH_VERSION`, `WHATSAPP_TYPING_BEST_EFFORT`

Webhook endpoints:
- Verificacion (Meta): `GET /webhooks/whatsapp`
- Eventos: `POST /webhooks/whatsapp`

Notas:
- El bot marca mensajes como leidos y hace best-effort de estado durante procesamiento largo.
- Soporta texto, imagen y audio (transcripcion + respuesta).
- Para exponer webhook localmente se puede usar:
```bash
~/.local/bin/cloudflared tunnel --url http://localhost:8081
```

## Configuracion MySQL
### Opcion A: MySQL en compose
- Usar `make up-mysql`.
- Inicializa esquema `products` con tabla `products_catalog` y datos semilla.
- Crea usuario readonly con `ops/scripts/init_mysql_readonly.sql`.
- Ajustar `.env` (`MYSQL_HOST=mysql`, etc.).

### Opcion B: MySQL externo (host/LAN)
- No usar profile mysql.
- Configurar `.env`:
  - `MYSQL_HOST=<host_o_ip>`
  - `MYSQL_PORT=3306`
  - `MYSQL_USER=<readonly_user>`
  - `MYSQL_PASSWORD=<readonly_password>`
  - `MYSQL_DATABASE=<db>`

## Comandos Telegram
- `/start` estado basico.
- `/link <SHARE_TOKEN>` autoriza user_id via token.
- `/prefs` lista preferencias.
- `/prefs clave=valor` guarda preferencia.
- `/forget` borra toda la memoria del `user_id` solicitante.
- `/privacy` explica almacenamiento y borrado.

## Flujo de autorizacion
1. Si usuario no esta en allowlist, queda bloqueado.
2. Ejecuta `/link <SHARE_TOKEN>`.
3. Se persiste `users.is_authorized=true` en SQLite.

## Vision
- Limite de tamano por `VISION_MAX_IMAGE_MB`.
- Pipeline: decode barcode/QR -> OCR fallback -> normalize -> search in MySQL.
- No se persisten imagenes.

## Audio
- El bot acepta notas de voz y audio de Telegram.
- Flujo: descargar media -> transcribir -> enviar transcripcion al Core transaccional.
- Limite configurable por `AUDIO_MAX_MB` (default `20`).

## Reportes de ventas
- La tabla `sales_transactions` se puebla con datos de ejemplo multi-dia desde `ops/scripts/init_mysql_readonly.sql`.
- MCP expone `sales_report` con:
  - resumen (ventas netas, unidades, devoluciones),
  - serie diaria (lista para graficar),
  - top productos,
  - desglose por categoria.
- Para consultas de insight, el bot usa este reporte en vez de solo introspeccion.

## Observabilidad
- Logs JSON estructurados.
- Healthchecks:
  - `GET /health` en bot_gateway.
  - `GET /health` en mcp_server.
- Rate limit por `user_id`.

## Robustez MCP
- Cliente MCP del bot con retry/backoff configurable:
  - `MCP_TOOL_TIMEOUT_SECONDS`
  - `MCP_TOOL_MAX_RETRIES`
  - `MCP_TOOL_RETRY_BACKOFF_MS`
- `mcp_server` maneja errores de validacion (`400`) y errores no controlados (`500`) con logging.

## Estructura
- `bot_gateway/` servicio principal.
- `bot_gateway/app/core/` core transaccional y cliente MCP.
- `bot_gateway/app/channels/` adapters de entrada por plataforma.
- `bot_gateway/app/tools/` tools locales (vision/audio).
- `mcp_server/` herramientas DB aisladas.
- `worker/` procesos pesados.
- `data/` `memory.db` y `capabilities_backlog.md`.
- `ops/scripts/` utilidades de operacion.

## Comandos utiles
```bash
make ps
make logs
make down
```
