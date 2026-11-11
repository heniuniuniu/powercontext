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

"""Contract tests for row-level CSV capture."""

from __future__ import annotations

import asyncio
import json

import pytest

from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    CSVRowInput,
    CSVRowSource,
    csv_row_source_id,
    parse_csv_document,
)
from powercontext.sources.definitions import SourceDefinitionRegistry
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


def _parse(content: bytes, *, document_id: str = "doc", primary_key: str = "id"):
    return parse_csv_document(CSVDocumentInput(document_id=document_id, primary_key=primary_key, content=content))


def test_bom_and_multiline_quoted_values_are_preserved():
    rows = _parse(
        b'\xef\xbb\xbf" id ",name,note\r\n001,"Airport, East","first\r\nsecond"\r\n',
        primary_key=" id ",
    )

    assert len(rows) == 1
    assert rows[0].columns == (" id ", "name", "note")
    assert rows[0].values == ("001", "Airport, East", "first\r\nsecond")


def test_completely_empty_records_are_ignored():
    assert _parse(b"id,name\n\n") == ()
    assert len(_parse(b"id,name\n\n1,one\n")) == 1


def test_header_only_document_is_valid():
    assert _parse(b"id,name\n") == ()


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"id,id\n1,one\n",
        b"id,\n1,one\n",
        b" \t,name\n1,one\n",
        b"id,name\n1\n",
        b"id,name\n,empty-key\n",
        b"id,name\n1,one\n1,duplicate\n",
        b'id,name\n1,"unclosed\n',
        b"\xff\xfe",
    ],
)
def test_invalid_documents_raise_value_error(content):
    with pytest.raises(ValueError):
        _parse(content)


def test_missing_primary_key_is_rejected():
    with pytest.raises(ValueError):
        _parse(b"id,name\n1,one\n", primary_key="missing")


def test_primary_key_is_matched_exactly():
    with pytest.raises(ValueError):
        _parse(b" id ,name\n1,one\n", primary_key="id")


def test_document_id_rejects_surrounding_whitespace():
    with pytest.raises(ValueError):
        _parse(b"id,name\n1,one\n", document_id=" doc ")


def test_primary_key_value_keeps_surrounding_whitespace():
    rows = _parse(b"id,name\n 001 ,one\n")

    assert rows[0].primary_key_value == " 001 "


def test_failed_validation_yields_no_partial_rows():
    with pytest.raises(ValueError):
        _parse(b"id,name\n1,one\n2\n")


def test_source_id_is_stable_and_percent_encoded():
    assert csv_row_source_id("doc:one", "key/one") == "csv:doc%3Aone:key%2Fone"
    assert csv_row_source_id("doc", "key") == csv_row_source_id("doc", "key")
    assert csv_row_source_id("doc", " key ") != csv_row_source_id("doc", "key")


@pytest.mark.parametrize(
    ("document_id", "primary_key_value"),
    [(" doc ", "key"), ("doc", "  "), ("doc", "x" * 260)],
)
def test_source_id_rejects_unusable_identities(document_id, primary_key_value):
    with pytest.raises(ValueError):
        csv_row_source_id(document_id, primary_key_value)


def test_resolved_source_is_captured_and_readable():
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    row = CSVRowInput(
        document_id="doc",
        primary_key="id",
        primary_key_value="001",
        columns=("id", "name"),
        values=("001", "Airport"),
    )

    source = asyncio.run(registry.resolve(row))

    assert isinstance(source, CSVRowSource)
    assert source.source_id == source.name == "csv:doc:001"
    assert source.materialization.value == "captured"
    assert asyncio.run(registry.read(source)) == row


def test_projection_keeps_column_order_and_value_types():
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    row = CSVRowInput(
        document_id="doc",
        primary_key="id",
        primary_key_value="001",
        columns=("id", "name", "empty"),
        values=("001", "Naatsisʼáán", ""),  # noqa: RUF001 - keep the original characters.
    )
    source = asyncio.run(registry.resolve(row))

    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))

    assert evidence.source_type == "csv-row"
    assert evidence.source_id == "csv:doc:001"
    assert evidence.content == json.dumps(
        {"id": "001", "name": "Naatsisʼáán", "empty": ""},  # noqa: RUF001 - keep the original characters.
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert set(evidence.metadata) == {
        "document_id",
        "primary_key",
        "primary_key_value",
        "columns",
        "row",
    }
    assert evidence.metadata["columns"] == ["id", "name", "empty"]
    assert evidence.metadata["row"]["empty"] == ""
