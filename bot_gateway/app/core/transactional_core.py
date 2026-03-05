import asyncio
import base64
import io
import logging
import os
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

import matplotlib

from ..backlog import CapabilitiesBacklog
from .mcp_client import MCPToolClient
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
        self.fuzzy_top_k = int(os.getenv("POS_FUZZY_TOP_K", "5"))
        self.fuzzy_pool_size = int(os.getenv("POS_FUZZY_POOL_SIZE", "30"))
        self.fuzzy_strong_threshold = float(os.getenv("POS_FUZZY_STRONG_THRESHOLD", "0.85"))
        self.fuzzy_candidate_threshold = float(os.getenv("POS_FUZZY_CANDIDATE_THRESHOLD", "0.60"))
        self._pending_option_items: dict[tuple[int, int], dict[str, Any]] = {}
        self.vision_tool = VisionToolService()
        self.audio_tool = AudioTranscriberService(self.gemini)

        with open("/app/app/prompts/system.txt", "r", encoding="utf-8") as f:
            self.system_prompt = f.read()

    async def process_text(self, user_id: int, chat_id: int, chat_type: str, text: str) -> str:
        answer, _ = await self.process_text_with_media(user_id, chat_id, chat_type, text)
        return answer

    def likely_uses_llm_for_text(self, text: str) -> bool:
        raw = (text or "").strip()
        if not raw:
            return False
        lowered = self._normalize_text(raw)
        if lowered in ("/ayuda", "ayuda", "help"):
            return False
        if re.match(r"^/(precio|stock|buscar)\b", raw, flags=re.IGNORECASE):
            return False
        if self._heuristic_route(raw):
            return False
        if any(
            k in lowered
            for k in (
                "precio",
                "cuanto",
                "valor",
                "costo",
                "stock",
                "inventario",
                "buscar",
                "producto",
                "referencia",
                "sku",
                "codigo",
                "barcode",
            )
        ):
            return False
        return True

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
            self._clear_pending_options(user_id, chat_id)
            self._store_conversation_turn(user_id, chat_id, chat_type, text, direct_memory)
            await self._maybe_update_summary(user_id, text, direct_memory)
            return direct_memory, None

        deterministic = await self._resolve_deterministic_text(text, user_id=user_id, chat_id=chat_id)
        if deterministic is not None:
            answer, chart_png = deterministic
            self._store_memory_from_text(user_id, chat_id, chat_type, text)
            self._store_conversation_turn(user_id, chat_id, chat_type, text, answer)
            await self._maybe_update_summary(user_id, text, answer)
            return answer, chart_png

        prefs = self.memory.get_prefs(user_id)
        raw_items = self.memory.get_memory_items(user_id)
        policy_items = filter_memory_for_chat(raw_items, chat_type, self.strict_group)
        summary = self.memory.get_summary(user_id).summary_text

        if self.gemini.is_temporarily_unavailable():
            self._clear_pending_options(user_id, chat_id)
            fallback = "IA no disponible temporalmente. Usa /precio <sku>, /stock <sku> o /buscar <texto>."
            self._store_memory_from_text(user_id, chat_id, chat_type, text)
            self._store_conversation_turn(user_id, chat_id, chat_type, text, fallback)
            return fallback, None

        decision = self._heuristic_route(text) or await self.gemini.aroute(text)
        if decision.ask_clarification:
            self._clear_pending_options(user_id, chat_id)
            self._store_memory_from_text(user_id, chat_id, chat_type, text)
            self._store_conversation_turn(user_id, chat_id, chat_type, text, decision.ask_clarification)
            return decision.ask_clarification, None

        if decision.needs_db:
            tool_payload = await self._maybe_query_tools(decision)
        else:
            forced = await self._try_forced_product_lookup(text)
            tool_payload = forced if forced is not None else {"db": "not_required"}
        tool_error_msg = self._humanize_tool_error(tool_payload)
        if tool_error_msg:
            self._clear_pending_options(user_id, chat_id)
            self._store_memory_from_text(user_id, chat_id, chat_type, text)
            self._store_conversation_turn(user_id, chat_id, chat_type, text, tool_error_msg)
            return tool_error_msg, None
        report_text = self._format_sales_report(tool_payload, text, decision.intent)
        if report_text:
            self._clear_pending_options(user_id, chat_id)
            self._store_memory_from_text(user_id, chat_id, chat_type, text)
            self._store_conversation_turn(user_id, chat_id, chat_type, text, report_text)
            await self._maybe_update_summary(user_id, text, report_text)
            report_png = self._build_sales_chart_png(tool_payload, text, decision.intent)
            return report_text, report_png
        product_detail = self._format_single_product_detail(tool_payload, decision.intent)
        if product_detail:
            self._clear_pending_options(user_id, chat_id)
            self._store_memory_from_text(user_id, chat_id, chat_type, text)
            self._store_conversation_turn(user_id, chat_id, chat_type, text, product_detail)
            await self._maybe_update_summary(user_id, text, product_detail)
            return product_detail, None
        option_list = self._format_product_options(tool_payload, decision.intent)
        if option_list:
            self._set_pending_options(user_id, chat_id, tool_payload)
            self._store_memory_from_text(user_id, chat_id, chat_type, text)
            self._store_conversation_turn(user_id, chat_id, chat_type, text, option_list)
            await self._maybe_update_summary(user_id, text, option_list)
            return option_list, None
        self._clear_pending_options(user_id, chat_id)
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

    def _set_pending_options(self, user_id: int, chat_id: int, payload: dict[str, Any]) -> None:
        items = payload.get("items") or []
        if not isinstance(items, list):
            return
        cooked: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            ident = str(item.get("barcode") or item.get("sku") or item.get("reference") or item.get("code") or "").strip()
            if not ident:
                continue
            name = str(item.get("product_name") or item.get("name") or "Producto").strip()
            price_sell = item.get("pricesell")
            if price_sell is None:
                price_sell = item.get("price")
            cooked.append({"id": ident, "name": name, "price": self._fmt_price_short(price_sell)})
        if cooked:
            self._pending_option_items[(user_id, chat_id)] = {"items": cooked, "offset": 0}

    def _clear_pending_options(self, user_id: int, chat_id: int) -> None:
        self._pending_option_items.pop((user_id, chat_id), None)

    def get_pending_options(self, user_id: int, chat_id: int) -> list[dict[str, str]]:
        data = self._pending_option_items.get((user_id, chat_id)) or {}
        items = data.get("items") or []
        offset = int(data.get("offset", 0))
        size = max(self.fuzzy_top_k, 1)
        return list(items[offset : offset + size])

    def pending_options_has_more(self, user_id: int, chat_id: int) -> bool:
        data = self._pending_option_items.get((user_id, chat_id)) or {}
        items = data.get("items") or []
        offset = int(data.get("offset", 0))
        size = max(self.fuzzy_top_k, 1)
        return (offset + size) < len(items)

    def advance_pending_options_page(self, user_id: int, chat_id: int) -> list[dict[str, str]]:
        data = self._pending_option_items.get((user_id, chat_id))
        if not data:
            return []
        items = data.get("items") or []
        offset = int(data.get("offset", 0))
        size = max(self.fuzzy_top_k, 1)
        if (offset + size) >= len(items):
            return []
        next_offset = offset + size
        data["offset"] = next_offset
        self._pending_option_items[(user_id, chat_id)] = data
        return list(items[next_offset : next_offset + size])

    def _fmt_price_short(self, value: Any) -> str:
        if value is None:
            return "-"
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return str(value)
        if amount.is_integer():
            return f"${int(amount):,}"
        return f"${amount:,.2f}"

    def _normalize_text(self, text: str) -> str:
        stripped = unicodedata.normalize("NFD", text or "")
        without_marks = "".join(ch for ch in stripped if unicodedata.category(ch) != "Mn")
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s._-]", " ", without_marks.lower())).strip()

    def _tokenize(self, text: str) -> list[str]:
        return [tok for tok in self._normalize_text(text).split(" ") if tok]

    def _normalize_search_tokens(self, tokens: list[str]) -> list[str]:
        # Lightweight domain synonyms and spelling variants for stationery retail.
        synonym_map = {
            "esferos": "esfero",
            "esferito": "esfero",
            "esferitos": "esfero",
            "lapicero": "esfero",
            "lapiceros": "esfero",
            "boligrafo": "esfero",
            "boligrafos": "esfero",
            "lapices": "lapiz",
            "resmas": "resma",
            "korez": "kores",
            "cores": "kores",
        }
        normalized: list[str] = []
        for tok in tokens:
            t = synonym_map.get(tok, tok)
            if t not in normalized:
                normalized.append(t)
        return normalized

    def _extract_query_entities(self, text: str) -> dict[str, str | None]:
        normalized = self._normalize_text(text)
        barcode_match = re.search(r"\b\d{8,14}\b", normalized)
        barcode = barcode_match.group(0) if barcode_match else None
        sku_match = re.search(r"\b[a-z0-9][a-z0-9._-]{2,}\b", normalized)
        sku = None
        if sku_match:
            candidate = sku_match.group(0)
            stop = {
                "precio",
                "stock",
                "buscar",
                "producto",
                "codigo",
                "barcode",
                "inventario",
                "categoria",
                "ayuda",
            }
            if candidate not in stop and not candidate.isdigit():
                if re.search(r"\d", candidate) or "-" in candidate or "_" in candidate or "." in candidate:
                    sku = candidate
        stop_words = {
            "precio",
            "precios",
            "stock",
            "inventario",
            "buscar",
            "busca",
            "producto",
            "productos",
            "codigo",
            "barcode",
            "sku",
            "ref",
            "referencia",
            "de",
            "del",
            "la",
            "el",
            "los",
            "las",
            "que",
            "quiero",
            "dame",
            "mostrar",
            "muestrame",
            "tienes",
            "hay",
            "por",
            "favor",
        }
        cleaned_text_tokens = [tok for tok in self._tokenize(normalized) if tok not in stop_words]
        cleaned_text_tokens = self._normalize_search_tokens(cleaned_text_tokens)
        cleaned_text = " ".join(cleaned_text_tokens)[:80] if cleaned_text_tokens else normalized[:80]

        return {
            "barcode": barcode,
            "sku": sku,
            "texto": cleaned_text if cleaned_text else None,
        }

    async def _resolve_deterministic_text(
        self, text: str, user_id: int | None = None, chat_id: int | None = None
    ) -> tuple[str, bytes | None] | None:
        raw = (text or "").strip()
        if not raw:
            return None
        lowered = self._normalize_text(raw)

        if lowered in ("/ayuda", "ayuda", "help"):
            return self._deterministic_help_text(), None

        cmd_match = re.match(r"^/(precio|stock|buscar)\s*(.*)$", raw, flags=re.IGNORECASE)
        if cmd_match:
            intent = cmd_match.group(1).lower()
            query = cmd_match.group(2).strip()
            return await self._resolve_product_query(intent, query, user_id=user_id, chat_id=chat_id)

        if self._heuristic_route(raw):
            return None

        intent = None
        if any(k in lowered for k in ("precio", "cuanto", "cuanto vale", "valor", "costo")):
            intent = "precio"
        elif any(k in lowered for k in ("stock", "inventario", "hay disponible", "hay en")):
            intent = "stock"
        elif any(k in lowered for k in ("buscar", "producto", "referencia", "sku", "codigo", "barcode")):
            intent = "buscar"

        if not intent:
            return None
        return await self._resolve_product_query(intent, raw, user_id=user_id, chat_id=chat_id)

    async def _resolve_product_query(
        self, intent: str, query: str, user_id: int | None = None, chat_id: int | None = None
    ) -> tuple[str, bytes | None]:
        clean_query = (query or "").strip()
        if not clean_query:
            if intent == "buscar":
                return "Uso: /buscar <nombre|sku|codigo>", None
            return f"Uso: /{intent} <sku|codigo>", None

        entities = self._extract_query_entities(clean_query)
        payload = await self._search_products_resolved(
            texto=entities.get("texto"),
            sku=entities.get("sku"),
            barcode=entities.get("barcode"),
            limit=max(self.fuzzy_top_k * 4, 20),
        )
        tool_error_msg = self._humanize_tool_error(payload)
        if tool_error_msg:
            return tool_error_msg, None

        if isinstance(payload, dict) and int(payload.get("count", 0)) == 0:
            if user_id is not None and chat_id is not None:
                self._clear_pending_options(user_id, chat_id)
            return (
                "No encontre coincidencias. Prueba con SKU/codigo exacto o ajusta el nombre del producto.",
                None,
            )

        if intent == "precio":
            detail = self._format_single_product_detail(payload, "detalle_producto")
            if detail:
                if user_id is not None and chat_id is not None:
                    self._clear_pending_options(user_id, chat_id)
                return detail, None
        if intent == "stock":
            detail = self._format_single_product_detail(payload, "detalle_producto")
            if detail:
                if user_id is not None and chat_id is not None:
                    self._clear_pending_options(user_id, chat_id)
                return detail, None

        options = self._format_product_options(payload, "buscar_producto")
        if options:
            if user_id is not None and chat_id is not None:
                self._set_pending_options(user_id, chat_id, payload)
            return options, None
        detail = self._format_single_product_detail(payload, "detalle_producto")
        if detail:
            if user_id is not None and chat_id is not None:
                self._clear_pending_options(user_id, chat_id)
            return detail, None
        return "No pude resolver la consulta. Intenta con /buscar <texto>.", None

    async def _search_products_resolved(
        self,
        *,
        texto: str | None = None,
        sku: str | None = None,
        barcode: str | None = None,
        categoria: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        payload = await self._call_mcp_tool(
            "search_products",
            {
                "texto": texto,
                "sku": sku,
                "barcode": barcode,
                "categoria": categoria,
                "limit": min(max(limit, 1), 50),
            },
        )
        if not isinstance(payload, dict) or payload.get("error"):
            return payload
        if int(payload.get("count", 0)) <= 1:
            if int(payload.get("count", 0)) == 0 and texto and not (sku or barcode):
                variant_payload = await self._search_products_with_variants(
                    texto=texto,
                    categoria=categoria,
                    limit=limit,
                )
                if isinstance(variant_payload, dict) and int(variant_payload.get("count", 0)) > 0:
                    payload = variant_payload
            return payload
        if sku or barcode or not texto:
            return payload
        return self._rerank_payload_by_fuzzy(payload, texto)

    def _build_text_variants(self, texto: str) -> list[str]:
        base = self._normalize_text(texto)
        if not base:
            return []
        variants = [base]
        tokens = [tok for tok in self._normalize_search_tokens(self._tokenize(base)) if len(tok) >= 3]
        if not tokens:
            return variants

        def add(v: str) -> None:
            normalized = self._normalize_text(v)
            if normalized and normalized not in variants:
                variants.append(normalized)

        for idx, tok in enumerate(tokens):
            candidates = []
            if tok.endswith("es") and len(tok) > 4:
                candidates.append(tok[:-2])
            if tok.endswith("s") and len(tok) > 3:
                candidates.append(tok[:-1])
            if not tok.endswith("s"):
                candidates.append(tok + "s")
            # Common Spanish typo/phonetic variants.
            candidates.append(tok.replace("z", "s"))
            candidates.append(tok.replace("s", "z"))
            candidates.append(tok.replace("v", "b"))
            candidates.append(tok.replace("b", "v"))
            if "k" in tok:
                candidates.append(tok.replace("k", "c"))
            if "c" in tok:
                candidates.append(tok.replace("c", "k"))
            for cand in candidates:
                new_tokens = list(tokens)
                new_tokens[idx] = cand
                add(" ".join(new_tokens))
        return variants

    async def _search_products_with_variants(self, *, texto: str, categoria: str | None, limit: int) -> dict[str, Any]:
        variants = self._build_text_variants(texto)
        best: dict[str, Any] | None = None
        best_count = 0
        for variant in variants[:6]:
            payload = await self._call_mcp_tool(
                "search_products",
                {
                    "texto": variant,
                    "categoria": categoria,
                    "limit": min(max(limit, 1), 50),
                },
            )
            if not isinstance(payload, dict) or payload.get("error"):
                continue
            count = int(payload.get("count", 0))
            if count > best_count:
                best = payload
                best_count = count
            if count == 1:
                return payload
        return best or {"count": 0, "items": []}

    def _score_product_match(self, query: str, item: dict[str, Any]) -> float:
        name = self._normalize_text(str(item.get("product_name") or item.get("name") or ""))
        sku = self._normalize_text(str(item.get("sku") or item.get("reference") or item.get("code") or ""))
        barcode = self._normalize_text(str(item.get("barcode") or item.get("code") or ""))
        category = self._normalize_text(str(item.get("category_name") or item.get("category") or ""))
        q = self._normalize_text(query)
        if not q:
            return 0.0

        score = 0.0
        q_tokens = set(self._tokenize(q))
        fields = f"{name} {category}".strip()
        field_tokens = set(self._tokenize(fields))

        if q == sku or q == barcode:
            score += 1.0
        if sku.startswith(q) or barcode.startswith(q):
            score += 0.4
        if q in name:
            score += 0.35
        if q in fields:
            score += 0.2
        if q_tokens and field_tokens:
            overlap = len(q_tokens & field_tokens) / max(len(q_tokens), 1)
            score += 0.45 * overlap
            # Typo tolerance for terms like "korez" vs "kores"
            fuzzy_hits = 0.0
            for qtok in q_tokens:
                best = 0.0
                for ftok in field_tokens:
                    if abs(len(qtok) - len(ftok)) > 2:
                        continue
                    ratio = SequenceMatcher(None, qtok, ftok).ratio()
                    if ratio > best:
                        best = ratio
                if best >= 0.72:
                    fuzzy_hits += best
            if q_tokens:
                score += 0.35 * min(fuzzy_hits / len(q_tokens), 1.0)
        if len(q) >= 4 and name:
            commons = sum(1 for a, b in zip(q, name) if a == b)
            score += min(commons / max(len(q), 1), 1.0) * 0.15
        return min(score, 1.0)

    def _rerank_payload_by_fuzzy(self, payload: dict[str, Any], query: str) -> dict[str, Any]:
        items = payload.get("items") or []
        if not isinstance(items, list):
            return payload
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            scored.append((self._score_product_match(query, item), item))
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored:
            best_score, best_item = scored[0]
            second_score = scored[1][0] if len(scored) > 1 else 0.0
            if best_score >= self.fuzzy_strong_threshold and (best_score - second_score) >= 0.12:
                return {"count": 1, "items": [best_item]}
        candidates = [item for score, item in scored if score >= self.fuzzy_candidate_threshold]
        if not candidates and scored:
            candidates = [item for _, item in scored[: max(self.fuzzy_top_k, 10)]]
        pool = candidates[: max(self.fuzzy_pool_size, self.fuzzy_top_k)]
        return {"count": len(pool), "items": pool}

    def _deterministic_help_text(self) -> str:
        return (
            "Comandos disponibles:\n"
            "/precio <sku|codigo>\n"
            "/stock <sku|codigo>\n"
            "/buscar <texto>\n"
            "/ayuda"
        )

    async def _try_forced_product_lookup(self, text: str) -> dict[str, Any] | None:
        lowered = self._normalize_text(text)
        intent_markers = (
            "sku",
            "codigo",
            "barcode",
            "referencia",
            "ref",
            "precio",
            "valor",
            "cuanto",
            "stock",
            "inventario",
            "producto",
        )
        has_long_digits = bool(re.search(r"\d{6,14}", lowered))
        if not (any(m in lowered for m in intent_markers) or has_long_digits):
            return None
        entities = self._extract_query_entities(text)
        payload = await self._search_products_resolved(
            texto=entities.get("texto"),
            sku=entities.get("sku"),
            barcode=entities.get("barcode"),
            limit=max(self.fuzzy_top_k * 4, 20),
        )
        if isinstance(payload, dict) and not payload.get("error") and int(payload.get("count", 0)) > 0:
            return payload
        return None

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

        query_text = ""
        if barcode:
            query_text = f"/buscar {barcode}"
        elif sku_candidates:
            query_text = f"/buscar {str(sku_candidates[0]).strip()}"
        elif ocr_text:
            query_text = f"/buscar {ocr_text[:80]}"

        if not query_text:
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
        resolved = await self._resolve_deterministic_text(query_text, user_id=user_id, chat_id=chat_id)
        if resolved is None:
            text = caption or "Buscar producto por imagen"
            answer = "No pude resolver automaticamente la imagen. Usa /buscar <texto>."
            self._store_conversation_turn(user_id, chat_id, chat_type, text, answer)
            return answer
        answer, _ = resolved
        self._store_conversation_turn(user_id, chat_id, chat_type, caption or "[foto]", answer)
        await self._maybe_update_summary(user_id, caption or "buscar por imagen", answer)
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
        prefs = self.memory.get_prefs(user_id)
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

        transcription_text = transcription.strip()
        candidates = self._build_audio_routing_candidates(transcription_text)
        answer = ""
        selected_candidate = ""
        for routed_text in candidates:
            candidate_answer, _ = await self.process_text_with_media(
                user_id=user_id,
                chat_id=chat_id,
                chat_type=chat_type,
                text=routed_text,
            )
            answer = candidate_answer
            selected_candidate = routed_text
            if not self._looks_like_no_match_answer(candidate_answer):
                break
        debug = self._is_pref_true(
            prefs,
            "debug_mode",
            "debug_audio",
            "debug_audio_routing",
            "debug_routing",
        )
        if debug:
            return (
                "DEBUG audio:\n"
                f"- transcripcion: {transcription_text}\n"
                f"- candidatos: {', '.join(candidates)}\n"
                f"- seleccionado: {selected_candidate}\n\n"
                f"{answer}"
            )
        echo = os.getenv("AUDIO_ECHO_TRANSCRIPTION", "false").lower() == "true"
        if not echo:
            return answer
        return f"Transcripcion: {transcription_text}\n\n{answer}"

    def _is_pref_true(self, prefs: dict[str, str], *keys: str) -> bool:
        truthy = {"1", "true", "yes", "si", "on", "enabled", "debug"}
        for key in keys:
            value = str(prefs.get(key, "")).strip().lower()
            if value in truthy:
                return True
        return False

    def _looks_like_no_match_answer(self, answer: str) -> bool:
        normalized = self._normalize_text(answer)
        return any(
            marker in normalized
            for marker in (
                "no encontre coincidencias",
                "no encuentro coincidencias",
                "no encontre ese producto",
                "no encuentro ese producto",
                "no se encontraron resultados",
            )
        )

    def _build_audio_routing_candidates(self, transcription: str) -> list[str]:
        normalized = self._normalize_text(transcription)
        tokens = self._tokenize(normalized)

        price_markers = {
            "precio",
            "precios",
            "vale",
            "valor",
            "costo",
            "cuanto",
            "cuanto vale",
            "cuesta",
        }
        stock_markers = {"stock", "inventario", "existencia", "disponible", "hay"}
        search_markers = {
            "buscar",
            "busca",
            "muestrame",
            "mostrar",
            "dame",
            "listar",
            "lista",
            "tienes",
            "producto",
            "productos",
        }
        strip_words = {
            "de",
            "del",
            "la",
            "el",
            "los",
            "las",
            "por",
            "favor",
            "me",
            "podrias",
            "podria",
            "quiero",
            "necesito",
            "un",
            "una",
            "unos",
            "unas",
        } | price_markers | stock_markers | search_markers

        has_price = any(m in normalized for m in price_markers)
        has_stock = any(m in normalized for m in stock_markers)
        has_search = any(m in normalized for m in search_markers)

        cleaned_tokens = [tok for tok in tokens if tok not in strip_words]
        cleaned_query = " ".join(cleaned_tokens).strip()

        candidates: list[str] = []

        def add_candidate(text: str) -> None:
            if text and text not in candidates:
                candidates.append(text)

        if has_price and cleaned_query:
            add_candidate(f"/precio {cleaned_query}")
            # Price questions often start broad; let /buscar provide selectable options.
            add_candidate(f"/buscar {cleaned_query}")
        if has_stock and cleaned_query:
            add_candidate(f"/stock {cleaned_query}")
            add_candidate(f"/buscar {cleaned_query}")
        if has_search and cleaned_query:
            add_candidate(f"/buscar {cleaned_query}")

        has_barcode = bool(re.search(r"\b\d{8,14}\b", normalized))
        has_sku_like = bool(re.search(r"\b[a-z0-9]+[-_.][a-z0-9._-]+\b", normalized))
        looks_like_product_query = has_barcode or has_sku_like or len(cleaned_tokens) >= 2
        if looks_like_product_query and cleaned_query:
            add_candidate(f"/buscar {cleaned_query}")

        add_candidate(transcription)
        return candidates

    async def _maybe_query_tools(self, decision: RouterDecision) -> dict[str, Any]:
        if not decision.needs_db:
            return {"db": "not_required"}

        filters = decision.filters.model_dump(exclude_none=True)
        if decision.intent in ("buscar_producto", "detalle_producto"):
            return await self._search_products_resolved(
                texto=filters.get("texto"),
                sku=filters.get("sku"),
                barcode=filters.get("barcode"),
                categoria=filters.get("categoria"),
                limit=10,
            )
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

    def _pretty_category(self, value: Any) -> str:
        raw = str(value or "-").strip()
        lowered = raw.lower()
        if lowered in {"-", "none", "null", "000", "category standard"}:
            return "Sin categoria"
        return raw

    def _format_product_options(self, payload: dict[str, Any], intent: str) -> str | None:
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

        def fmt_price(value: Any) -> str:
            if value is None:
                return "-"
            try:
                amount = float(value)
            except (TypeError, ValueError):
                return str(value)
            if amount.is_integer():
                return f"${int(amount):,}"
            return f"${amount:,.2f}"

        def fmt_stock(value: Any) -> str:
            if value is None:
                return "-"
            try:
                qty = float(value)
            except (TypeError, ValueError):
                return str(value)
            if qty.is_integer():
                return str(int(qty))
            return f"{qty:.2f}"

        lines = [f"Coincidencias ({len(items)}):"]
        for idx, item in enumerate(items, start=1):
            name = str(item.get("product_name") or item.get("name") or "-")
            price_sell = item.get("pricesell")
            if price_sell is None:
                price_sell = item.get("price")
            lines.append(f"{idx}. {name}")
            lines.append(f"   Precio: {fmt_price(price_sell)}")
        lines.append("Toca un boton para ver el detalle completo.")
        return "\n".join(lines)

    def _format_single_product_detail(self, payload: dict[str, Any], intent: str) -> str | None:
        if not isinstance(payload, dict):
            return None
        if int(payload.get("count", 0)) != 1:
            return None
        items = payload.get("items") or []
        if not isinstance(items, list) or not items:
            return None

        item = items[0]

        def fmt_price(value: Any) -> str:
            if value is None:
                return "-"
            try:
                amount = float(value)
            except (TypeError, ValueError):
                return str(value)
            if amount.is_integer():
                return f"${int(amount):,}"
            return f"${amount:,.2f}"

        def fmt_stock(value: Any) -> str:
            if value is None:
                return "-"
            try:
                qty = float(value)
            except (TypeError, ValueError):
                return str(value)
            if qty.is_integer():
                return str(int(qty))
            return f"{qty:.2f}"

        name = str(item.get("product_name") or item.get("name") or "-")
        ref = str(item.get("sku") or item.get("reference") or item.get("code") or "-")
        code = str(item.get("barcode") or item.get("code") or "-")
        category = self._pretty_category(item.get("category_name") or item.get("categoria") or item.get("category"))
        price_buy = item.get("pricebuy")
        price_sell = item.get("pricesell")
        if price_sell is None:
            price_sell = item.get("price")
        stock = item.get("stock")
        if stock is None:
            stock = item.get("stockunits")

        margin_txt = "-"
        if price_buy is not None and price_sell is not None:
            try:
                margin = float(price_sell) - float(price_buy)
                margin_txt = fmt_price(margin)
            except (TypeError, ValueError):
                margin_txt = "-"

        lines = [
            f"Producto: {name}",
            f"Ref: {ref} | Codigo: {code}",
            f"Categoria: {category}",
            f"Precio compra distribuidor: {fmt_price(price_buy)}",
            f"Precio venta publico: {fmt_price(price_sell)}",
            f"Margen estimado: {margin_txt}",
            f"Stock: {fmt_stock(stock)}",
        ]
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
