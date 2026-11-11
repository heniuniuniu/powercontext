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

"""Contract tests for Pi session branch capture and checkpoint recovery."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from powercontext.sources.connectors import ConnectorBinding, ConnectorLifecycle
from powercontext.sources.definitions import SourceDefinitionRegistry
from powercontext.sources.models import SourceRef
from powercontext.sources.pi_session import (
    PI_EVENT_SOURCE_DEFINITION,
    PiEventInput,
    PiSessionConnector,
    PiSessionInput,
    parse_pi_session,
    pi_event_source_id,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

_HEADER = {"type": "session", "version": 3, "id": "session"}


def _archive(*records: dict) -> bytes:
    return "\n".join(json.dumps(record) for record in (_HEADER, *records)).encode()


def _parse(*records: dict, leaf: str | None = None) -> tuple[PiEventInput, ...]:
    return parse_pi_session(PiSessionInput(content=_archive(*records), leaf_id=leaf))


class _RecordingSink:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, object]] = []

    async def submit(self, binding, item_id, definition_name, value):
        self.items.append((item_id, definition_name, value))
        return SourceRef(source_type=definition_name, source_id=item_id)


class _MemoryCheckpoints:
    def __init__(self) -> None:
        self.value = None

    async def load(self, binding):
        return self.value

    async def save(self, binding, checkpoint, *, expected):
        assert expected == self.value
        self.value = checkpoint


def _run(connector: PiSessionConnector, sink: _RecordingSink, checkpoints: _MemoryCheckpoints):
    binding = ConnectorBinding(
        scope_id="scope",
        binding_id="pi",
        connector_name="pi-session-history",
        connector_version="1",
    )
    return asyncio.run(ConnectorLifecycle(sink=sink, checkpoints=checkpoints).run(connector, binding))


def test_control_nodes_keep_the_ancestor_chain():
    events = _parse(
        {"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "ask"}},
        {"type": "thinking_level_change", "id": "b", "parentId": "a"},
        {"type": "message", "id": "c", "parentId": "b", "message": {"role": "assistant", "content": "answer"}},
    )

    assert [event.event_id for event in events] == ["a", "c"]
    assert [event.parent_id for event in events] == [None, "b"]
    assert events[1].event_type == "message"


def test_tool_calls_keep_compact_sorted_unicode_arguments():
    events = _parse({
        "type": "message",
        "id": "a",
        "parentId": None,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "toolCall",
                    "name": "read",
                    "id": "call-1",
                    "arguments": {"z": 1, "a": "ä"},
                }
            ],
        },
    })

    assert events[0].content == 'assistant: [tool_call read call-1] {"a":"ä","z":1}'


def test_thinking_and_image_blocks_stay_out_of_the_text():
    events = _parse({
        "type": "message",
        "id": "a",
        "parentId": None,
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "private"},
                {"type": "image", "data": "ignored"},
                {"type": "text", "text": "visible"},
            ],
        },
    })

    assert events[0].content == "assistant: visible"


def test_summaries_and_custom_messages_keep_their_own_role():
    events = _parse(
        {"type": "compaction", "id": "a", "parentId": None, "summary": "kept"},
        {"type": "branch_summary", "id": "b", "parentId": "a", "summary": "branch"},
        {"type": "custom_message", "id": "c", "parentId": "b", "content": "custom"},
    )

    assert [event.content for event in events] == [
        "compaction: kept",
        "branch_summary: branch",
        "custom: custom",
    ]


def test_records_without_visible_bodies_are_skipped():
    events = _parse(
        {"type": "model_change", "id": "a", "parentId": None},
        {"type": "message", "id": "b", "parentId": "a", "message": {"role": "user", "content": []}},
        {"type": "message", "id": "c", "parentId": "b", "message": {"role": "user", "content": "kept"}},
    )

    assert [event.event_id for event in events] == ["c"]


def test_the_complete_input_record_is_retained():
    record = {"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "kept"}}
    events = _parse(record)

    assert events[0].record == record


def test_the_default_leaf_is_the_last_record():
    records = [
        {"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "first"}},
        {"type": "message", "id": "b", "parentId": "a", "message": {"role": "assistant", "content": "second"}},
    ]

    assert _parse(*records) == _parse(*records, leaf="b")


@pytest.mark.parametrize(
    "records,leaf",
    [
        ([{"type": "message", "id": "a", "parentId": "missing"}], "a"),
        ([{"type": "message", "id": "a", "parentId": "a"}], "a"),
        ([{"type": "message", "id": "a", "parentId": None}] * 2, "a"),
        ([{"type": "message", "id": "a", "parentId": None}], "absent"),
        ([{"type": "message", "id": "a"}], "a"),
        ([{"type": "message", "parentId": None}], "a"),
    ],
)
def test_invalid_graphs_raise_value_error(records, leaf):
    with pytest.raises(ValueError):
        _parse(*records, leaf=leaf)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not json\n",
        b"[]\n",
        b"null\n",
        b"\xff",
        b'{"type":"session","version":2,"id":"x"}\n',
        b'{"type":"session","version":3}\n',
        b'{"version":3,"id":"x"}\n',
    ],
)
def test_invalid_archives_raise_value_error(raw):
    with pytest.raises(ValueError):
        parse_pi_session(PiSessionInput(content=raw))


def test_source_ids_are_stable_bounded_and_unique():
    assert pi_event_source_id("session", "event") == "pi:session:event"
    assert pi_event_source_id("session:one", "event") == "pi:session%3Aone:event"
    assert pi_event_source_id("session", "event") == pi_event_source_id("session", "event")

    long_session = "s" * 400
    identity = pi_event_source_id(long_session, "event")

    assert len(identity) <= 256
    assert identity != pi_event_source_id(long_session, "other")


def test_the_definition_projects_one_complete_citation():
    registry = SourceDefinitionRegistry((PI_EVENT_SOURCE_DEFINITION,))
    events = _parse({"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "kept"}})

    source = asyncio.run(registry.resolve(events[0]))
    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))

    assert source.name == source.source_id == "pi:session:a"
    assert source.materialization.value == "captured"
    assert asyncio.run(registry.read(source)) == events[0]
    assert evidence.source_type == "pi-event"
    assert evidence.source_id == source.name
    assert evidence.content == "user: kept"
    assert evidence.metadata == {
        "session_id": "session",
        "event_id": "a",
        "parent_id": None,
        "event_type": "message",
        "role": "user",
    }


def _write_session(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record) for record in (_HEADER, *records)) + "\n",
        encoding="utf-8",
    )


def test_the_connector_imports_only_the_new_events_of_one_branch(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    records = [
        {"type": "message", "id": "root", "parentId": None, "message": {"role": "user", "content": "shared"}},
        {"type": "message", "id": "left", "parentId": "root", "message": {"role": "assistant", "content": "left"}},
        {"type": "model_change", "id": "control", "parentId": "root"},
        {
            "type": "message",
            "id": "right",
            "parentId": "control",
            "message": {"role": "assistant", "content": "right"},
        },
    ]
    _write_session(path, records)
    sink, checkpoints = _RecordingSink(), _MemoryCheckpoints()

    first = _run(PiSessionConnector(path, "left"), sink, checkpoints)
    second = _run(PiSessionConnector(path, "right"), sink, checkpoints)
    replay = _run(PiSessionConnector(path, "right"), sink, checkpoints)

    assert [item[0] for item in sink.items] == ["pi:session:root", "pi:session:left", "pi:session:right"]
    assert len(first.items) == 2
    assert len(second.items) == 1
    assert second.previous_checkpoint == first.committed_checkpoint
    assert replay.items == ()
    assert replay.committed_checkpoint == second.committed_checkpoint


def test_the_connector_rejects_rewritten_records_and_keeps_the_progress(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    records = [
        {"type": "message", "id": "root", "parentId": None, "message": {"role": "user", "content": "shared"}},
        {"type": "message", "id": "tail", "parentId": "root", "message": {"role": "assistant", "content": "tail"}},
    ]
    _write_session(path, records)
    sink, checkpoints = _RecordingSink(), _MemoryCheckpoints()

    committed = _run(PiSessionConnector(path), sink, checkpoints).committed_checkpoint

    records[0]["message"]["content"] = "rewritten"
    _write_session(path, records)
    with pytest.raises(ValueError):
        _run(PiSessionConnector(path), sink, checkpoints)
    assert checkpoints.value == committed

    records[0]["message"]["content"] = "shared"
    _write_session(path, records)
    repaired = _run(PiSessionConnector(path), sink, checkpoints)

    assert repaired.previous_checkpoint == committed
    assert repaired.items == ()


def test_the_connector_rejects_a_missing_session_file(tmp_path: Path):
    with pytest.raises(ValueError):
        _run(PiSessionConnector(tmp_path / "absent.jsonl"), _RecordingSink(), _MemoryCheckpoints())
