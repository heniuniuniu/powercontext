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

"""Withdraw and restore derived artifacts from a Source-versioned dependency graph.

Every node is registered once per Scope with the exact reference it was derived
from. Sources become available or unavailable through versioned events; Memory
entries and Topic artifacts follow their parents under an ``all`` or ``any``
policy. Withdrawal never rewrites the original text: Memory entries only change
state, and an unsupported Topic is hidden from the search projection while its
exact Artifact revision stays readable.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NoReturn, cast

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.topic_memory import TopicMemorySearchResult
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_EDGES_TABLE, SOURCE_LIFECYCLE_NODES_TABLE
from powercontext.builtin.sources.journal import validate_scope_id
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

NodeFamily = Literal["source", "memory", "topic"]
DependencyPolicy = Literal["all", "any"]
LifecycleReference = SourceRef | MemoryCitation | ArtifactRef
LifecyclePurpose = Literal["generation", "audit"]

MAX_NODE_ID_LENGTH = 256
_LIFECYCLE_REASON = "source-lifecycle"
_NODES = SOURCE_LIFECYCLE_NODES_TABLE
_EDGES = SOURCE_LIFECYCLE_EDGES_TABLE


class SourceLifecycleError(ValueError):
    """A registration, event, or resolution that violates the graph contract."""


class SourceLifecycleEvent(BaseModel):
    """One validated availability change for a registered Source."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    revision: int
    sequence: int
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def require_visible_text(cls, value: object) -> object:
        if not isinstance(value, str) or not value.strip():
            _fail("event-text")
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def require_positive_version(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            _fail("event-version")
        return value


@dataclass(frozen=True, slots=True)
class _Node:
    """One registered node with its immutable support and current state."""

    node_id: str
    family: NodeFamily
    policy: DependencyPolicy | None
    available: bool
    native_available: bool
    source_revision: int
    event_sequence: int
    event_reason: str | None
    reference: LifecycleReference
    parents: tuple[str, ...]


class SourceLifecycleApplication:
    """Select the lifecycle service for one Scope and filter Topic search results."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase) -> None:
        self._runtime = runtime
        self._database = database

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycleService:
        return ScopedSourceLifecycleService(self._runtime, self._database, scope_id)

    async def filter_topic_search(self, scope_id: str, result: TopicMemorySearchResult, /) -> TopicMemorySearchResult:
        """Drop hits whose Topic artifact is currently unsupported by its evidence."""

        suppressed = await self._suppressed_topics(validate_scope_id(scope_id))
        if not suppressed:
            return result
        hits = tuple(
            hit for hit in result.hits if (hit.artifact_ref.artifact_id, hit.artifact_ref.revision) not in suppressed
        )
        if len(hits) == len(result.hits):
            return result
        return result.model_copy(update={"hits": hits})

    async def _suppressed_topics(self, scope_id: str) -> frozenset[tuple[str, int]]:
        async with self._database.transaction() as connection:
            rows = (
                await connection.execute(
                    select(_NODES.c.topic_artifact_id, _NODES.c.topic_revision).where(
                        _NODES.c.scope_id == scope_id,
                        _NODES.c.family == "topic",
                        _NODES.c.native_available.is_(False),
                    )
                )
            ).all()
        return frozenset((str(artifact_id), int(revision)) for artifact_id, revision in rows if artifact_id is not None)


class ScopedSourceLifecycleService:
    """Register one Scope's dependency graph and apply its Source events."""

    def __init__(self, runtime: BuiltinRuntime, database: AsyncDatabase, scope_id: str) -> None:
        self._runtime = runtime
        self._database = database
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(self, node_id: str, reference: SourceRef, *, revision: int) -> None:
        """Register one readable Source at an exact lifecycle revision."""

        node = _require_node_id(node_id)
        if not isinstance(reference, SourceRef):
            _fail("source-reference")
        _require_positive("revision", revision)
        await self._runtime.records.for_scope(self.scope_id).get_source(reference.source_type, reference.source_id)
        await self._register(node, "source", reference, (), None, revision=revision)

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: tuple[str, ...],
        policy: DependencyPolicy,
    ) -> None:
        """Register one readable Memory entry derived from earlier nodes."""

        node = _require_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            _fail("memory-reference")
        graph_parents = _require_parents(node, parents)
        graph_policy = _require_policy(policy)
        await self._verify_citation(citation)
        await self._register(node, "memory", citation, graph_parents, graph_policy, revision=0)

    async def register_topic(
        self,
        node_id: str,
        reference: ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: DependencyPolicy,
    ) -> None:
        """Register one readable Topic artifact derived from earlier nodes."""

        node = _require_node_id(node_id)
        if not isinstance(reference, ArtifactRef):
            _fail("topic-reference")
        graph_parents = _require_parents(node, parents)
        graph_policy = _require_policy(policy)
        await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
            reference.family, reference.artifact_id, reference.revision
        )
        await self._register(node, "topic", reference, graph_parents, graph_policy, revision=0)

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        """Apply one Source event and return every available node in identifier order."""

        async with self._database.transaction() as connection:
            nodes = await self._load(connection)
            node = nodes.get(event.node_id)
            if node is None or node.family != "source":
                _fail("unknown-source")
            if event.revision != node.source_revision:
                _fail("revision-mismatch")
            replay = event.sequence == node.event_sequence
            if event.sequence < node.event_sequence:
                _fail("stale-sequence")
            if replay and (node.available != event.available or node.event_reason != event.reason):
                _fail("sequence-conflict")
            if not replay:
                await self._record_event(connection, nodes, event)
        await self._reconcile()
        nodes = await self._load()
        return sorted(node_id for node_id, item in nodes.items() if item.available)

    async def resolve(self, node_id: str, /, *, purpose: LifecyclePurpose) -> LifecycleReference:
        """Return a node's original reference for generation or audit."""

        node_id = _require_node_id(node_id)
        if purpose not in {"generation", "audit"}:
            _fail("purpose")
        nodes = await self._load()
        node = nodes.get(node_id)
        if node is None:
            _fail("unknown-node")
        if purpose == "generation" and not node.available:
            _fail("unavailable")
        return node.reference

    async def _register(
        self,
        node_id: str,
        family: NodeFamily,
        reference: LifecycleReference,
        parents: tuple[str, ...],
        policy: DependencyPolicy | None,
        *,
        revision: int,
    ) -> None:
        async with self._database.transaction() as connection:
            nodes = await self._load(connection)
            existing = nodes.get(node_id)
            if existing is not None:
                if not _same_binding(existing, family, reference, parents, policy, revision):
                    _fail("node-conflict")
                return
            missing = next((parent for parent in parents if parent not in nodes), None)
            if missing is not None:
                _fail("missing-parent")
            available = family == "source" or _evaluate(parents, policy, nodes)
            values = _reference_values(family, reference)
            await connection.execute(
                _NODES.insert().values(
                    scope_id=self.scope_id,
                    node_id=node_id,
                    family=family,
                    policy=policy,
                    available=available,
                    native_available=True,
                    source_revision=revision,
                    event_sequence=0,
                    event_reason=None,
                    **values,
                )
            )
            for ordinal, parent in enumerate(parents):
                await connection.execute(
                    _EDGES.insert().values(
                        scope_id=self.scope_id,
                        node_id=node_id,
                        ordinal=ordinal,
                        parent_id=parent,
                    )
                )
        await self._reconcile(node_id)

    async def _record_event(
        self,
        connection: AsyncConnection,
        nodes: dict[str, _Node],
        event: SourceLifecycleEvent,
    ) -> None:
        active = _propagate(nodes, event.node_id, event.available)
        for node_id, node in nodes.items():
            values: dict[str, object] = {}
            if active[node_id] != node.available:
                values["available"] = active[node_id]
            if node_id == event.node_id:
                values["event_sequence"] = event.sequence
                values["event_reason"] = event.reason
            if values:
                await connection.execute(self._node_update(node_id).values(**values))

    async def _reconcile(self, only: str | None = None) -> None:
        """Make each node's native projection match its logical availability."""

        nodes = await self._load()
        for node_id, node in nodes.items():
            if only is not None and node_id != only:
                continue
            if node.family == "memory":
                await self._apply_memory_state(node, node.available)
            if node.native_available != node.available:
                async with self._database.transaction() as connection:
                    await connection.execute(self._node_update(node_id).values(native_available=node.available))

    async def _apply_memory_state(self, node: _Node, available: bool) -> None:
        citation = node.reference
        if not isinstance(citation, MemoryCitation):
            _fail("memory-reference")
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            service = context.artifacts.memory
            try:
                head = await service.head(context.artifacts.memory_artifact_id)
            except ArtifactNotFoundError:
                _fail("missing-memory")
            item = next((entry for entry in head.content.manifest.entries if entry.entry_id == citation.entry_id), None)
            if item is None:
                _fail("missing-entry")
            if item.entry_version_id != citation.entry_version_id:
                _fail("entry-version")
            if (item.state == "active") == available:
                return
            version = next(
                (entry for entry in await service.entries(head) if entry.entry_id == citation.entry_id), None
            )
            if version is None:
                _fail("missing-entry")
            if available:
                await service.reactivate(head, entries=(version,), reason=_LIFECYCLE_REASON)
            else:
                await service.forget(head, entries=(version,), reason=_LIFECYCLE_REASON)

    async def _verify_citation(self, citation: MemoryCitation) -> None:
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            try:
                memory = await service.revision(citation.memory_ref)
            except ArtifactNotFoundError:
                _fail("missing-memory")
            if not any(
                entry.entry_id == citation.entry_id and entry.entry_version_id == citation.entry_version_id
                for entry in memory.content.manifest.entries
            ):
                _fail("missing-entry")
            await service.validate_citation(citation)

    async def _load(self, connection: AsyncConnection | None = None) -> dict[str, _Node]:
        if connection is None:
            async with self._database.transaction() as owned:
                return await self._load(owned)
        rows = (await connection.execute(select(_NODES).where(_NODES.c.scope_id == self.scope_id))).mappings().all()
        parents: dict[str, list[str]] = {}
        edges = await connection.execute(
            select(_EDGES.c.node_id, _EDGES.c.parent_id)
            .where(_EDGES.c.scope_id == self.scope_id)
            .order_by(_EDGES.c.node_id, _EDGES.c.ordinal)
        )
        for node_id, parent_id in edges:
            parents.setdefault(str(node_id), []).append(str(parent_id))
        return {str(row["node_id"]): _node(row, tuple(parents.get(str(row["node_id"]), ()))) for row in rows}

    def _node_update(self, node_id: str):
        return update(_NODES).where(_NODES.c.scope_id == self.scope_id, _NODES.c.node_id == node_id)


