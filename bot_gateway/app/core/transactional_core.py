import asyncio
import base64
import io
import logging
import os
import re
from typing import Any

import matplotlib

from ..backlog import CapabilitiesBacklog
from .mcp_client import MCPToolClient
from ..formatting import sanitize_for_llm
from ..llm_gemini import GeminiService
from ..memory.policies import filter_memory_for_chat
from ..memory.store import MemoryStore
from ..schemas import RouterDecision, RouterFilters
from ..tools.audio_transcriber import AudioTranscriberService
from ..tools.vision_service import VisionToolService

logger = logging.getLogger(__name__)
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


class TransactionalCore:
    def __init__(self, memory: MemoryStore):
        self.memory = memory
        self.gemini = GeminiService()
        self.backlog = CapabilitiesBacklog()
        self.mcp = MCPToolClient()
        self.strict_group = os.getenv("MEMORY_GROUP_CHAT_STRICT_MODE", "true").lower() == "true"
        self.summary_every = int(os.getenv("MEMORY_SUMMARY_EVERY_N_MESSAGES", "4"))
        self.vision_tool = VisionToolService()
        self.audio_tool = AudioTranscriberService(self.gemini)

        with open("/app/app/prompts/system.txt", "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    async def process_text(self, user_id: int, chat_id: int, chat_type: str, text: str) -> str:
        answer, _ = await self.process_text_with_media(user_id, chat_id, chat_type, text)
        return answer

    async def process_text_with_media(
        self, user_id: int, chat_id: int, chat_type: str, text: str
    ) -> tuple[str, bytes | None]:
        lowered = text.lower()
        if chat_type in ("group", "supergroup"):
            wants_history = any(
                key in lowered for key in ("usa mi historial", "recuerdame lo de antes", "recuérdame lo de antes")
            )
            confirms = "confirmo usar historial en grupo" in lowered
            if wants_history and not confirms:
                return (
                    "Para usar historial sensible en grupo, confirma con: "
                    "'confirmo usar historial en grupo', o continua en chat privado."
                ), None

        direct_memory = self._answer_from_personal_memory(user_id, lowered)
        if direct_memory:
            self._store_conversation_turn(user_id, chat_id, chat_type, text, direct_memory)
            await self._maybe_update_summary(user_id, text, direct_memory)
            return direct_memory, None

        prefs = self.memory.get_prefs(user_id)
        raw_items = self.memory.get_memory_items(user_id)
        policy_items = filter_memory_for_chat(raw_items, chat_type, self.strict_group)
        summary = self.memory.get_summary(user_id).summary_text

        decision = self._heuristic_route(text) or await self.gemini.aroute(text)
        if decision.ask_clarification:
            self._store_memory_from_text(user_id, chat_id, chat_type, text)
            self._store_conversation_turn(user_id, chat_id, chat_type, text, decision.ask_clarification)
            return decision.ask_clarification, None

        tool_payload = await self._maybe_query_tools(decision)
        tool_error_msg = self._humanize_tool_error(tool_payload)
        if tool_error_msg:
            self._store_memory_from_text(user_id, chat_id, chat_type, text)
            self._store_conversation_turn(user_id, chat_id, chat_type, text, tool_error_msg)
            return tool_error_msg, None
        report_text = self._format_sales_report(tool_payload, text, decision.intent)
        if report_text:
            self._store_memory_from_text(user_id, chat_id, chat_type, text)
            self._store_conversation_turn(user_id, chat_id, chat_type, text, report_text)
            await self._maybe_update_summary(user_id, text, report_text)
            report_png = self._build_sales_chart_png(tool_payload, text, decision.intent)
            return report_text, report_png
        option_list = self._format_product_options(tool_payload, decision.intent)
        if option_list:
            self._store_memory_from_text(user_id, chat_id, chat_type, text)
            self._store_conversation_turn(user_id, chat_id, chat_type, text, option_list)
            await self._maybe_update_summary(user_id, text, option_list)
            return option_list, None
        context = {
            "chat_type": chat_type,
            "prefs": prefs,
            "summary": summary,
            "memory_items": [
                {"kind": i.kind, "content": i.content[:300], "source_chat_type": i.source_chat_type}
                for i in policy_items
            ],
            "tool_payload": tool_payload,
        }
        answer = await self.gemini.arespond(self.system_prompt, text, context)

        self._store_memory_from_text(user_id, chat_id, chat_type, text)
        self._store_conversation_turn(user_id, chat_id, chat_type, text, answer)
        await self._maybe_update_summary(user_id, text, answer)
        return answer, None

    async def process_photo(self, user_id: int, chat_id: int, chat_type: str, image_bytes: bytes, caption: str | None) -> str:
        if not self.vision_tool.enabled:
            msg = "La vision esta desactivada por configuracion."
            self._store_conversation_turn(user_id, chat_id, chat_type, caption or "[foto]", msg)
            return msg

        b64 = base64.b64encode(image_bytes).decode("ascii")
        job = self.vision_tool.submit_image(b64)
        try:
            result = job.get_status(refresh=True)
            waited = 0
            while result not in ("finished", "failed") and waited < self.vision_tool.timeout_seconds:
                await asyncio.sleep(1)
                waited += 1
                result = job.get_status(refresh=True)
            if result != "finished":
                msg = "No pude procesar la imagen a tiempo. Intenta con mejor luz o enfoque."
                self._store_conversation_turn(user_id, chat_id, chat_type, caption or "[foto]", msg)
                return msg
            parsed = job.result or {}
        except Exception as exc:
            logger.warning("vision_job_error: %s", exc)
            msg = "Fallo el procesamiento de imagen."
            self._store_conversation_turn(user_id, chat_id, chat_type, caption or "[foto]", msg)
            return msg

        barcode = parsed.get("barcode")
        sku_candidates = parsed.get("sku_candidates") or []
        ocr_text = parsed.get("ocr_text")

        search = {}
        if barcode:
            search["barcode"] = barcode
        elif sku_candidates:
            search["sku"] = sku_candidates[0]
        elif ocr_text:
            search["texto"] = ocr_text[:80]

        if not search:
            self.backlog.add_missing(
                title="Vision mejorada para imagenes sin texto legible",
                user_request=caption or "Foto sin deteccion",
                reason="No se detecto QR/barcode ni OCR util.",
                impact="No se puede buscar producto automaticamente.",
                proposal="Agregar clasificador visual y detector de layout.",
                priority="Media",
            )
            msg = "No detecte codigo ni texto util en la foto."
            self._store_conversation_turn(user_id, chat_id, chat_type, caption or "[foto]", msg)
            return msg

        tool_payload = await self._call_mcp_tool("search_products", {**search, "limit": 5})
        if isinstance(tool_payload, dict) and not tool_payload.get("error"):
            if int(tool_payload.get("count", 0)) == 0 and sku_candidates:
                for sku_try in sku_candidates:
                    retry = await self._call_mcp_tool("search_products", {"sku": sku_try, "limit": 5})
                    if not retry.get("error") and int(retry.get("count", 0)) > 0:
                        tool_payload = retry
                        break
            if int(tool_payload.get("count", 0)) == 0 and ocr_text:
                retry = await self._call_mcp_tool("search_products", {"texto": ocr_text[:80], "limit": 5})
                if not retry.get("error") and int(retry.get("count", 0)) > 0:
                    tool_payload = retry
        tool_error_msg = self._humanize_tool_error(tool_payload)
        if tool_error_msg:
            self._store_conversation_turn(user_id, chat_id, chat_type, caption or "[foto]", tool_error_msg)
            return tool_error_msg
        if isinstance(tool_payload, dict) and int(tool_payload.get("count", 0)) == 0:
            detected = []
            if barcode:
                detected.append(f"codigo detectado: {barcode}")
            if sku_candidates:
                detected.append(f"sku detectado: {sku_candidates[0]}")
            if not detected and ocr_text:
                detected.append(f"texto detectado: {ocr_text[:40]}")
            if detected:
                msg = (
                    "No encontre ese producto en la base de datos. "
                    + " | ".join(detected)
                    + ". Puedes registrarlo o enviarme el SKU exacto."
                )
                self._store_conversation_turn(user_id, chat_id, chat_type, caption or "[foto]", msg)
                return msg
            msg = "No se encontraron resultados para la imagen. ¿Puedes proporcionar el código de barras o SKU?"
            self._store_conversation_turn(user_id, chat_id, chat_type, caption or "[foto]", msg)
            return msg
        safe_payload = sanitize_for_llm(tool_payload)
        text = caption or "Buscar producto por imagen"
        answer = await self.gemini.arespond(
            self.system_prompt,
            text,
            {"vision": parsed, "search_results": safe_payload},
        )
        self._store_conversation_turn(user_id, chat_id, chat_type, caption or "[foto]", answer)
        await self._maybe_update_summary(user_id, text, answer)
        return answer

    async def process_audio(
        self,
        user_id: int,
        chat_id: int,
        chat_type: str,
        audio_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> str:
        user_prompt = caption or "[audio]"
        try:
            transcription = await self.audio_tool.transcribe(audio_bytes, mime_type=mime_type, hint_text=caption)
        except Exception:
            logger.exception("audio_transcription_failed")
            msg = "No pude transcribir el audio en este momento. Intenta con un audio mas corto o texto."
            self._store_conversation_turn(user_id, chat_id, chat_type, user_prompt, msg)
            return msg

        if not transcription.strip():
            msg = "No pude extraer texto del audio. Intenta nuevamente con mejor claridad."
            self._store_conversation_turn(user_id, chat_id, chat_type, user_prompt, msg)
            return msg

        answer, _ = await self.process_text_with_media(
            user_id=user_id,
            chat_id=chat_id,
            chat_type=chat_type,
            text=transcription.strip(),
        )
        return f"Transcripcion: {transcription.strip()}\n\n{answer}"

    async def _maybe_query_tools(self, decision: RouterDecision) -> dict[str, Any]:
        if not decision.needs_db:
            return {"db": "not_required"}

        filters = decision.filters.model_dump(exclude_none=True)
        if decision.intent in ("buscar_producto", "detalle_producto"):
            return await self._call_mcp_tool("search_products", {**filters, "limit": 10})
        if decision.intent == "alertas_stock":
            return await self._call_mcp_tool("stock_alerts", {"threshold_mode": "low_stock", "limit": 10})
        if decision.intent == "insight":
            days = 30
            if decision.filters.price_min is not None:
                days = min(max(int(decision.filters.price_min), 1), 90)
            payload = {"days": days, "top_n": 10}
            if decision.filters.categoria:
                payload["categoria"] = decision.filters.categoria
            if decision.filters.sku:
                payload["sku"] = decision.filters.sku
            return await self._call_mcp_tool("sales_report", payload)

        self.backlog.add_missing(
            title=f"Tool para intent {decision.intent}",
            user_request=decision.model_dump_json(),
            reason="Intent no cubierto por tool actual.",
            impact="Respuesta limitada.",
            proposal="Agregar nueva tool MCP especializada.",
            priority="Media",
        )
        return {"error": "intent_without_tool"}

    async def _call_mcp_tool(self, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.mcp.call_tool(tool=tool, payload=payload)

    def _humanize_tool_error(self, payload: dict[str, Any]) -> str | None:
        err = str(payload.get("error", "")) if isinstance(payload, dict) else ""
        if not err:
            return None
        if "mcp_status_401" in err or "mcp_status_403" in err:
            return "No tengo autorización para consultar la base de datos en este momento."
        if "mcp_status_400" in err:
            return "La consulta a base de datos fue inválida. Intenta con SKU o código de barras exacto."
        if "mcp_status_500" in err or "mcp_exception" in err:
            return "No pude conectar con la base de datos en este momento. Intenta nuevamente en unos minutos."
        return "No pude consultar la base de datos en este momento."

    def _format_product_options(self, payload: dict[str, Any], intent: str) -> str | None:
        if intent not in ("buscar_producto", "detalle_producto"):
            return None
        if not isinstance(payload, dict):
            return None
        count = int(payload.get("count", 0))
        if count < 2:
            return None
        items = payload.get("items") or []
        if not isinstance(items, list):
            return None
        items = items[:10]
        if not items:
            return None

        lines = ["Encontre varias coincidencias. Elige una opcion:"]
        for idx, item in enumerate(items, start=1):
            sku = str(item.get("sku", "-"))
            name = str(item.get("product_name") or item.get("name") or "-")
            category = str(item.get("categoria", "-"))
            price = item.get("price", "-")
            stock = item.get("stock", "-")
            lines.append(f"{idx}. {name}")
            lines.append(f"   SKU: {sku} | Categoria: {category} | Precio: {price} | Stock: {stock}")
        lines.append("Responde con el SKU exacto para continuar.")
        return "\n".join(lines)

    def _heuristic_route(self, text: str) -> RouterDecision | None:
        lowered = text.lower()
        report_markers = (
            "reporte",
            "reportes",
            "ventas",
            "grafico",
            "gráfico",
            "categoria mas vendida",
            "categoría más vendida",
            "top productos",
            "tendencia",
        )
        if not any(k in lowered for k in report_markers):
            return None

        days = 30
        m = re.search(r"(?:ultimos|ultimas|ultimo|ultima)\s+(\d{1,2})\s+dias?", lowered)
        if not m:
            m = re.search(r"(\d{1,2})\s+dias?", lowered)
        if m:
            days = min(max(int(m.group(1)), 1), 90)

        filters = RouterFilters()
        if "lacteo" in lowered:
            filters.categoria = "lacteos"
        elif "despensa" in lowered:
            filters.categoria = "despensa"
        elif "snack" in lowered:
            filters.categoria = "snacks"

        filters.price_min = float(days)  # reuse for report window
        return RouterDecision(
            intent="insight",
            needs_db=True,
            needs_vision=False,
            filters=filters,
            confidence=0.95,
            ask_clarification=None,
        )

    def _format_sales_report(self, payload: dict[str, Any], user_text: str, intent: str) -> str | None:
        if intent != "insight" or not isinstance(payload, dict) or "summary" not in payload:
            return None

        summary = payload.get("summary") or {}
        top_categories = payload.get("category_breakdown") or []
        top_products = payload.get("top_products") or []
        days = payload.get("window_days", 30)

        lines = [f"Reporte de ventas ({days} dias):"]
        lines.append(
            "Ventas netas: {net} | Unidades netas: {units} | Devoluciones: {ret} | Transacciones: {tx}".format(
                net=summary.get("net_sales", "0"),
                units=summary.get("units_net", "0"),
                ret=summary.get("units_returned", "0"),
                tx=summary.get("tx_count", "0"),
            )
        )
        if top_categories:
            top_cat = top_categories[0]
            lines.append(
                f"Categoria mas vendida: {top_cat.get('categoria', '-')} (ventas netas {top_cat.get('net_sales', '0')})"
            )
        if top_products:
            lines.append("Top productos:")
            for i, row in enumerate(top_products[:5], start=1):
                lines.append(
                    f"{i}. {row.get('product_name', '-')} | SKU {row.get('sku', '-')} | Ventas {row.get('net_sales', '0')}"
                )

        lowered = user_text.lower()
        wants_chart = "grafico" in lowered or "gráfico" in lowered
        wants_python = "python" in lowered or "matplotlib" in lowered
        series = payload.get("daily_series") or []
        if wants_chart and series:
            values = [float(r.get("net_sales", 0) or 0) for r in series][-14:]
            labels = [str(r.get("sale_date", "")) for r in series][-14:]
            max_abs = max([abs(v) for v in values] + [1.0])
            lines.append("")
            lines.append("Grafico ASCII (ventas netas por dia):")
            for d, v in zip(labels, values):
                width = int((abs(v) / max_abs) * 18)
                bar = "#" * max(width, 1)
                sign = "-" if v < 0 else ""
                lines.append(f"{d} | {sign}{bar} {v:.2f}")

        if wants_python and series:
            labels = [str(r.get("sale_date", "")) for r in series][-30:]
            values = [float(r.get("net_sales", 0) or 0) for r in series][-30:]
            labels_json = str(labels).replace("'", '"')
            values_json = str(values)
            lines.append("")
            lines.append("Script Python (matplotlib):")
            lines.append("```python")
            lines.append("import matplotlib.pyplot as plt")
            lines.append("")
            lines.append(f"dates = {labels_json}")
            lines.append(f"net_sales = {values_json}")
            lines.append("")
            lines.append("plt.figure(figsize=(10,4))")
            lines.append("plt.plot(dates, net_sales, marker='o', linewidth=2)")
            lines.append("plt.title('Ventas netas por dia')")
            lines.append("plt.xlabel('Fecha')")
            lines.append("plt.ylabel('Ventas netas')")
            lines.append("plt.xticks(rotation=45, ha='right')")
            lines.append("plt.grid(alpha=0.3)")
            lines.append("plt.tight_layout()")
            lines.append("plt.show()")
            lines.append("```")

        return "\n".join(lines)

    def _build_sales_chart_png(self, payload: dict[str, Any], user_text: str, intent: str) -> bytes | None:
        if intent != "insight" or not isinstance(payload, dict):
            return None
        lowered = user_text.lower()
        wants_png = "png" in lowered or ("grafico" in lowered or "gráfico" in lowered) and "python" not in lowered
        if not wants_png:
            return None

        chart_ready = payload.get("chart_ready") or {}
        dates = chart_ready.get("x") or []
        values = chart_ready.get("y_net_sales") or []
        if not dates or not values:
            return None

        dates = dates[-30:]
        values = [float(v) for v in values[-30:]]
        colors = ["#0f766e" if v >= 0 else "#b91c1c" for v in values]

        fig, ax = plt.subplots(figsize=(10, 4.2), dpi=150)
        ax.bar(dates, values, color=colors, alpha=0.9)
        ax.plot(dates, values, color="#111827", linewidth=1.5, alpha=0.7)
        ax.set_title("Reporte de ventas netas por dia")
        ax.set_xlabel("Fecha")
        ax.set_ylabel("Ventas netas")
        ax.grid(axis="y", alpha=0.25, linestyle="--")
        ax.tick_params(axis="x", rotation=45, labelsize=8)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png")
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    def _store_memory_from_text(self, user_id: int, chat_id: int, chat_type: str, text: str) -> None:
        lowered = text.lower()
        if lowered.startswith("recuerda ") or "mi preferencia" in lowered:
            self.memory.add_memory_item(user_id, "preference", text[:500], chat_id, chat_type)
            return
        profile_markers = (
            "me llamo",
            "me gusta",
            "prefiero",
            "no me gusta",
            "mi nombre es",
            "soy ",
            "trabajo en",
            "vivo en",
            "mi cumple",
            "mis objetivos",
            "quiero aprender",
        )
        if any(m in lowered for m in profile_markers):
            self.memory.add_memory_item(user_id, "profile", text[:500], chat_id, chat_type)

    def _answer_from_personal_memory(self, user_id: int, lowered_text: str) -> str | None:
        ask_name_markers = (
            "como me llamo",
            "cómo me llamo",
            "cual es mi nombre",
            "cuál es mi nombre",
        )
        if not any(m in lowered_text for m in ask_name_markers):
            return None
        name = self._extract_user_name(user_id)
        if name:
            return f"Te llamas {name}."
        return "Aun no tengo tu nombre guardado. Puedes decirme: 'me llamo <tu nombre>'."

    def _extract_user_name(self, user_id: int) -> str | None:
        items = self.memory.get_memory_items(user_id, limit=40)
        name_patterns = [
            re.compile(r"\bme llamo\s+([a-zA-ZáéíóúñÁÉÍÓÚÑ]+(?:\s+[a-zA-ZáéíóúñÁÉÍÓÚÑ]+){0,2})", re.IGNORECASE),
            re.compile(
                r"\bmi nombre es\s+([a-zA-ZáéíóúñÁÉÍÓÚÑ]+(?:\s+[a-zA-ZáéíóúñÁÉÍÓÚÑ]+){0,2})",
                re.IGNORECASE,
            ),
            re.compile(r"\bsoy\s+([a-zA-ZáéíóúñÁÉÍÓÚÑ]+(?:\s+[a-zA-ZáéíóúñÁÉÍÓÚÑ]+){0,2})", re.IGNORECASE),
        ]
        for item in items:
            if item.kind not in ("profile", "chat_user"):
                continue
            txt = item.content.strip()
            for pat in name_patterns:
                m = pat.search(txt)
                if m:
                    return m.group(1).strip()
        return None

    def _store_conversation_turn(self, user_id: int, chat_id: int, chat_type: str, user_text: str, bot_text: str) -> None:
        self.memory.add_memory_item(user_id, "chat_user", user_text[:500], chat_id, chat_type)
        self.memory.add_memory_item(user_id, "chat_bot", bot_text[:500], chat_id, chat_type)

    async def _maybe_update_summary(self, user_id: int, user_text: str, bot_text: str) -> None:
        msg_count = self.memory.increment_msg_count(user_id)
        if msg_count % self.summary_every != 0:
            return
        prior = self.memory.get_summary(user_id).summary_text
        new_summary = await self.gemini.asummarize(prior, user_text, bot_text)
        if not new_summary or new_summary.strip() == prior.strip():
            items = self.memory.get_memory_items(user_id, limit=8)
            snippets = []
            for i in reversed(items):
                if i.kind in ("profile", "preference", "chat_user"):
                    snippets.append(i.content[:140])
            if snippets:
                new_summary = " | ".join(snippets)[-1800:]
        self.memory.update_summary(user_id, new_summary, msg_count)
