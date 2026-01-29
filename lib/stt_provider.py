"""
Stub for stt provider objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Protocol


@dataclass(frozen=True, init=True)
class TranscriptEvent:
    text: str
    is_final: bool  # "committed" in your terminology


class RealtimeSttProvider(Protocol):
    async def __aenter__(self) -> "RealtimeSttProvider": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    async def send_audio(self, pcm_chunk: bytes) -> None: ...
    async def end_audio(self) -> None: ...

    def events(self) -> AsyncIterator[TranscriptEvent]: ...
