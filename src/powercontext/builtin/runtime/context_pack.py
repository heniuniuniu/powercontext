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

"""Prefix-reuse Context Pack transport for incremental context delivery.

A :class:`ContextPack` is a complete prepared context split into bounded, UTF-8-safe
chunks and sealed with a digest, so a receiver can verify one payload on its own. A
:class:`~ContextPackDelta` describes a later context as "keep this many chunks of the
previous pack, then append these chunks", which lets a client that already holds the
previous pack rebuild the next one without receiving the repeated prefix again.

Both objects are pure values: decoding never needs the sender's process state, so a
receiver can rebuild a context from bytes alone.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any, NoReturn

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CONTEXT_PACK_CHUNK_BYTES = 512

_DIGEST_PREFIX = "sha256:"
_DIGEST_LENGTH = len(_DIGEST_PREFIX) + 64
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(ValueError):
    """A Context Pack, delta, or chunk layout breaks the wire protocol."""


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete context as an ordered tuple of verified chunks."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Seal an ordered tuple of chunks as a complete pack."""

        encoded = [_encode_item(item) for item in items]
        digest = _digest(encoded)
        return cls(items=tuple(items), digest=digest, byte_count=_settle_byte_count(encoded, digest))

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = DEFAULT_CONTEXT_PACK_CHUNK_BYTES) -> ContextPack:
        """Chunk text deterministically at UTF-8 boundaries within ``chunk_bytes``."""

        return cls.build(_chunk_content(content, chunk_bytes))

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        """Decode one wire pack, verifying its digest and byte count."""

        payload = _load_object(data, _PACK_FIELDS, "context pack")
        if payload["schema"] != CONTEXT_PACK_SCHEMA:
            _fail(f"context pack schema must be {CONTEXT_PACK_SCHEMA}")
        raw_items = _load_base64_list(payload["items"], "context pack items")
        digest = _validate_digest(payload["digest"], "context pack digest")
        if digest != _digest(raw_items):
            _fail("context pack digest does not match its items")
        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count != len(bytes(data)):
            _fail("context pack byte count does not match its payload")
        return cls(items=_decode_items(raw_items, "context pack items"), digest=digest, byte_count=byte_count)

    def to_bytes(self) -> bytes:
        """Serialize to canonical wire JSON."""

        return _canonical_json({
            "schema": CONTEXT_PACK_SCHEMA,
            "items": [_encode_item(item) for item in self.items],
            "digest": self.digest,
            "byte_count": self.byte_count,
        })


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A reusable prefix of an earlier pack plus the chunks that follow it."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @property
    def byte_count(self) -> int:
        """Wire size of this delta; never written as a delta field."""

        return len(self.to_bytes())

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Describe ``target`` with the longest shared chunk prefix of ``base``."""

        prefix = _common_prefix(base.items, target.items)
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=tuple(target.items[prefix:]),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        """Decode one wire delta without needing its base pack."""

        payload = _load_object(data, _DELTA_FIELDS, "context pack delta")
        if payload["schema"] != CONTEXT_PACK_DELTA_SCHEMA:
            _fail(f"context pack delta schema must be {CONTEXT_PACK_DELTA_SCHEMA}")
        raw_appended = _load_base64_list(payload["appended"], "context pack delta appended items")
        prefix_items = payload["prefix_items"]
        if type(prefix_items) is not int or prefix_items < 0:
            _fail("context pack delta prefix must be a non-negative integer")
        return cls(
            base_digest=_validate_digest(payload["base_digest"], "context pack delta base digest"),
            target_digest=_validate_digest(payload["target_digest"], "context pack delta target digest"),
            prefix_items=prefix_items,
            appended=_decode_items(raw_appended, "context pack delta appended items"),
        )

    def to_bytes(self) -> bytes:
        """Serialize to canonical wire JSON."""

        return _canonical_json({
            "schema": CONTEXT_PACK_DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": [_encode_item(item, "context pack delta item") for item in self.appended],
        })


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the delta's target pack from ``base`` and ``delta``."""

    if delta.base_digest != base.digest:
        _fail("context pack delta does not apply to the supplied base pack")
    if type(delta.prefix_items) is not int or delta.prefix_items < 0:
        _fail("context pack delta prefix must be a non-negative integer")
    if delta.prefix_items > len(base.items):
        _fail("context pack delta prefix is longer than the base pack")
    rebuilt = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if rebuilt.digest != delta.target_digest:
        _fail("context pack delta target digest does not match the rebuilt pack")
    return rebuilt


