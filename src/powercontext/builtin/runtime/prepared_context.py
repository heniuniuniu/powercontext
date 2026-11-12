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

"""Prepare final, bounded context from approved Artifact evidence."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import chain, pairwise
from typing import TypeVar

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceSearchHit, render_experience
from powercontext.builtin.artifacts.memory.models import MemoryCitation, MemoryHit
from powercontext.builtin.artifacts.profile.models import Profile
from powercontext.builtin.artifacts.topic_memory import TopicMemory, TopicMemorySearchHit
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_code import (
    CodeEvidenceRef,
    PreparedCodeCandidate,
    assemble_code,
)
from powercontext.builtin.runtime.prepared_text import (
    TRUST_POLICY,
    ContextTextItem,
    fit_context_text_item,
    render_context_text,
)
from powercontext.builtin.runtime.recall_sufficiency import RecallBudgetView

_MIN_TRUNCATED_CONTENT_BYTES = 64
_ELLIPSIS = "…"
_BEGIN_MARKER = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END_MARKER = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"
_Item = TypeVar("_Item")
_TOKEN_PATTERN = re.compile(r"[a-z]+|\d+")
_SENTENCE_TERMINATORS = frozenset(".!?\u3002\uff01\uff1f")
_SENTENCE_CLOSERS = frozenset("\"'\u201d\u2019)]}")
_MAX_UNIT_CHARS = 400
_UNIT_TARGET_CHARS = 320
_MAX_MERGE_GAP_CHARS = 24


@dataclass(frozen=True)
class _PreparedContextEntry:
    origin: PreparedContextOrigin
    kind: str
    citation: dict[str, object]
    content: str
    truncated: bool


@dataclass(frozen=True)
class _EntryFit:
    """One fit attempt on the non-assembly path: the entry that fitted, or why none did.

    The two drop reasons are disjoint and mirror the assembly path's
    :class:`~powercontext.builtin.runtime.prepared_text.FitOutcome`. An entry is dropped
    *below the minimum truncated content* when a shorter rendering exists and fits but is too
    short to be an honest delivery; it is dropped *with no fitting truncation* when no
    shortened rendering fits at all.
    """

    entry: _PreparedContextEntry | None = None
    dropped_below_min_bytes: bool = False
    dropped_no_fitting_truncation: bool = False


@dataclass(frozen=True)
class PreparedContextOmissions:
    """Aggregate counts of the items the byte budget omitted in one build.

    Every count is a per-call aggregate, never per-entry attribution and never a verdict about
    an entry: an entry losing to the budget is not a negative result about that entry.

    ``dropped_items`` is the sum of its two sub-counts, so "the budget could not fit this
    item" can be told apart from "this item was too short to truncate into the remaining
    space". A single merged counter would misreport the second cause as the first.
    """

    truncated_items: int = 0
    dropped_items: int = 0
    dropped_below_min_bytes: int = 0
    dropped_no_fitting_truncation: int = 0


@dataclass(frozen=True)
class PreparedContextBuild:
    """Final public context and the exact origins selected to produce it.

    ``omissions`` is always filled by the Builder. The RFC 1560 recall trace is deliberately
    **not** a field here: ``ScopedContextApplication._prepare`` returns ``build.context`` and
    discards the rest, so such a field would have no production observer. The trace is
    delivered through the Runtime's optional ``RecallEffortSink`` instead.
    """

    context: PreparedContext
    origins: tuple[PreparedContextOrigin, ...]
    omissions: PreparedContextOmissions = PreparedContextOmissions()
    code_origins: tuple[CodeEvidenceRef, ...] = ()
    fragments: tuple[PreparedFragment, ...] = ()


@dataclass(frozen=True)
class MemoryEntryAddress:
    """Identify one exact Memory entry version across Scope boundaries."""

    memory: ArtifactAddress
    entry_id: str
    entry_version_id: str


PreparedContextOrigin = MemoryCitation | ArtifactRef | MemoryEntryAddress | ArtifactAddress


@dataclass(frozen=True)
class PreparedFragment:
    """One literal slice of a Memory entry selected for the current query.

    ``text`` is always ``entry.text[start:end]``: the selection never rewrites, translates, or
    summarizes evidence, so a caller can re-read the cited entry version and reconcile the
    exact character interval.
    """

    address: MemoryEntryAddress
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _FragmentHit:
    """One deduplicated Memory entry the fragment selector may slice."""

    address: MemoryEntryAddress
    text: str
    order: int


@dataclass(frozen=True)
class _FragmentUnit:
    """One candidate character interval of one entry and its query relevance."""

    hit: _FragmentHit
    start: int
    end: int
    score: float = 0.0


@dataclass(frozen=True)
class PreparedMemoryCandidates:
    """Memory candidates read from one Scope."""

    scope_id: str
    memory_ref: ArtifactRef | None = None
    hits: tuple[MemoryHit, ...] = ()


@dataclass(frozen=True)
class PreparedExperienceCandidates:
    """Experience candidates read from one Scope."""

    scope_id: str
    hits: tuple[ExperienceSearchHit, ...] = ()


@dataclass(frozen=True)
class PreparedProfileCandidate:
    """The latest committed Profile snapshot read from one Scope."""

    scope_id: str
    profile: Profile


class PreparedContextBuilder:
    """Select and render final context without I/O, persistence, or reranking."""

    memory_candidate_limit = 16
    topic_memory_candidate_limit = 8
    experience_candidate_limit = 8
    candidate_limit = memory_candidate_limit
    entry_limit = 8
    topic_memory_entry_limit = 8
    experience_entry_limit = 2
    max_entry_content_bytes = 2000
    _text_entry_limit: int | None = None

    def empty(self) -> PreparedContext:
        return PreparedContext(status="empty", content=None, content_bytes=0)

    def build(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContext:
        return self.build_result(
            request=request,
            scope_id=scope_id,
            memory_ref=memory_ref,
            hits=hits,
            topic_memory_hits=topic_memory_hits,
            experience_hits=experience_hits,
        ).context

    def build_result(
        self,
        *,
        request: PrepareContextRequest,
        scope_id: str | None = None,
        memory_ref: ArtifactRef | None = None,
        hits: Sequence[MemoryHit] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_hits: Sequence[ExperienceSearchHit] = (),
    ) -> PreparedContextBuild:
        return self.build_scopes_result(
            request=request,
            current_scope_id=scope_id,
            memory_candidates=(
                PreparedMemoryCandidates(scope_id=scope_id or "", memory_ref=memory_ref, hits=tuple(hits)),
            ),
            experience_candidates=(PreparedExperienceCandidates(scope_id=scope_id or "", hits=tuple(experience_hits)),),
            topic_memory_hits=topic_memory_hits,
        )

    def build_scopes_result(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
        code_candidates: Sequence[PreparedCodeCandidate] = (),
    ) -> PreparedContextBuild:
        if request.evidence_selection:
            return self._build_fragment_context(request, memory_candidates)

        if request.include_code:

            def historical(history_request: PrepareContextRequest, entries: int) -> PreparedContextBuild:
                builder = PreparedContextBuilder()
                builder.entry_limit = entries
                builder._text_entry_limit = entries if entries < self.entry_limit else None
                return builder.build_scopes_result(
                    request=history_request.model_copy(update={"include_code": False}),
                    current_scope_id=current_scope_id,
                    memory_candidates=memory_candidates,
                    topic_memory_hits=topic_memory_hits,
                    experience_candidates=experience_candidates,
                    profile_candidates=profile_candidates,
                )

            return assemble_code(request, code_candidates, self.entry_limit, historical)

        if sum(len(candidates.hits) for candidates in memory_candidates) > self.memory_candidate_limit:
            raise PreparedContextInvariantError("memory-candidate-limit")
        if len(topic_memory_hits) > self.topic_memory_candidate_limit:
            raise PreparedContextInvariantError("topic-memory-candidate-limit")
        if sum(len(candidates.hits) for candidates in experience_candidates) > self.experience_candidate_limit:
            raise PreparedContextInvariantError("experience-candidate-limit")

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        if content is None:
            return PreparedContextBuild(context=self.empty(), origins=(), omissions=omissions)
        content_bytes = len(content.encode("utf-8"))
        # The assembly path is bounded by construction (every fitted item is measured against
        # `max_bytes`), so the ceiling check belongs to the non-assembly renderer only.
        if request.assembly is None and content_bytes > request.max_bytes:
            raise PreparedContextInvariantError("output-budget")
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=content_bytes),
            origins=origins,
            omissions=omissions,
        )

    def probe_budget(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates] = (),
        topic_memory_hits: Sequence[TopicMemorySearchHit] = (),
        experience_candidates: Sequence[PreparedExperienceCandidates] = (),
        profile_candidates: Sequence[PreparedProfileCandidate] = (),
    ) -> RecallBudgetView:
        """Report what the byte budget does to one candidate set, without delivering it.

        This is the RFC 1560 *budget probe*: one pass of this Builder's own pure selection and
        rendering code, keeping only the counters and discarding the rendered output. It lets
        the gate tell budget-limited thinness from recall-limited thinness.

        Pure and side-effect free: no I/O, no persistence, nothing reported to any sink, and
        the same inputs as :meth:`build_scopes_result` are guaranteed to yield the same
        counters because both go through :meth:`_select_entries`.
        """

        content, origins, omissions = self._select_entries(
            request=request,
            current_scope_id=current_scope_id,
            memory_candidates=memory_candidates,
            experience_candidates=experience_candidates,
            profile_candidates=profile_candidates,
            topic_memory_hits=topic_memory_hits,
        )
        content_bytes = 0 if content is None else len(content.encode("utf-8"))
        return RecallBudgetView(
            max_bytes=request.max_bytes,
            delivered_items=len(origins),
            truncated_items=omissions.truncated_items,
            dropped_items=omissions.dropped_items,
            unused_bytes=max(0, request.max_bytes - content_bytes),
        )

    def _select_entries(
        self,
        *,
        request: PrepareContextRequest,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        """Run one complete pure selection pass and render it.

        Returns ``(content, origins, omissions)``; ``content`` is ``None`` exactly when nothing
        was selected, which is the empty-result case for both paths. This is the single
        selection path shared by :meth:`build_scopes_result` and :meth:`probe_budget`, so the
        probe's counters always describe the selection the build would actually perform.
        """

        if request.evidence_selection:
            content, origins, _ = self._select_fragments(request, memory_candidates)
            return content, origins, PreparedContextOmissions()

        if request.assembly is not None:
            return self._select_text(
                request,
                current_scope_id=current_scope_id,
                memory_candidates=memory_candidates,
                experience_candidates=experience_candidates,
                profile_candidates=profile_candidates,
                topic_memory_hits=topic_memory_hits,
            )

        memory_entries = _interleave_groups(
            tuple(
                self._memory_entries(
                    candidates.memory_ref,
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in memory_candidates
            )
        )
        topic_memory_entries = self._topic_memory_entries(topic_memory_hits)
        experience_entries = _interleave_groups(
            tuple(
                self._experience_entries(
                    candidates.hits,
                    scope_id=None if candidates.scope_id == current_scope_id else candidates.scope_id or None,
                )
                for candidates in experience_candidates
            )
        )[: self.experience_entry_limit]
        entries, omissions = self._fit_entries(request, memory_entries, topic_memory_entries, experience_entries)
        if not entries:
            return None, (), omissions
        return _render(entries), tuple(entry.origin for entry in entries), omissions

    def _build_fragment_context(
        self,
        request: PrepareContextRequest,
        memory_candidates: Sequence[PreparedMemoryCandidates],
    ) -> PreparedContextBuild:
        if sum(len(candidates.hits) for candidates in memory_candidates) > self.memory_candidate_limit:
            raise PreparedContextInvariantError("memory-candidate-limit")
        content, origins, fragments = self._select_fragments(request, memory_candidates)
        if content is None:
            return PreparedContextBuild(context=self.empty(), origins=())
        return PreparedContextBuild(
            context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode("utf-8"))),
            origins=origins,
            fragments=fragments,
        )

    def _select_fragments(
        self,
        request: PrepareContextRequest,
        memory_candidates: Sequence[PreparedMemoryCandidates],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], tuple[PreparedFragment, ...]]:
        """Pick the literal Memory slices that answer ``request.query`` inside the byte budget.

        Candidate entries are split into sentence-sized units, ranked by an IDF-weighted lexical
        overlap with the query, and packed greedily until the rendered envelope would exceed
        ``max_bytes``. Adjacent selected units of one entry collapse into a single fragment so the
        per-item citation cost is paid once, which leaves more of the budget for evidence text.
        Every delivered interval is exactly ``entry.text[start:end]``.
        """

        hits = _fragment_hits(memory_candidates)
        if not hits:
            return None, (), ()
        units = tuple(unit for hit in hits for unit in _fragment_units(hit))
        ranked = sorted(
            _score_fragment_units(request.query, units),
            key=lambda unit: (-unit.score, unit.hit.order, unit.start),
        )
        chosen: list[_FragmentUnit] = []
        if ranked and ranked[0].score > 0:
            for unit in ranked:
                if unit.score <= 0:
                    continue
                if _fragment_units_fit((*chosen, unit), request.max_bytes):
                    chosen.append(unit)
            chosen.extend(_neighbour_units(chosen, units, request.max_bytes))
        else:
            for hit in hits:
                first = next(unit for unit in units if unit.hit.order == hit.order)
                if _fragment_units_fit((*chosen, first), request.max_bytes):
                    chosen.append(first)
        if not chosen:
            return None, (), ()
        fragments = _merge_fragment_units(chosen)
        content = _render_fragment_items(fragments)
        if len(content.encode("utf-8")) > request.max_bytes:
            raise PreparedContextInvariantError("output-budget")
        return content, tuple(fragment.address for fragment in fragments), fragments

    def _select_text(
        self,
        request: PrepareContextRequest,
        *,
        current_scope_id: str | None,
        memory_candidates: Sequence[PreparedMemoryCandidates],
        experience_candidates: Sequence[PreparedExperienceCandidates],
        profile_candidates: Sequence[PreparedProfileCandidate],
        topic_memory_hits: Sequence[TopicMemorySearchHit],
    ) -> tuple[str | None, tuple[PreparedContextOrigin, ...], PreparedContextOmissions]:
        assembly = request.assembly
        if assembly is None:
            raise PreparedContextInvariantError("text-assembly-missing")
        included: list[ContextTextItem] = []
        origins: list[PreparedContextOrigin] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for section in assembly.sections:
            # Unfilled earlier sections leave their capacity available to later ones.
            limit = (
                section.limit
                if self._text_entry_limit is None
                else min(section.limit, self._text_entry_limit - len(included))
            )
            if section.family == "profile":
                entries = self._profile_entries(profile_candidates)
            elif section.family == "topic-memory":
                entries = self._topic_memory_entries(topic_memory_hits, scope_id=current_scope_id)
            else:
                groups = (
                    tuple(
                        tuple(
                            self._memory_entries(group.memory_ref, (hit,), scope_id=group.scope_id)
                            for hit in group.hits
                        )
                        for group in memory_candidates
                    )
                    if section.family == "memory"
                    else tuple(
                        tuple(self._experience_entries((hit,), scope_id=group.scope_id) for hit in group.hits)
                        for group in experience_candidates
                    )
                )
                entries = tuple(chain.from_iterable(_interleave_groups(groups)))
            seen: set[tuple[str, str, str, int, str | None, str | None]] = set()
            rank = 0
            selected_count = 0
            for entry in entries:
                if selected_count >= limit:
                    break
                item = _text_item(entry)
                artifact = item.artifact
                identity = (
                    artifact.scope_id,
                    artifact.artifact.family,
                    artifact.artifact.artifact_id,
                    artifact.artifact.revision,
                    item.entry_id,
                    item.entry_version_id,
                )
                if identity in seen or not item.content.strip():
                    continue
                seen.add(identity)
                rank += 1
                outcome = fit_context_text_item(included, replace(item, recall_rank=rank), assembly, request.max_bytes)
                if outcome.item is None:
                    dropped_below_min_bytes += int(outcome.dropped_below_min_bytes)
                    dropped_no_fitting_truncation += int(outcome.dropped_no_fitting_truncation)
                    continue
                fitted = outcome.item
                included.append(fitted)
                origins.append(entry.origin)
                selected_count += 1
                truncated_items += int(fitted.truncated)
        omissions = PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )
        if not included:
            return None, (), omissions
        content = render_context_text(included, assembly)
        return content, tuple(origins), omissions

    def _profile_entries(self, candidates: Sequence[PreparedProfileCandidate]) -> tuple[_PreparedContextEntry, ...]:
        entries = []
        for candidate in candidates:
            origin = ArtifactAddress(scope_id=candidate.scope_id, artifact=candidate.profile.as_ref())
            entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="profile",
                    citation={"artifact": origin.model_dump(mode="json")},
                    content=candidate.profile.content.content,
                    truncated=False,
                )
            )
        return tuple(entries)

    def _memory_entries(
        self,
        memory_ref: ArtifactRef | None,
        hits: Sequence[MemoryHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        if hits and memory_ref is None:
            raise PreparedContextInvariantError("memory-ref-missing")
        memory_entries: list[_PreparedContextEntry] = []
        seen: set[tuple[str, str]] = set()
        for hit in hits:
            if hit.memory_ref != memory_ref:
                raise PreparedContextInvariantError("memory-ref-mismatch")

            citation_key = (hit.entry_id, hit.entry_version_id)
            if citation_key in seen:
                continue
            seen.add(citation_key)
            if not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                continue
            citation = MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
            origin: PreparedContextOrigin = citation
            rendered_citation = citation.model_dump(mode="json")
            if scope_id is not None:
                memory = ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref)
                origin = MemoryEntryAddress(
                    memory=memory,
                    entry_id=hit.entry_id,
                    entry_version_id=hit.entry_version_id,
                )
                rendered_citation = {
                    "memory": memory.model_dump(mode="json"),
                    "entry_id": hit.entry_id,
                    "entry_version_id": hit.entry_version_id,
                }
            memory_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="memory",
                    citation=rendered_citation,
                    content=hit.text,
                    truncated=False,
                )
            )
        return tuple(memory_entries)

    def _topic_memory_entries(
        self,
        hits: Sequence[TopicMemorySearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        topic_entries: list[_PreparedContextEntry] = []
        seen_topics: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != TopicMemory.family:
                raise PreparedContextInvariantError("topic-memory-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_topics:
                continue
            seen_topics.add(identity)
            if len(topic_entries) >= self.topic_memory_entry_limit:
                break
            content = {"title": hit.title, "summary": hit.summary}
            if hit.snippet is not None:
                content["snippet"] = hit.snippet
            origin: PreparedContextOrigin = hit.artifact_ref
            citation = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            body = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                citation = {"artifact": origin.model_dump(mode="json")}
                body = "\n\n".join(f"{label.capitalize()}: {value}" for label, value in content.items())
            topic_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="topic-memory",
                    citation=citation,
                    content=body,
                    truncated=False,
                )
            )
        return tuple(topic_entries)

    def _experience_entries(
        self,
        hits: Sequence[ExperienceSearchHit],
        *,
        scope_id: str | None = None,
    ) -> tuple[_PreparedContextEntry, ...]:
        experience_entries: list[_PreparedContextEntry] = []
        seen_experiences: set[tuple[str, int]] = set()
        for hit in hits:
            if hit.artifact_ref.family != Experience.family:
                raise PreparedContextInvariantError("experience-family-mismatch")
            identity = (hit.artifact_ref.artifact_id, hit.artifact_ref.revision)
            if identity in seen_experiences:
                continue
            seen_experiences.add(identity)
            origin: PreparedContextOrigin = hit.artifact_ref
            rendered_citation: dict[str, object] = {"artifact_ref": hit.artifact_ref.model_dump(mode="json")}
            if scope_id is not None:
                origin = ArtifactAddress(scope_id=scope_id, artifact=hit.artifact_ref)
                rendered_citation = {"artifact": origin.model_dump(mode="json")}
            experience_entries.append(
                _PreparedContextEntry(
                    origin=origin,
                    kind="experience",
                    citation=rendered_citation,
                    content=render_experience(hit.content),
                    truncated=False,
                )
            )
        return tuple(experience_entries)

    def _fit_entries(
        self,
        request: PrepareContextRequest,
        memory_entries: Sequence[_PreparedContextEntry],
        topic_memory_entries: Sequence[_PreparedContextEntry],
        experience_entries: Sequence[_PreparedContextEntry],
    ) -> tuple[tuple[_PreparedContextEntry, ...], PreparedContextOmissions]:
        entries: list[_PreparedContextEntry] = []
        truncated_items = 0
        dropped_below_min_bytes = 0
        dropped_no_fitting_truncation = 0
        for candidate in _interleave(memory_entries, topic_memory_entries, experience_entries):
            if len(entries) >= self.entry_limit:
                break
            fit = self._fit_entry(
                entries,
                origin=candidate.origin,
                kind=candidate.kind,
                citation=candidate.citation,
                text=candidate.content,
                max_bytes=request.max_bytes,
            )
            if fit.entry is None:
                dropped_below_min_bytes += int(fit.dropped_below_min_bytes)
                dropped_no_fitting_truncation += int(fit.dropped_no_fitting_truncation)
                continue
            entries.append(fit.entry)
            if fit.entry.truncated:
                truncated_items += 1
        return tuple(entries), PreparedContextOmissions(
            truncated_items=truncated_items,
            dropped_items=dropped_below_min_bytes + dropped_no_fitting_truncation,
            dropped_below_min_bytes=dropped_below_min_bytes,
            dropped_no_fitting_truncation=dropped_no_fitting_truncation,
        )

    def _fit_entry(
        self,
        entries: Sequence[_PreparedContextEntry],
        *,
        origin: PreparedContextOrigin,
        kind: str,
        citation: dict[str, object],
        text: str,
        max_bytes: int,
    ) -> _EntryFit:
        """Fit one entry into the remaining budget, reporting why it was dropped if it was.

        The two ``None`` paths of the original code are kept apart: ``below-min-bytes`` means a
        shorter rendering exists and fits but is too short to deliver honestly, while
        ``no-fitting-truncation`` means no shortened rendering fitted at all.
        """

        source_bytes = len(text.encode("utf-8"))
        entry_budget = min(source_bytes, self.max_entry_content_bytes)
        candidate = _PreparedContextEntry(
            origin=origin,
            kind=kind,
            citation=citation,
            content=text if source_bytes <= entry_budget else _truncate_utf8(text, entry_budget),
            truncated=source_bytes > entry_budget,
        )
        if _rendered_bytes((*entries, candidate)) <= max_bytes:
            return _EntryFit(entry=candidate)
        if source_bytes < _MIN_TRUNCATED_CONTENT_BYTES:
            return _EntryFit(dropped_below_min_bytes=True)

        lower = _MIN_TRUNCATED_CONTENT_BYTES
        upper = min(entry_budget, source_bytes - 1)
        best: _PreparedContextEntry | None = None
        while lower <= upper:
            byte_budget = (lower + upper) // 2
            candidate = _PreparedContextEntry(
                origin=origin,
                kind=kind,
                citation=citation,
                content=_truncate_utf8(text, byte_budget),
                truncated=True,
            )
            if len(candidate.content.encode("utf-8")) < _MIN_TRUNCATED_CONTENT_BYTES:
                lower = byte_budget + 1
                continue
            if _rendered_bytes((*entries, candidate)) <= max_bytes:
                best = candidate
                lower = byte_budget + 1
            else:
                upper = byte_budget - 1
        if best is None:
            return _EntryFit(dropped_no_fitting_truncation=True)
        return _EntryFit(entry=best)


def _interleave(
    memory: Sequence[_PreparedContextEntry],
    topics: Sequence[_PreparedContextEntry],
    experiences: Sequence[_PreparedContextEntry],
) -> tuple[_PreparedContextEntry, ...]:
    """Preserve each family's rank while alternating Memory, Topic, Experience."""

    ordered: list[_PreparedContextEntry] = []
    for index in range(max(len(memory), len(topics), len(experiences))):
        if index < len(memory):
            ordered.append(memory[index])
        if index < len(topics):
            ordered.append(topics[index])
        if index < len(experiences):
            ordered.append(experiences[index])
    return tuple(ordered)


