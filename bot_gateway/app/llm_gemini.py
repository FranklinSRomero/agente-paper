import asyncio
import base64
import json
import logging
import os
import threading
import time
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import ValidationError

from .schemas import RouterDecision

logger = logging.getLogger(__name__)


class GeminiService:
    def __init__(self):
        self.model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self.api_keys = self._load_api_keys()
        self.clients = [genai.Client(api_key=k) for k in self.api_keys]
        self._next_client_idx = 0
        self._lock = threading.Lock()
        self.quota_cooldown_seconds = int(os.getenv("GEMINI_QUOTA_COOLDOWN_SECONDS", "300"))
        self._degraded_until = 0.0

    def _load_api_keys(self) -> list[str]:
        raw_multi = os.getenv("GEMINI_API_KEYS", "").strip()
        keys = []
        if raw_multi:
            normalized = raw_multi.replace("\n", ",").replace(";", ",")
            keys.extend([k.strip() for k in normalized.split(",") if k.strip()])
        single = os.getenv("GEMINI_API_KEY", "").strip()
        if single:
            keys.append(single)
        deduped = []
        seen = set()
        for key in keys:
            if key not in seen:
                seen.add(key)
                deduped.append(key)
        return deduped

    def _next_clients(self):
        if not self.clients:
            return []
        with self._lock:
            start = self._next_client_idx
            self._next_client_idx = (self._next_client_idx + 1) % len(self.clients)
        ordered = []
        for i in range(len(self.clients)):
            idx = (start + i) % len(self.clients)
            ordered.append((idx, self.clients[idx]))
        return ordered

    def _is_retryable_client_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return any(k in text for k in ("resource_exhausted", "quota", "429", "503", "unavailable", "rate"))

    def _run_with_failover(self, fn):
        last_exc = None
        for idx, client in self._next_clients():
            try:
                return fn(client)
            except genai_errors.ClientError as exc:
                last_exc = exc
                logger.warning("gemini_client_error_key_%s: %s", idx, exc)
                if not self._is_retryable_client_error(exc):
                    raise
                continue
        if last_exc:
            raise last_exc
        raise RuntimeError("no_gemini_clients_configured")

    def _set_degraded(self) -> None:
        self._degraded_until = time.monotonic() + max(self.quota_cooldown_seconds, 30)
        logger.warning("gemini_temporarily_degraded_for_seconds=%s", self.quota_cooldown_seconds)

    def is_temporarily_unavailable(self) -> bool:
        return time.monotonic() < self._degraded_until

    def route(self, user_text: str) -> RouterDecision:
        if self.is_temporarily_unavailable():
            return RouterDecision(
                intent="ayuda",
                needs_db=False,
                needs_vision=False,
                confidence=0.0,
                ask_clarification=(
                    "La IA esta temporalmente no disponible por cuota. "
                    "Usa /precio <sku>, /stock <sku> o /buscar <texto>."
                ),
            )
        if not self.clients:
            return RouterDecision(
                intent="ayuda",
                needs_db=False,
                needs_vision=False,
                confidence=0.2,
                ask_clarification="No tengo GEMINI_API_KEY/GEMINI_API_KEYS configurada. ¿Qué quieres consultar exactamente?",
            )

        prompt = (
            "Devuelve SOLO JSON valido con este schema. "
            "Si falta contexto, ask_clarification debe tener una sola pregunta concreta.\n"
            "Campos: intent, needs_db, needs_vision, filters, confidence, ask_clarification.\n"
            f"Mensaje usuario: {user_text}"
        )
        try:
            response = self._run_with_failover(
                lambda client: client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=RouterDecision,
                        temperature=0.1,
                    ),
                )
            )
            payload = json.loads((response.text or "{}").strip())
            return RouterDecision.model_validate(payload)
        except genai_errors.ClientError as exc:
            logger.warning("router_client_error: %s", exc)
            if self._is_quota_error(exc):
                self._set_degraded()
                return RouterDecision(
                    intent="ayuda",
                    needs_db=False,
                    needs_vision=False,
                    confidence=0.0,
                    ask_clarification=(
                        "Se agotó la cuota de Gemini en este momento. "
                        "Intenta más tarde o agrega API keys con cuota en GEMINI_API_KEYS."
                    ),
                )
            return RouterDecision(
                intent="otro",
                needs_db=False,
                needs_vision=False,
                confidence=0.1,
                ask_clarification="No pude consultar el modelo ahora. Intenta de nuevo en unos minutos.",
            )
        except (json.JSONDecodeError, ValidationError, Exception) as exc:
            logger.warning("router_validation_failed: %s", exc)
            return RouterDecision(
                intent="otro",
                needs_db=False,
                needs_vision=False,
                confidence=0.1,
                ask_clarification="¿Puedes especificar en una frase qué producto o dato necesitas?",
            )

    def respond(self, system_prompt: str, user_text: str, context: dict[str, Any]) -> str:
        if self.is_temporarily_unavailable():
            return "La IA esta temporalmente no disponible. Usa /precio, /stock o /buscar para consultas POS."
        if not self.clients:
            return "Configura GEMINI_API_KEY o GEMINI_API_KEYS para respuestas del modelo."

        prompt = (
            f"{system_prompt}\n\n"
            f"Contexto JSON confiable:\n{json.dumps(context, ensure_ascii=True)}\n\n"
            f"Solicitud usuario:\n{user_text}\n\n"
            "Responde en espanol, concreto y accionable."
        )
        try:
            response = self._run_with_failover(
                lambda client: client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.2),
                )
            )
            return (response.text or "No pude generar respuesta.").strip()
        except genai_errors.ClientError as exc:
            logger.warning("respond_client_error: %s", exc)
            if self._is_quota_error(exc):
                self._set_degraded()
                return (
                    "Se agotó la cuota de Gemini por ahora. "
                    "Intenta más tarde o agrega API keys/proyectos con cuota activa en GEMINI_API_KEYS."
                )
            return "No pude consultar Gemini en este momento. Intenta nuevamente en unos minutos."
        except Exception as exc:
            logger.warning("respond_error: %s", exc)
            return "No pude generar respuesta en este momento. Intenta nuevamente."

    def summarize(self, prior_summary: str, latest_user_text: str, latest_bot_text: str) -> str:
        if self.is_temporarily_unavailable():
            return prior_summary[:2000]
        if not self.clients:
            raw = f"{prior_summary} | U:{latest_user_text[:120]} | B:{latest_bot_text[:120]}"
            return raw[-1000:]
        prompt = (
            "Resume brevemente en maximo 5 lineas, neutral, sin datos sensibles "
            "(sin tokens, credenciales, PII).\n"
            f"Resumen previo: {prior_summary}\n"
            f"Ultimo usuario: {latest_user_text}\n"
            f"Ultima respuesta: {latest_bot_text}"
        )
        try:
            response = self._run_with_failover(
                lambda client: client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.1),
                )
            )
            return (response.text or prior_summary).strip()[:2000]
        except Exception as exc:
            logger.warning("summarize_error: %s", exc)
            return prior_summary[:2000]

    def _is_quota_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        return any(k in text for k in ("resource_exhausted", "quota", "429"))

    async def aroute(self, user_text: str) -> RouterDecision:
        return await asyncio.to_thread(self.route, user_text)

    async def arespond(self, system_prompt: str, user_text: str, context: dict[str, Any]) -> str:
        return await asyncio.to_thread(self.respond, system_prompt, user_text, context)

    async def asummarize(self, prior_summary: str, latest_user_text: str, latest_bot_text: str) -> str:
        return await asyncio.to_thread(self.summarize, prior_summary, latest_user_text, latest_bot_text)

    def transcribe_audio(self, audio_bytes: bytes, mime_type: str, hint_text: str | None = None) -> str:
        if self.is_temporarily_unavailable():
            return ""
        if not self.clients:
            return ""
        b64 = base64.b64encode(audio_bytes).decode("ascii")
        instruction = "Transcribe el audio a texto en espanol. Devuelve solo la transcripcion."
        if hint_text:
            instruction += f" Contexto breve: {hint_text[:120]}"
        contents = [
            {
                "role": "user",
                "parts": [
                    {"text": instruction},
                    {"inline_data": {"mime_type": mime_type or "audio/ogg", "data": b64}},
                ],
            }
        ]
        try:
            response = self._run_with_failover(
                lambda client: client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(temperature=0),
                )
            )
            return (response.text or "").strip()
        except Exception as exc:
            logger.warning("transcribe_audio_error: %s", exc)
            if self._is_quota_error(exc):
                self._set_degraded()
            return ""

    async def atranscribe_audio(self, audio_bytes: bytes, mime_type: str, hint_text: str | None = None) -> str:
        return await asyncio.to_thread(self.transcribe_audio, audio_bytes, mime_type, hint_text)