def _node(row: Mapping[str, object], parents: tuple[str, ...]) -> _Node:
    return _Node(
        node_id=str(row["node_id"]),
        family=cast(NodeFamily, row["family"]),
        policy=cast("DependencyPolicy | None", row["policy"]),
        available=bool(row["available"]),
        native_available=bool(row["native_available"]),
        source_revision=int(cast(int, row["source_revision"])),
        event_sequence=int(cast(int, row["event_sequence"])),
        event_reason=cast("str | None", row["event_reason"]),
        reference=_reference(row),
        parents=parents,
    )


def _reference(row: Mapping[str, object]) -> LifecycleReference:
    if row["family"] == "source":
        return SourceRef(source_type=str(row["source_type"]), source_id=str(row["source_id"]))
    if row["family"] == "memory":
        return MemoryCitation(
            memory_ref=ArtifactRef(
                family=str(row["memory_family"]),
                artifact_id=str(row["memory_artifact_id"]),
                revision=int(cast(int, row["memory_revision"])),
            ),
            entry_id=str(row["entry_id"]),
            entry_version_id=str(row["entry_version_id"]),
        )
    return ArtifactRef(
        family=str(row["topic_family"]),
        artifact_id=str(row["topic_artifact_id"]),
        revision=int(cast(int, row["topic_revision"])),
    )


