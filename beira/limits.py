"""Límites que protegen el VPS y los tokens de quien pide."""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from typing import Callable

from mcp import types

TRUNCATED_NOTE = "\n\n[Beira: respuesta recortada por superar el tamaño máximo]"


class RateLimiter:
    """Ventana deslizante de 60 s por contacto, en memoria."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic):
        self.per_minute = per_minute
        self.clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = self.clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= 60:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return False
        hits.append(now)
        return True


def _size(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False, default=str).encode())


def cap_result(result: types.CallToolResult, max_bytes: int) -> tuple[types.CallToolResult, bool]:
    """Recorta el resultado a `max_bytes`. Devuelve (resultado, se_recortó)."""
    budget = max_bytes
    truncated = False
    content: list = []
    for block in result.content:
        if isinstance(block, types.TextContent):
            raw = block.text.encode()
            if len(raw) > budget:
                text = raw[:budget].decode(errors="ignore")
                block = types.TextContent(type="text", text=text)
                truncated = True
                budget = 0
            else:
                budget -= len(raw)
            content.append(block)
        else:
            size = _size(block.model_dump(mode="json"))
            if size > budget:
                truncated = True  # imágenes, audio…: enteros o nada
                continue
            budget -= size
            content.append(block)

    structured = result.structuredContent
    if structured is not None and (truncated or _size(structured) > max_bytes):
        structured = None
        truncated = True

    if not truncated:
        return result, False
    content.append(types.TextContent(type="text", text=TRUNCATED_NOTE.strip()))
    return types.CallToolResult(content=content, structuredContent=structured, isError=result.isError), True
