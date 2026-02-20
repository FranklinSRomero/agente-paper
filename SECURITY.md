# SECURITY

## Modelo de amenazas
- Acceso no autorizado al bot.
- Prompt injection desde mensajes de usuario o resultados DB.
- SQL injection / abuso de DB.
- Fuga de memoria entre usuarios o contextos de chat.
- Exfiltracion de secretos.

## Mitigaciones implementadas
1. Control de acceso
- Allowlist por `TELEGRAM_ALLOWED_USER_IDS` y `TELEGRAM_ALLOWED_CHAT_IDS`.
- `SHARE_TOKEN` con `/link` para onboarding controlado.
- Estado de autorizacion persistido por `user_id`.

2. Aislamiento multi-tenant
- Memoria indexada solo por `telegram_user_id`.
- Eliminacion por usuario con `/forget`.

3. Anti-fuga en grupos
- En `group/supergroup` se restringe uso de `fact/todo/note` originados en chat privado.
- Solo preferencias generales por defecto.

4. Hardening de tools y SQL
- LLM no ejecuta SQL.
- `mcp_server` exige `MCP_AUTH_TOKEN`.
- Guardrails SQL: `SELECT` only, sin `;`, sin comentarios, `LIMIT` obligatorio y capado.
- `raw_select_restricted` usa whitelist de templates.

5. Prompt injection
- Entradas no confiables.
- Resultados de tools truncados/sanitizados antes de pasar al LLM.

6. Operacion segura
- Timeouts para LLM/vision/DB (en codigo y cliente HTTP).
- Rate limiting por usuario.
- Logs estructurados para auditoria.

## Recomendaciones de despliegue
- Rotar `SHARE_TOKEN`, `MCP_AUTH_TOKEN`, `GEMINI_API_KEY`.
- Usar red interna Docker y no exponer MySQL publicamente.
- TLS en reverse proxy delante de servicios HTTP.
- Backup y cifrado de volumen `data`.