def _reference_values(family: NodeFamily, reference: LifecycleReference) -> dict[str, object]:
    if family == "source":
        if not isinstance(reference, SourceRef):
            _fail("source-reference")
        return {"source_type": reference.source_type, "source_id": reference.source_id}
    if family == "memory":
        if not isinstance(reference, MemoryCitation):
            _fail("memory-reference")
        return {
            "memory_family": reference.memory_ref.family,
            "memory_artifact_id": reference.memory_ref.artifact_id,
            "memory_revision": reference.memory_ref.revision,
            "entry_id": reference.entry_id,
            "entry_version_id": reference.entry_version_id,
        }
    if not isinstance(reference, ArtifactRef):
        _fail("topic-reference")
    return {
        "topic_family": reference.family,
        "topic_artifact_id": reference.artifact_id,
        "topic_revision": reference.revision,
    }


def _same_binding(
    node: _Node,
    family: NodeFamily,
    reference: LifecycleReference,
    parents: tuple[str, ...],
    policy: DependencyPolicy | None,
    revision: int,
) -> bool:
    return (
        node.family == family
        and node.reference == reference
        and node.parents == parents
        and node.policy == policy
        and node.source_revision == revision
    )


def _propagate(nodes: dict[str, _Node], node_id: str, available: bool) -> dict[str, bool]:
    overrides = {node_id: available}
    active = {
        key: (overrides.get(key, node.available) if node.family == "source" else False) for key, node in nodes.items()
    }
    for _ in range(len(nodes) + 1):
        changed = False
        for key, node in nodes.items():
            if node.family == "source":
                continue
            value = _evaluate(node.parents, node.policy, nodes, active)
            if active[key] != value:
                active[key] = value
                changed = True
        if not changed:
            break
    return active


def _evaluate(
    parents: tuple[str, ...],
    policy: DependencyPolicy | None,
    nodes: dict[str, _Node],
    active: dict[str, bool] | None = None,
) -> bool:
    values = [_available(parent, nodes, active) for parent in parents]
    return all(values) if policy == "all" else any(values)


def _available(parent: str, nodes: dict[str, _Node], active: dict[str, bool] | None) -> bool:
    if active is not None:
        return active.get(parent, False)
    node = nodes.get(parent)
    return False if node is None else node.available


def _require_node_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_NODE_ID_LENGTH:
        _fail("node-id")
    return value


def _require_parents(node_id: str, parents: object) -> tuple[str, ...]:
    if not isinstance(parents, (tuple, list)) or not parents:
        _fail("parents")
    for parent in parents:
        if not isinstance(parent, str) or not parent.strip():
            _fail("parent-id")
        if parent == node_id:
            _fail("self-reference")
    if len(set(parents)) != len(parents):
        _fail("duplicate-parent")
    return tuple(parents)


def _require_policy(value: object) -> DependencyPolicy:
    if value not in {"all", "any"}:
        _fail("policy")
    return cast(DependencyPolicy, value)


def _require_positive(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(f"{name}-positive")
    return value


def _fail(code: str) -> NoReturn:
    raise SourceLifecycleError(code)


__all__ = ["ScopedSourceLifecycleService", "SourceLifecycleApplication", "SourceLifecycleError", "SourceLifecycleEvent"]
