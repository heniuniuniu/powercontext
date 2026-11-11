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

"""Capture the visible events of one selected Pi session branch as Sources."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
"""Stable registration name of the Pi event Source Definition."""

PI_EVENT_SOURCE_VERSION = "1"
"""Durable version of the Pi event Source Definition."""

PI_SESSION_CONNECTOR_NAME = "pi-session-history"
"""Stable registration name of the Pi session Connector."""

PI_SESSION_CONNECTOR_VERSION = "1"
"""Durable version of the Pi session Connector."""

_SESSION_VERSION = 3
"""Only Pi session archives declaring this version are captured."""

_CHECKPOINT_VERSION = 1
"""Version of the Connector owned checkpoint layout."""


class PiSessionInput(BaseModel):
    """One raw Pi session archive offered to the capture pipeline."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event captured from a Pi session branch."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """Captured Pi event kept with the raw record it was parsed from."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class _ParsedSession:
    """One parsed session archive with its branch and its raw records."""

    session_id: str
    records: tuple[dict[str, JsonValue], ...]
    events: tuple[PiEventInput, ...]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Return the stable identity of one captured Pi event."""

    identity = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) <= MAX_SOURCE_ID_LENGTH:
        return identity
    digest = hashlib.sha256(f"{session_id}\x00{event_id}".encode()).hexdigest()
    return f"pi:{digest}"


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse one Pi session archive into the visible events of the selected branch."""

    return _parse_session(value).events


@dataclass(frozen=True, slots=True)
class PiEventTextEvidenceProjection:
    """Project one captured Pi event into the standard textual evidence shape."""

    name: str = TEXT_EVIDENCE_PROJECTION_KEY.name
    version: str = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[PiEventSource] = PiEventSource
    output_class: type[TextEvidence] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        """Render the captured event as one complete textual citation."""

        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


@dataclass(frozen=True, slots=True)
class PiEventSourceDefinition:
    """Bind captured Pi events to the durable Source contract."""

    version: str = PI_EVENT_SOURCE_VERSION
    projections: tuple[PiEventTextEvidenceProjection, ...] = (PiEventTextEvidenceProjection(),)

    @property
    def input_class(self) -> type[PiEventInput]:
        return PiEventInput

    @property
    def name(self) -> str:
        return PI_EVENT_SOURCE_NAME

    @property
    def source_class(self) -> type[PiEventSource]:
        return PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        """Capture one parsed event as a materialized Source."""

        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,
            description=None,
            source_id=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        """Read one captured event back as the value it was captured from."""

        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = PiEventSourceDefinition()
"""Definition used to capture Pi events and project their evidence."""


class PiSessionConnector:
    """Import the visible events of one Pi session branch as immutable Sources."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        """Import every visible event of the selected branch that is not imported yet."""

        parsed = _parse_session(PiSessionInput(content=_read_file(self._path), leaf_id=self._leaf_id))
        imported = _imported_events(session.checkpoint, parsed.session_id)
        _reject_rewritten_events(imported, parsed)
        pending = [event for event in parsed.events if event.event_id not in imported]
        for event in pending:
            await session.submit(
                pi_event_source_id(event.session_id, event.event_id),
                PI_EVENT_SOURCE_NAME,
                event,
            )
            imported[event.event_id] = _record_digest(event.record)
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={
                "version": _CHECKPOINT_VERSION,
                "session_id": parsed.session_id,
                "imported": dict(imported),
            },
        )


def _parse_session(value: PiSessionInput) -> _ParsedSession:
    """Parse one Pi session archive into its branch and its raw records."""

    session_id, records = _read_records(value.content)
    events = []
    for record in _select_branch(records, value.leaf_id):
        rendered = _render_event(record)
        if rendered is None:
            continue
        role, body = rendered
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=_event_id(record),
                parent_id=_parent_id(record),
                event_type=_event_type(record),
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )
    return _ParsedSession(session_id=session_id, records=records, events=tuple(events))


def _read_records(content: bytes) -> tuple[str, tuple[dict[str, JsonValue], ...]]:
    """Read one Pi session archive into its header identity and its records."""

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session archive is not valid UTF-8") from error  # noqa: TRY003
    records: list[dict[str, JsonValue]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Pi session line {number} is not valid JSON: {error}") from error  # noqa: TRY003
        if not isinstance(payload, dict):
            raise ValueError(f"Pi session line {number} is not a JSON object")  # noqa: TRY003, TRY004
        records.append({str(key): value for key, value in payload.items()})
    if not records:
        raise ValueError("Pi session archive contains no records")  # noqa: TRY003
    header = records[0]
    if header.get("type") != "session":
        raise ValueError("Pi session archive must start with a session header")  # noqa: TRY003
    version = header.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != _SESSION_VERSION:
        raise ValueError(f"Pi session archive must declare version {_SESSION_VERSION}")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Pi session header must declare a non-empty id")  # noqa: TRY003
    events = records[1:]
    _validate_graph(events)
    return session_id, tuple(events)


def _validate_graph(records: tuple[dict[str, JsonValue], ...]) -> None:
    """Validate the parent chain of every record before any event is produced."""

    seen: set[str] = set()
    for record in records:
        event_id = _event_id(record)
        if event_id in seen:
            raise ValueError(f"Pi session repeats the event id {event_id!r}")  # noqa: TRY003
        if "parentId" not in record:
            raise ValueError(f"Pi session event {event_id!r} must declare parentId")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError(f"Pi session event {event_id!r} declares an invalid parentId")  # noqa: TRY003
        if isinstance(parent_id, str) and parent_id and parent_id not in seen:
            raise ValueError(f"Pi session event {event_id!r} refers to the unknown parent {parent_id!r}")  # noqa: TRY003
        seen.add(event_id)


def _select_branch(
    records: tuple[dict[str, JsonValue], ...],
    leaf_id: str | None,
) -> tuple[dict[str, JsonValue], ...]:
    """Return the ancestor chain of the selected leaf, ordered from root to leaf."""

    by_id = {_event_id(record): record for record in records}
    leaf = _event_id(records[-1]) if leaf_id is None or not leaf_id.strip() else leaf_id
    if leaf not in by_id:
        raise ValueError(f"Pi session does not contain the leaf event {leaf!r}")  # noqa: TRY003
    branch: list[dict[str, JsonValue]] = []
    visited: set[str] = set()
    current: str | None = leaf
    while current is not None:
        if current in visited:
            raise ValueError(f"Pi session event {current!r} takes part in a cycle")  # noqa: TRY003
        visited.add(current)
        record = by_id[current]
        branch.append(record)
        current = _parent_id(record)
    branch.reverse()
    return tuple(branch)


def _render_event(record: dict[str, JsonValue]) -> tuple[str, str] | None:
    """Render one record as its role and its visible body."""

    event_type = _event_type(record)
    if event_type == "message":
        message = record.get("message")
        return None if not isinstance(message, dict) else _render_message(message)
    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        body = summary if isinstance(summary, str) else ""
        return (event_type, body) if body else None
    if event_type == "custom_message":
        body = _render_visible_text(record.get("content"))
        return ("custom", body) if body else None
    return None


def _render_message(message: dict[str, JsonValue]) -> tuple[str, str] | None:
    """Render one Pi message as its role and its visible body."""

    role = message.get("role")
    if not isinstance(role, str) or not role.strip():
        return None
    if role == "toolResult":
        body = _render_tool_result(message)
        return (role, body) if body else None
    if role == "bashExecution":
        return _render_bash_execution(message)
    body = _render_message_content(message.get("content"))
    return (role, body) if body else None


def _render_bash_execution(message: dict[str, JsonValue]) -> tuple[str, str] | None:
    """Render one executed shell command as its role and its visible body."""

    command = message.get("command")
    output = message.get("output")
    command_text = command if isinstance(command, str) else ""
    output_text = output if isinstance(output, str) else ""
    if not command_text and not output_text:
        return None
    body = f"$ {command_text}" if not output_text else f"$ {command_text}\n{output_text}"
    return ("bashExecution", body)


def _render_message_content(value: object) -> str:
    """Render the visible blocks of one message content."""

    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts = []
    for block in value:
        rendered = _render_block(block)
        if rendered:
            parts.append(rendered)
    return "\n".join(parts)


def _render_visible_text(value: object) -> str:
    """Render the visible strings and text blocks of one content."""

    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts = []
    for block in value:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _render_block(block: object) -> str | None:
    """Render one content block that carries visible text."""

    if not isinstance(block, dict):
        return None
    block_type = block.get("type")
    if block_type == "text":
        text = block.get("text")
        return text if isinstance(text, str) else None
    if block_type == "toolCall":
        return _render_tool_call(block)
    if block_type == "toolResult":
        return _render_tool_result(block)
    return None


def _render_tool_call(block: dict[str, JsonValue]) -> str:
    """Render one tool call with its compact and sorted arguments."""

    arguments = block.get("arguments")
    encoded = json.dumps(
        {} if arguments is None else arguments,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return f"[tool_call {_text(block.get('name'))} {_text(block.get('id'))}] {encoded}"


def _render_tool_result(block: dict[str, JsonValue]) -> str:
    """Render one tool result with the call it answers."""

    prefix = f"[tool_result {_text(block.get('toolName'))} {_text(block.get('toolCallId'))}]"
    text = _render_message_content(block.get("content"))
    return f"{prefix} {text}" if text else prefix


def _text(value: object) -> str:
    """Return one optional textual field as plain text."""

    return value if isinstance(value, str) else ""


def _event_id(record: dict[str, JsonValue]) -> str:
    """Return the identifier of one record."""

    event_id = record.get("id")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("Pi session records must declare a non-empty id")  # noqa: TRY003
    return event_id


def _event_type(record: dict[str, JsonValue]) -> str:
    """Return the type of one record."""

    event_type = record.get("type")
    return event_type if isinstance(event_type, str) else ""


def _parent_id(record: dict[str, JsonValue]) -> str | None:
    """Return the parent of one record, or None when it starts a branch."""

    parent_id = record.get("parentId")
    if not isinstance(parent_id, str) or not parent_id:
        return None
    return parent_id


def _read_file(path: Path) -> bytes:
    """Read the raw bytes of one Pi session archive."""

    try:
        content = path.read_bytes()
    except OSError as error:
        raise ValueError(f"Pi session file {path} could not be read: {error}") from error  # noqa: TRY003
    return content


def _record_digest(record: dict[str, JsonValue]) -> str:
    """Return the checksum of one raw record."""

    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _imported_events(checkpoint: JsonValue | None, session_id: str) -> dict[str, str]:
    """Return the imported event identities and their recorded checksums."""

    if not isinstance(checkpoint, dict):
        return {}
    if checkpoint.get("session_id") != session_id:
        return {}
    imported = checkpoint.get("imported")
    if not isinstance(imported, dict):
        return {}
    return {
        str(event_id): str(digest)
        for event_id, digest in imported.items()
        if isinstance(event_id, str) and isinstance(digest, str)
    }


def _reject_rewritten_events(imported: dict[str, str], parsed: _ParsedSession) -> None:
    """Reject a session whose already imported records were rewritten."""

    digests = {_event_id(record): _record_digest(record) for record in parsed.records}
    for event_id, digest in imported.items():
        current = digests.get(event_id)
        if current is not None and current != digest:
            raise ValueError(f"Pi session event {event_id!r} was rewritten after it was imported")  # noqa: TRY003
