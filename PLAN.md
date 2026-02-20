# PLAN - AI Telegram Assistant MVP Hardenizado

## 1) Objetivo
Construir un asistente de Telegram "always-on" autohospedado con:
- LLM Gemini con enrutamiento estructurado y validado.
- Memoria global persistente por `telegram_user_id` con aislamiento multi-tenant.
- Tooling aislado en `mcp_server` para MySQL de solo lectura.
- Ingesta de imágenes con barcode/QR + OCR como fallback.
- Control de acceso (allowlist + share token).
- Backlog automático de capacidades faltantes.
- Observabilidad, healthchecks y límites operativos.

## 2) Arquitectura

```text
Telegram User
   |
   v
bot_gateway (python-telegram-bot + Gemini + policy engine)
   |        |                 |
   |        |                 +--> SQLite /data/memory.db (users, prefs, summaries, memory_items)
   |        +--> Redis queue ---------> worker (vision/OCR jobs)
   |
   +--> mcp_server (MCP tools + SQL guardrails + token auth)
                    |
                    v
                 MySQL (compose profile opcional o externo)
```

## 3) Flujos clave

### 3.1 Mensaje de texto
```text
Telegram update -> authz -> rate limit -> memory fetch + policy filtering
-> Gemini router (structured output)
-> if needs_db: call MCP tool
-> compose response with sanitized tool output
-> update memory summary/prefs (según reglas)
-> Telegram reply
```

### 3.2 Mensaje con foto
```text
Telegram photo -> authz -> size checks
-> enqueue worker job (decode QR/barcode -> OCR fallback -> normalize)
-> wait bounded timeout for result
-> if identifier found: MCP search_products
-> answer + memory update
```

### 3.3 Enlace por token
```text
Unauthorized user -> /link <SHARE_TOKEN>
-> validate token -> users.is_authorized=true
-> subsequent requests allowed
```

## 4) Decisiones técnicas
- `python-telegram-bot` en modo polling (MVP simple y robusto).
- `FastAPI` en `bot_gateway` para endpoint `/health` + loop de polling en background.
- `mcp_server` con SDK oficial `mcp` (`FastMCP`) y transporte HTTP streamable.
- `SQLAlchemy` + `PyMySQL` para DB y SQLite.
- `redis + rq` para tareas pesadas de visión.
- `opencv-python-headless`, `pyzbar`, `pytesseract` para pipeline de imagen.

## 5) Seguridad y gobernanza
- Input de usuario tratado como no confiable.
- No SQL directo desde LLM.
- MCP exige token y aplica guardrails (`SELECT` only, sin comentarios, sin `;`, `LIMIT` requerido).
- Truncado/sanitización de salida de tools antes de reinyectar al LLM.
- Aislamiento por `user_id` en memoria.
- Política anti-fuga en grupos: solo preferencias generales por defecto.

## 6) Modelo de datos (SQLite)
- `users(user_id PK, created_at, last_seen, is_authorized, last_chat_id, last_chat_type)`
- `prefs(user_id, key, value, updated_at, PK(user_id,key))`
- `user_summary(user_id PK, summary_text, updated_at, msg_count)`
- `user_memory_items(id PK, user_id, kind, content, source_chat_id, source_chat_type, created_at)`

## 7) Entregables
- Repositorio completo con compose, Dockerfiles, servicios Python, docs y scripts.
- `README.md`, `SECURITY.md`, `PRIVACY.md`, `.env.example`, `Makefile`.
- `ops/scripts/smoke_test.sh` y SQL de init readonly.

## 8) Criterios de aceptación
- `docker compose up --build` inicia servicios.
- `/health` en `bot_gateway` y `mcp_server` responde `ok`.
- Mensajes autorizados reciben respuesta.
- `/link` permite autorizar usuario no allowlist.
- Foto con barcode/QR activa pipeline y búsqueda.
- `/forget` elimina memoria del `user_id`.
- Registro de capacidades faltantes en `data/capabilities_backlog.md`.
