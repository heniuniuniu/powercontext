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

"""Behavior tests for the HTTP Memory reranker."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.artifacts.memory.models import MemoryHit


def _hit(rank: int) -> MemoryHit:
    return MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="m", revision=1),
        entry_id=f"e{rank}",
        entry_version_id=f"v{rank}",
        text=f"candidate {rank}",
        score=1.0 / rank,
        matched_by=("fts",),
    )


def test_batches_keep_original_ranks_and_order_globally() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        scores = {1: 0.2, 2: 0.9, 3: 0.9}
        return httpx.Response(
            200,
            json={"scores": [{"rank": item["rank"], "score": scores[item["rank"]]} for item in body["candidates"]]},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=2)
            decision = await reranker.rerank("query", tuple(_hit(rank) for rank in range(1, 4)), 2)

        assert decision.selected_ranks == (2, 3)
        assert decision.usage.requests == 2
        assert len(seen) == 2
        assert sorted(item["rank"] for body in seen for item in body["candidates"]) == [1, 2, 3]
        for body in seen:
            assert body["query"] == "query"
            assert 1 <= len(body["candidates"]) <= 2
            for item in body["candidates"]:
                assert item["text"] == _hit(item["rank"]).text

    asyncio.run(scenario())


def test_accepts_out_of_order_scores() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"scores": [{"rank": 2, "score": 0.1}, {"rank": 1, "score": 0.8}]})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=2)
            decision = await reranker.rerank("query", (_hit(1), _hit(2)), 1)

        assert decision.selected_ranks == (1,)
        assert decision.usage.requests == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {"scores": [], "extra": True},
        {"scores": [{"rank": 1, "score": 0.5, "extra": True}]},
        {"scores": [{"rank": 1, "score": 0.5}, {"rank": 1, "score": 0.4}]},
        {"scores": [{"rank": 2, "score": 0.5}]},
        {"scores": [{"rank": 1, "score": float("nan")}]},
        {"scores": [{"rank": 1, "score": float("inf")}]},
        {"scores": [{"rank": 1, "score": "0.5"}]},
    ],
)
def test_rejects_invalid_response_schemas(payload: dict[str, Any]) -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=json.dumps(payload).encode()))
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_rejects_invalid_json_response() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"not json"))
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_preserves_http_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client)
            with pytest.raises(httpx.ConnectError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_preserves_http_status_errors() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(500, json={"scores": []}))
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client)
            with pytest.raises(httpx.HTTPStatusError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_validates_bounds_before_any_request() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(200))) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (), 1)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 2)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 0)
            with pytest.raises(ValueError):
                HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=0)

    asyncio.run(scenario())
