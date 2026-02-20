import asyncio
from typing import Protocol


class AudioLLM(Protocol):
    async def atranscribe_audio(self, audio_bytes: bytes, mime_type: str, hint_text: str | None = None) -> str: ...


class AudioTranscriberService:
    def __init__(self, llm: AudioLLM):
        self.llm = llm

    async def transcribe(self, audio_bytes: bytes, mime_type: str, hint_text: str | None = None) -> str:
        # Audio transcription can take longer than text inference.
        return await asyncio.wait_for(
            self.llm.atranscribe_audio(audio_bytes, mime_type, hint_text),
            timeout=35,
        )