def _interleave_groups(groups: Sequence[Sequence[_Item]]) -> tuple[_Item, ...]:
    ordered: list[_Item] = []
    for index in range(max((len(group) for group in groups), default=0)):
        ordered.extend(group[index] for group in groups if index < len(group))
    return tuple(ordered)


def _render(entries: Sequence[_PreparedContextEntry]) -> str:
    envelope = {
        "trust": "untrusted_history",
        "items": [_render_entry(entry) for entry in entries],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))


def _text_item(entry: _PreparedContextEntry) -> ContextTextItem:
    origin = entry.origin
    if isinstance(origin, MemoryEntryAddress):
        return ContextTextItem(
            artifact=origin.memory,
            content=entry.content,
            recall_rank=0,
            entry_id=origin.entry_id,
            entry_version_id=origin.entry_version_id,
        )
    if isinstance(origin, ArtifactAddress):
        return ContextTextItem(artifact=origin, content=entry.content, recall_rank=0)
    raise PreparedContextInvariantError("text-scope-missing")


def _render_entry(entry: _PreparedContextEntry) -> dict[str, object]:
    rendered: dict[str, object] = {
        "citation": entry.citation,
        "content": entry.content,
        "truncated": entry.truncated,
    }
    if entry.kind != "memory":
        rendered["kind"] = entry.kind
    return rendered


