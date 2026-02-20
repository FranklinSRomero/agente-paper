# PRIVACY

## Memoria global por `telegram_user_id`
El asistente guarda memoria global por usuario, no por chat:
- `users`: estado basico y autorizacion.
- `prefs`: preferencias declaradas por el usuario.
- `user_summary`: resumen conversacional breve y neutro.
- `user_memory_items`: hechos/notas/tareas/preferencias.

## Retencion
- Configurable por `MEMORY_RETENTION_DAYS` (default 365).
- Limpieza de registros antiguos soportada en la capa de memoria.

## Que no se guarda
- No se almacenan imagenes enviadas.
- No se deben incluir secretos/credenciales en summaries.

## Grupos vs privado
- En grupos/supergrupos se aplica politica anti-fuga:
  - No reutilizar detalles de `fact/todo/note` originados en chat privado.
  - Si el usuario quiere usar historial sensible, se recomienda confirmar o mover a privado.

## Derecho de borrado
- Comando `/forget`: elimina memoria del `user_id` solicitante.

## Transparencia
- Comando `/privacy` explica que datos se guardan y como borrarlos.
