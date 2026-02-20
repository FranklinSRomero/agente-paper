from datetime import datetime
from pathlib import Path


class CapabilitiesBacklog:
    def __init__(self, path: str = "/data/capabilities_backlog.md"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("# Capabilities Backlog\n\n", encoding="utf-8")

    def add_missing(self, title: str, user_request: str, reason: str, impact: str, proposal: str, priority: str) -> None:
        ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        block = (
            f"## [{ts}] Capacidad faltante: {title}\n"
            f"- Solicitud del usuario: \"{user_request}\"\n"
            f"- Motivo: {reason}\n"
            f"- Impacto: {impact}\n"
            f"- Propuesta de tool/skill: {proposal}\n"
            f"- Prioridad sugerida: {priority}\n\n"
        )
        with self.path.open("a", encoding="utf-8") as f:
            f.write(block)
