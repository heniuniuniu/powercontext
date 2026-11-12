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

"""Behavior tests for the incremental Context Pack transport."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest

from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def test_pack_wire_matches_an_independent_digest_oracle() -> None:
    items = (b"alpha\n", "\u6d77\u6d0b".encode())
    unsigned = {"schema": "powercontext.context-pack.v1", "items": [base64.b64encode(item).decode() for item in items]}
    pack = ContextPack.build(items)

    payload = json.loads(pack.to_bytes())

    assert payload == {
        **unsigned,
        "digest": "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest(),
        "byte_count": len(pack.to_bytes()),
    }
    assert pack.to_bytes() == _canonical(payload)
    assert pack.byte_count == len(pack.to_bytes())


def test_delta_wire_states_the_prefix_and_suffix_without_a_byte_count_field() -> None:
    base = ContextPack.build((b"a", b"b"))
    target = ContextPack.build((b"a", b"b", b"c"))
    delta = ContextPackDelta.between(base, target)

    payload = json.loads(delta.to_bytes())

    assert set(payload) == {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
    assert payload["schema"] == "powercontext.context-pack-delta.v1"
    assert payload["base_digest"] == base.digest
    assert payload["target_digest"] == target.digest
    assert payload["prefix_items"] == 2
    assert payload["appended"] == [base64.b64encode(b"c").decode()]
    assert delta.byte_count == len(delta.to_bytes())


def test_delta_rebuilds_targets_that_change_at_any_position() -> None:
    base = ContextPack.build((b"a", b"b", b"old"))
    for target_items in ((b"a", b"b", b"new", b"tail"), (b"new", b"b", b"old"), (), (b"a", b"b", b"old")):
        target = ContextPack.build(target_items)

        rebuilt = apply_context_pack_delta(
            ContextPack.from_bytes(base.to_bytes()),
            ContextPackDelta.from_bytes(ContextPackDelta.between(base, target).to_bytes()),
        )

        assert rebuilt.to_bytes() == target.to_bytes()
        assert b"".join(rebuilt.items) == b"".join(target_items)


def test_chunks_stay_utf8_safe_within_the_requested_limit() -> None:
    pack = ContextPack.from_content("alpha \u6d77\u6d0b beta", chunk_bytes=7)

    assert b"".join(pack.items).decode("utf-8") == "alpha \u6d77\u6d0b beta"
    assert all(len(item) <= 7 for item in pack.items)
    assert all(item.decode("utf-8") for item in pack.items)
    assert ContextPack.from_content("").items == ()


def test_chunk_limit_that_cannot_hold_one_character_fails() -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_content("\u6d77", chunk_bytes=2)
    with pytest.raises(ContextPackError):
        ContextPack.from_content("abc", chunk_bytes=0)


def test_pack_decoding_rejects_missing_fields_and_tampering() -> None:
    pack = ContextPack.build((b"alpha",))
    payload = json.loads(pack.to_bytes())

    for field in payload:
        incomplete = {key: value for key, value in payload.items() if key != field}
        with pytest.raises(ContextPackError):
            ContextPack.from_bytes(_canonical(incomplete))
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(pack.to_bytes().replace(pack.digest.encode(), b"sha256:" + b"0" * 64))
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(json.dumps({**payload, "extra": True}).encode())


@pytest.mark.parametrize("prefix", [True, 1.5, -1])
def test_delta_decoding_rejects_non_integer_or_negative_prefix(prefix: object) -> None:
    base = ContextPack.build((b"alpha",))
    payload = json.loads(ContextPackDelta.between(base, ContextPack.build((b"alpha", b"beta"))).to_bytes())
    payload["prefix_items"] = prefix

    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(_canonical(payload))


def test_applying_a_delta_rejects_wrong_base_prefix_and_target() -> None:
    base = ContextPack.build((b"one",))
    delta = ContextPackDelta.between(base, ContextPack.build((b"one", b"two")))

    with pytest.raises(ContextPackError):
        apply_context_pack_delta(ContextPack.build((b"other",)), delta)
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, ContextPackDelta(base.digest, "sha256:" + "0" * 64, 2, ()))
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, ContextPackDelta(base.digest, "sha256:" + "0" * 64, 0, (b"two",)))


def test_empty_pack_round_trips_and_is_a_legal_delta_target() -> None:
    empty = ContextPack.build(())

    assert ContextPack.from_bytes(empty.to_bytes()) == empty
    assert apply_context_pack_delta(empty, ContextPackDelta.between(empty, empty)).to_bytes() == empty.to_bytes()