def _rendered_bytes(entries: Sequence[_PreparedContextEntry]) -> int:
    return len(_render(entries).encode("utf-8"))


def _truncate_utf8(text: str, byte_budget: int) -> str:
    prefix_budget = byte_budget - len(_ELLIPSIS.encode("utf-8"))
    encoded_prefix = text.encode("utf-8")[:prefix_budget]
    return f"{encoded_prefix.decode('utf-8', errors='ignore')}{_ELLIPSIS}"


def _fragment_hits(memory_candidates: Sequence[PreparedMemoryCandidates]) -> tuple[_FragmentHit, ...]:
    """Deduplicate the candidate hits that may contribute fragments.

    Identity is the exact entry version inside one scope, so equal text in two scopes never
    collapses into one fragment and never borrows another scope's address.
    """

    hits: list[_FragmentHit] = []
    seen: set[tuple[str, str, str, int, str, str]] = set()
    for candidates in memory_candidates:
        if candidates.hits and candidates.memory_ref is None:
            raise PreparedContextInvariantError("memory-ref-missing")
        for hit in candidates.hits:
            if hit.memory_ref != candidates.memory_ref:
                raise PreparedContextInvariantError("memory-ref-mismatch")
            identity = (
                candidates.scope_id,
                hit.memory_ref.family,
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            if identity in seen:
                continue
            seen.add(identity)
            if not hit.entry_id.strip() or not hit.entry_version_id.strip() or not hit.text.strip():
                continue
            hits.append(
                _FragmentHit(
                    address=MemoryEntryAddress(
                        memory=ArtifactAddress(scope_id=candidates.scope_id, artifact=hit.memory_ref),
                        entry_id=hit.entry_id,
                        entry_version_id=hit.entry_version_id,
                    ),
                    text=hit.text,
                    order=len(hits),
                )
            )
    return tuple(hits)


def _fragment_units(hit: _FragmentHit) -> tuple[_FragmentUnit, ...]:
    return tuple(_FragmentUnit(hit=hit, start=start, end=end) for start, end in _sentence_spans(hit.text))


def _sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Split text into non-overlapping, whitespace-trimmed sentence spans.

    Spans longer than ``_MAX_UNIT_CHARS`` are chunked at whitespace so one oversized sentence
    cannot monopolize a small byte budget; selected neighbours are merged back together before
    delivery, so chunking never changes the delivered characters.
    """

    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character in _SENTENCE_TERMINATORS:
            boundary = index + 1
            while boundary < length and text[boundary] in _SENTENCE_CLOSERS:
                boundary += 1
            if boundary == length or text[boundary].isspace():
                spans.append((start, boundary))
                start = boundary
                index = boundary
                continue
        elif character == "\n":
            spans.append((start, index))
            start = index + 1
        index += 1
    if start < length:
        spans.append((start, length))
    return _trim_spans(text, spans)


def _trim_spans(text: str, spans: Sequence[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    trimmed: list[tuple[int, int]] = []
    for span_start, span_end in spans:
        while span_start < span_end and text[span_start].isspace():
            span_start += 1
        while span_end > span_start and text[span_end - 1].isspace():
            span_end -= 1
        if span_end > span_start:
            trimmed.extend(_split_long_span(text, span_start, span_end))
    return tuple(trimmed)


def _split_long_span(text: str, start: int, end: int) -> tuple[tuple[int, int], ...]:
    if end - start <= _MAX_UNIT_CHARS:
        return ((start, end),)
    spans: list[tuple[int, int]] = []
    while end - start > _MAX_UNIT_CHARS:
        boundary = min(start + _UNIT_TARGET_CHARS, end)
        while boundary > start:
            if text[boundary - 1].isspace() or (boundary < end and text[boundary].isspace()):
                break
            boundary -= 1
        if boundary <= start:
            boundary = min(start + _UNIT_TARGET_CHARS, end)
        spans.append((start, boundary))
        start = boundary
        while start < end and text[start].isspace():
            start += 1
    if end > start:
        spans.append((start, end))
    return tuple(spans)


def _stem(token: str) -> str:
    """Fold the light English morphology that separates claims from article wording."""

    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 5 and token.endswith("ing"):
        token = token[:-3]
    elif len(token) > 4 and token.endswith("ed"):
        token = token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _token_stems(text: str) -> tuple[str, ...]:
    return tuple(_stem(token) for token in _TOKEN_PATTERN.findall(text.lower()))


def _score_fragment_units(query: str, units: Sequence[_FragmentUnit]) -> tuple[_FragmentUnit, ...]:
    """Rank units by IDF-weighted query overlap, with coverage and adjacency bonuses.

    Two structural priors break ties the text cannot: the opening sentence of an entry is often
    a heading or title label rather than evidence, so it is down-weighted, and later sentences
    are slightly favoured because a document usually states its finding after its setup.
    """

    query_stems = _token_stems(query)
    query_terms = tuple(dict.fromkeys(query_stems))
    if not query_terms or not units:
        return tuple(units)
    unit_tokens = [_token_stems(unit.hit.text[unit.start : unit.end]) for unit in units]
    document_frequency = dict.fromkeys(query_terms, 0)
    for tokens in unit_tokens:
        present = set(tokens)
        for term in query_terms:
            if term in present:
                document_frequency[term] += 1
    total = len(units)
    weights = {
        term: math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))
        for term, frequency in document_frequency.items()
    }
    average_length = sum(len(tokens) for tokens in unit_tokens) / total
    query_pairs = set(pairwise(query_stems))
    first_starts: dict[int, int] = {}
    for unit in units:
        first_starts[unit.hit.order] = min(first_starts.get(unit.hit.order, unit.start), unit.start)
    scored: list[_FragmentUnit] = []
    for unit, tokens in zip(units, unit_tokens, strict=True):
        counts = Counter(tokens)
        present = set(tokens)
        matched = [term for term in query_terms if term in present]
        if not matched:
            scored.append(unit)
            continue
        length_factor = 1.2 * (0.25 + 0.75 * len(tokens) / average_length)
        raw = sum(weights[term] * (counts[term] * 2.2) / (counts[term] + length_factor) for term in matched)
        coverage = len(matched) / len(query_terms)
        pairs = sum(1 for pair in query_pairs if pair in set(pairwise(tokens)))
        rank_prior = 1 + 0.15 / (1 + unit.hit.order)
        position_prior = 1 + 0.3 * unit.start / len(unit.hit.text)
        heading_prior = 0.25 if unit.start == first_starts[unit.hit.order] else 1.0
        score = (raw * (0.5 + 0.5 * coverage) + 0.5 * pairs) * rank_prior * position_prior * heading_prior
        scored.append(replace(unit, score=score))
    return tuple(scored)


def _neighbour_units(
    chosen: Sequence[_FragmentUnit],
    units: Sequence[_FragmentUnit],
    max_bytes: int,
) -> tuple[_FragmentUnit, ...]:
    """Offer the sentence before and after each selected unit while budget allows."""

    grouped: dict[int, list[_FragmentUnit]] = {}
    for unit in units:
        grouped.setdefault(unit.hit.order, []).append(unit)
    selected = {(unit.hit.order, unit.start) for unit in chosen}
    additions: list[_FragmentUnit] = []
    for unit in chosen:
        siblings = grouped[unit.hit.order]
        index = next(position for position, sibling in enumerate(siblings) if sibling.start == unit.start)
        for neighbour_index in (index - 1, index + 1):
            if not 0 <= neighbour_index < len(siblings):
                continue
            neighbour = siblings[neighbour_index]
            key = (neighbour.hit.order, neighbour.start)
            if key in selected:
                continue
            if _fragment_units_fit((*chosen, *additions, neighbour), max_bytes):
                additions.append(neighbour)
                selected.add(key)
    return tuple(additions)


def _merge_fragment_units(units: Sequence[_FragmentUnit]) -> tuple[PreparedFragment, ...]:
    """Collapse selected units into exact, document-ordered literal fragments."""

    grouped: dict[int, tuple[_FragmentHit, list[tuple[int, int]]]] = {}
    for unit in units:
        entry = grouped.setdefault(unit.hit.order, (unit.hit, []))
        entry[1].append((unit.start, unit.end))
    fragments: list[PreparedFragment] = []
    for order in sorted(grouped):
        hit, spans = grouped[order]
        merged: list[tuple[int, int]] = []
        for start, end in sorted(spans):
            if merged and start - merged[-1][1] <= _MAX_MERGE_GAP_CHARS and not hit.text[merged[-1][1] : start].strip():
                merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))
        fragments.extend(
            PreparedFragment(address=hit.address, start=start, end=end, text=hit.text[start:end])
            for start, end in merged
        )
    return tuple(fragments)


def _fragment_units_fit(units: Sequence[_FragmentUnit], max_bytes: int) -> bool:
    return len(_render_fragment_items(_merge_fragment_units(units)).encode("utf-8")) <= max_bytes


def _render_fragment_items(fragments: Sequence[PreparedFragment]) -> str:
    envelope = {
        "trust": "untrusted_history",
        "items": [
            {
                "citation": {
                    "scope_id": fragment.address.memory.scope_id,
                    "memory_ref": fragment.address.memory.artifact.model_dump(mode="json"),
                    "entry_id": fragment.address.entry_id,
                    "entry_version_id": fragment.address.entry_version_id,
                },
                "content": fragment.text,
                "start": fragment.start,
                "end": fragment.end,
            }
            for fragment in fragments
        ],
    }
    encoded = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return "\n\n".join((TRUST_POLICY, f"{_BEGIN_MARKER}\n{encoded}\n{_END_MARKER}"))