def _fail(message: str) -> NoReturn:
    raise ContextPackError(message)


def _fail_from(message: str, error: Exception) -> NoReturn:
    raise ContextPackError(message) from error


def _canonical_json(payload: dict[str, Any]) -> bytes:
    """Encode one payload exactly as the wire format requires."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_item(item: bytes, label: str = "context pack item") -> str:
    if not isinstance(item, bytes):
        _fail(f"{label} must be bytes")
    return base64.b64encode(item).decode("ascii")


def _decode_items(raw_items: list[str], label: str) -> tuple[bytes, ...]:
    items: list[bytes] = []
    for value in raw_items:
        try:
            decoded = base64.b64decode(value.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError) as error:
            _fail_from(f"{label} must be standard Base64", error)
        if base64.b64encode(decoded).decode("ascii") != value:
            _fail(f"{label} must be standard Base64")
        items.append(decoded)
    return tuple(items)


def _load_base64_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        _fail(f"{label} must be a JSON array of Base64 strings")
    return list(value)


def _digest(payload_items: list[str]) -> str:
    """Digest exactly ``schema`` and ``items``, as the wire contract states."""

    canonical = _canonical_json({"schema": CONTEXT_PACK_SCHEMA, "items": payload_items})
    return f"{_DIGEST_PREFIX}{hashlib.sha256(canonical).hexdigest()}"


def _validate_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != _DIGEST_LENGTH or not value.startswith(_DIGEST_PREFIX):
        _fail(f"{label} must be sha256:<hex>")
    if not set(value[len(_DIGEST_PREFIX) :]) <= _HEX_DIGITS:
        _fail(f"{label} must be sha256:<hex>")
    return value


def _load_object(data: bytes, fields: frozenset[str], label: str) -> dict[str, Any]:
    if not isinstance(data, (bytes, bytearray)):
        _fail(f"{label} must be bytes")
    try:
        payload = json.loads(bytes(data))
    except ValueError as error:
        _fail_from(f"{label} must be UTF-8 JSON", error)
    if not isinstance(payload, dict):
        _fail(f"{label} must be a JSON object")
    if set(payload) != set(fields):
        _fail(f"{label} must contain exactly {', '.join(sorted(fields))}")
    return payload


def _settle_byte_count(payload_items: list[str], digest: str) -> int:
    """Find the byte count that reproduces the length of its own serialization."""

    size = 0
    for _ in range(8):
        candidate = len(
            _canonical_json({
                "schema": CONTEXT_PACK_SCHEMA,
                "items": payload_items,
                "digest": digest,
                "byte_count": size,
            })
        )
        if candidate == size:
            return size
        size = candidate
    _fail("context pack byte count does not converge")


def _common_prefix(base: tuple[bytes, ...], target: tuple[bytes, ...]) -> int:
    for index, (left, right) in enumerate(zip(base, target, strict=False)):
        if left != right:
            return index
    return min(len(base), len(target))


def _chunk_content(content: str, chunk_bytes: int) -> tuple[bytes, ...]:
    """Split text greedily into chunks that never break a UTF-8 character."""

    if type(chunk_bytes) is not int or chunk_bytes < 1:
        _fail("context pack chunk size must be a positive integer")
    chunks: list[bytes] = []
    pending: list[str] = []
    size = 0
    for character in content:
        encoded = character.encode("utf-8")
        if len(encoded) > chunk_bytes:
            _fail("context pack chunk size cannot hold one encoded character")
        if size + len(encoded) > chunk_bytes:
            chunks.append("".join(pending).encode("utf-8"))
            pending = []
            size = 0
        pending.append(character)
        size += len(encoded)
    if pending:
        chunks.append("".join(pending).encode("utf-8"))
    return tuple(chunks)


__all__ = [
    "CONTEXT_PACK_DELTA_SCHEMA",
    "CONTEXT_PACK_SCHEMA",
    "DEFAULT_CONTEXT_PACK_CHUNK_BYTES",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
