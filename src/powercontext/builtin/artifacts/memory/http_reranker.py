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

"""Rerank coarse Memory search candidates with a remote HTTP scoring service."""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision, _validate_bounds
from powercontext.builtin.inference import InferenceUsage

HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"
HTTP_RERANK_BATCH_SIZE = 32


class _HTTPResponseModel(BaseModel):
    """Strict envelope for one scoring service response."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _HTTPScoreRecord(_HTTPResponseModel):
    """One scored candidate reported by the scoring service."""

    rank: int = Field(ge=1)
    score: float = Field(allow_inf_nan=False)


class _HTTPScoreResponse(_HTTPResponseModel):
    """The exact response envelope of the scoring service."""

    scores: list[_HTTPScoreRecord]


class HTTPMemoryReranker:
    """Select Memory hits with an HTTP scoring service.

    The candidate pool is split into batches of at most ``batch_size`` entries. Every batch
    keeps the one-based rank the candidate has in the complete pool, so a service score always
    maps back to the original candidate and the service never sees renumbered ranks. All batches
    then compete in one global selection: descending score, ties broken by the original rank.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = HTTP_RERANK_BATCH_SIZE,
        policy_id: str = HTTP_RERANK_POLICY_ID,
    ) -> None:
        if batch_size < 1:
            raise ValueError("http rerank batch size must be positive")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Return the original ranks of the ``limit`` best-scoring candidates."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            ranks = tuple(range(start + 1, start + 1 + len(batch)))
            scores.update(await self._score_batch(query, batch, ranks))
            requests += 1
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _ in ordered[:limit]),
            usage=InferenceUsage(requests=requests),
        )

    async def _score_batch(
        self,
        query: str,
        batch: tuple[MemoryHit, ...],
        ranks: tuple[int, ...],
    ) -> dict[int, float]:
        """Score one batch and return its scores keyed by the original rank."""

        response = await self._client.post(
            self._endpoint,
            json={
                "query": query,
                "candidates": [
                    {"rank": rank, "text": candidate.text} for rank, candidate in zip(ranks, batch, strict=True)
                ],
            },
        )
        response.raise_for_status()
        return _parse_scores(response, ranks)


def _parse_scores(response: httpx.Response, ranks: tuple[int, ...]) -> dict[int, float]:
    """Validate the service envelope and return the scores of one batch."""

    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError("http rerank response is not valid JSON") from error  # noqa: TRY003
    try:
        scores = _HTTPScoreResponse.model_validate(payload)
    except ValidationError as error:
        raise ValueError(f"http rerank response does not match the score schema: {error}") from error  # noqa: TRY003
    expected = set(ranks)
    parsed: dict[int, float] = {}
    for record in scores.scores:
        if record.rank not in expected:
            raise ValueError(  # noqa: TRY003
                f"http rerank response scored rank {record.rank} outside the requested batch"
            )
        if record.rank in parsed:
            raise ValueError(f"http rerank response scored rank {record.rank} more than once")  # noqa: TRY003
        parsed[record.rank] = record.score
    if parsed.keys() != expected:
        missing = sorted(expected - parsed.keys())
        raise ValueError(f"http rerank response omitted requested ranks: {missing}")  # noqa: TRY003
    return parsed


__all__ = ["HTTP_RERANK_BATCH_SIZE", "HTTP_RERANK_POLICY_ID", "HTTPMemoryReranker"]
