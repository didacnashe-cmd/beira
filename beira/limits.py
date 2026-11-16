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


class FailureGuard:
    """Bloquea una clave (una IP) que acumula `limit` fallos en `window` s.

    El bloqueo dura `block_for` s desde el fallo que lo dispara. Todo en memoria.
    """

    MAX_KEYS = 10_000  # por encima, se barren las claves sin fallos recientes

    def __init__(
        self,
        limit: int,
        window: float = 60.0,
        block_for: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limit = limit
        self.window = window
        self.block_for = block_for
        self.clock = clock
        self._fails: dict[str, deque[float]] = {}
        self._until: dict[str, float] = {}

    def blocked(self, key: str) -> float:
        """Segundos de bloqueo que le quedan a `key` (0 si no está bloqueada)."""
        until = self._until.get(key)
        if until is None:
            return 0.0
        left = until - self.clock()
        if left <= 0:
            del self._until[key]
            return 0.0
        return left

    def fail(self, key: str) -> None:
        """Anota un fallo de `key` y la bloquea si llega al límite."""
        if self.limit <= 0:
            return
        now = self.clock()
        fails = self._fails.setdefault(key, deque())
        while fails and now - fails[0] >= self.window:
            fails.popleft()
        fails.append(now)
        if len(fails) >= self.limit:
            self._until[key] = now + self.block_for
            fails.clear()
        if len(self._fails) > self.MAX_KEYS:
            self._sweep(now)

    def _sweep(self, now: float) -> None:
        for key in [k for k, f in self._fails.items() if not f or now - f[-1] >= self.window]:
            del self._fails[key]
        for key in [k for k, u in self._until.items() if u <= now]:
            del self._until[key]


class Cooldown:
    """Recuerda un fallo durante `seconds` s (p. ej. «el MCP local está caído»)."""

    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        self.seconds = seconds
        self.clock = clock
        self._until = 0.0
        self.reason = ""

    def trip(self, reason: str) -> None:
        if self.seconds > 0:
            self._until = self.clock() + self.seconds
            self.reason = reason

    def active(self) -> bool:
        return self.clock() < self._until

    def reset(self) -> None:
        self._until = 0.0
        self.reason = ""


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
