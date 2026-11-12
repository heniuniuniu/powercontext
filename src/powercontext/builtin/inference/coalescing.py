# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Share embedding work between concurrent callers and reuse finished vectors.

Every :class:`CoalescingEmbeddingModel` call deduplicates its own texts, joins a
short merge window with other calls waiting at the same time, and hands the union
to the wrapped model in batches of at most ``max_batch_size`` distinct texts. A
call therefore never pays twice for a text another call is already embedding, and
a text that finishes successfully is served from an LRU cache until evicted.

The wrapper is a pure :class:`~powercontext.builtin.inference.protocols.EmbeddingModel`:
it exposes the wrapped profile and reports, per call, only the batches that call
waited on. Cancelling one caller leaves the shared batch running for the others,
and a failed batch is reported to its waiters without being cached.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
    from powercontext.builtin.inference.protocols import EmbeddingModel

DEFAULT_MAX_BATCH_SIZE = 64
DEFAULT_COALESCE_MS = 5.0
DEFAULT_CACHE_SIZE = 1024

_Delivery = tuple[EmbeddingVector, object, InferenceUsage]


@dataclass(frozen=True, slots=True)
class _Batch:
    """One underlying provider call and the identity its waiters share."""

    texts: tuple[str, ...]
    token: object


class CoalescingEmbeddingModel:
    """Coalesce, bound, and cache embedding work for one wrapped model."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        coalesce_ms: float = DEFAULT_COALESCE_MS,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        _require_count("max_batch_size", max_batch_size, minimum=1)
        _require_count("cache_size", cache_size, minimum=0)
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, int | float) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative number")  # noqa: TRY003
        self.profile: EmbeddingProfile = model.profile
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, EmbeddingVector] | None = OrderedDict() if cache_size > 0 else None
        self._waiting: dict[str, asyncio.Future[_Delivery]] = {}
        self._queue: list[str] = []
        self._flush_handle: asyncio.TimerHandle | None = None
        self._running: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one vector per input position, sharing or reusing work where possible."""

        if not texts:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        resolved: dict[str, EmbeddingVector] = {}
        waited: list[tuple[str, asyncio.Future[_Delivery]]] = []
        for text in dict.fromkeys(texts):
            cached = self._hit(text)
            if cached is not None:
                resolved[text] = cached
                continue
            waited.append((text, self._join(text)))
        tokens: dict[object, InferenceUsage] = {}
        if waited:
            deliveries = await asyncio.gather(*(asyncio.shield(future) for _, future in waited))
            for (text, _), (vector, token, usage) in zip(waited, deliveries, strict=True):
                resolved[text] = vector
                tokens[token] = usage
        return EmbeddingResult(
            vectors=tuple(resolved[text] for text in texts),
            usage=_total_usage(tokens.values()),
        )

    def _join(self, text: str) -> asyncio.Future[_Delivery]:
        """Register ``text`` for this wave, or attach to the wave already carrying it."""

        future = self._waiting.get(text)
        if future is not None:
            return future
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(_retrieve)
        self._waiting[text] = future
        self._queue.append(text)
        self._schedule_flush()
        return future

    def _schedule_flush(self) -> None:
        if self._flush_handle is None:
            self._flush_handle = asyncio.get_running_loop().call_later(self._coalesce_seconds, self._flush)

    def _flush(self) -> None:
        self._flush_handle = None
        queued, self._queue = self._queue, []
        for start in range(0, len(queued), self._max_batch_size):
            batch = _Batch(texts=tuple(queued[start : start + self._max_batch_size]), token=object())
            task = asyncio.get_running_loop().create_task(self._run(batch))
            self._running.add(task)
            task.add_done_callback(self._running.discard)

    async def _run(self, batch: _Batch) -> None:
        try:
            result = await self._model.embed(batch.texts)
        except Exception as error:  # every waiter must see the provider failure
            self._fail(batch, error)
            return
        if len(result.vectors) != len(batch.texts):
            self._fail(batch, ValueError("embedding provider returned the wrong number of vectors"))
            return
        for text, vector in zip(batch.texts, result.vectors, strict=True):
            self._store(text, vector)
            future = self._waiting.pop(text, None)
            if future is not None and not future.done():
                future.set_result((vector, batch.token, result.usage))

    def _fail(self, batch: _Batch, error: Exception) -> None:
        for text in batch.texts:
            future = self._waiting.pop(text, None)
            if future is not None and not future.done():
                future.set_exception(error)

    def _hit(self, text: str) -> EmbeddingVector | None:
        if self._cache is None:
            return None
        vector = self._cache.get(text)
        if vector is not None:
            self._cache.move_to_end(text)
        return vector

    def _store(self, text: str, vector: EmbeddingVector) -> None:
        if self._cache is None:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)


def _retrieve(future: asyncio.Future[_Delivery]) -> None:
    """Mark a shared failure as observed so an abandoned waiter raises no warning."""

    if not future.cancelled():
        future.exception()


def _total_usage(usages: Iterable[InferenceUsage]) -> InferenceUsage:
    requests = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    for usage in usages:
        requests += usage.requests
        if usage.input_tokens is not None:
            input_tokens = (input_tokens or 0) + usage.input_tokens
        if usage.output_tokens is not None:
            output_tokens = (output_tokens or 0) + usage.output_tokens
    return InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens)


def _require_count(name: str, value: object, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")  # noqa: TRY003


__all__ = ["DEFAULT_CACHE_SIZE", "DEFAULT_COALESCE_MS", "DEFAULT_MAX_BATCH_SIZE", "CoalescingEmbeddingModel"]
