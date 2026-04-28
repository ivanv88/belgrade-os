from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TextChunk:
    content: str


@dataclass
class ToolUse:
    call_id: str
    name: str
    input: dict


@dataclass
class StreamDone:
    stop_reason: str
    tool_calls: list[ToolUse] = field(default_factory=list)


class InferenceProvider(ABC):
    @abstractmethod
    async def generate(
        self,
        messages: list,
        tools: Optional[list] = None,
    ) -> AsyncIterator:
        """Yield TextChunk for each text delta, then a single StreamDone."""
        yield  # pragma: no cover
