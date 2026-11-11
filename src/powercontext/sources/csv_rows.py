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

"""Row-level CSV capture so cited evidence traces back to one raw record."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.errors import InvalidSourceReferenceError
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"
"""Stable registration name of the row-level CSV Source Definition."""

CSV_ROW_SOURCE_VERSION = "1"
"""Durable version of the row-level CSV Source Definition."""


class CSVDocumentInput(BaseModel):
    """One raw CSV document offered to the capture pipeline."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One captured CSV row addressed by its primary key value."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """Captured CSV row kept with the column order and values it was read with."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Return the stable identity of one captured CSV row."""

    _validate_document_id(document_id)
    _validate_primary_key_value(primary_key_value)
    source_id = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise InvalidSourceReferenceError(
            "source_id",
            f"must not exceed {MAX_SOURCE_ID_LENGTH} characters",
        )
    return source_id


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse one raw CSV document into the rows it captures."""

    document_id = value.document_id
    _validate_document_id(document_id)
    primary_key = value.primary_key
    if not isinstance(primary_key, str) or not primary_key.strip():
        raise ValueError("primary_key must be a non-empty string")  # noqa: TRY003
    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"CSV document {document_id!r} is not valid UTF-8") from error
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"CSV document {document_id!r} is malformed: {error}") from error
    if not records:
        raise ValueError(f"CSV document {document_id!r} has no header record")  # noqa: TRY003
    header = records[0]
    _validate_header(header, document_id)
    if primary_key not in header:
        raise ValueError(  # noqa: TRY003
            f"primary key {primary_key!r} is missing from the CSV header of {document_id!r}"
        )
    key_index = header.index(primary_key)
    rows: list[CSVRowInput] = []
    seen: set[str] = set()
    for record in records[1:]:
        if not record:
            continue
        if len(record) != len(header):
            raise ValueError(  # noqa: TRY003
                f"CSV row of {document_id!r} has {len(record)} fields instead of {len(header)}"
            )
        key_value = record[key_index]
        _validate_primary_key_value(key_value)
        if key_value in seen:
            raise ValueError(  # noqa: TRY003
                f"CSV document {document_id!r} repeats the primary key value {key_value!r}"
            )
        seen.add(key_value)
        rows.append(
            CSVRowInput(
                document_id=document_id,
                primary_key=primary_key,
                primary_key_value=key_value,
                columns=tuple(header),
                values=tuple(record),
            )
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class CSVRowTextEvidenceProjection:
    """Project one captured CSV row into the standard textual evidence shape."""

    name: str = TEXT_EVIDENCE_PROJECTION_KEY.name
    version: str = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class: type[CSVRowSource] = CSVRowSource
    output_class: type[TextEvidence] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        """Render the captured row as one JSON object keyed by its columns."""

        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


@dataclass(frozen=True, slots=True)
class CSVRowSourceDefinition:
    """Bind captured CSV rows to the durable Source contract."""

    version: str = CSV_ROW_SOURCE_VERSION
    projections: tuple[CSVRowTextEvidenceProjection, ...] = (CSVRowTextEvidenceProjection(),)

    @property
    def input_class(self) -> type[CSVRowInput]:
        return CSVRowInput

    @property
    def name(self) -> str:
        return CSV_ROW_SOURCE_NAME

    @property
    def source_class(self) -> type[CSVRowSource]:
        return CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        """Capture one parsed row as a materialized Source."""

        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,
            description=None,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        """Read one captured row back as the value it was captured from."""

        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = (
    CSVRowSourceDefinition()
)
"""Definition used to capture CSV rows and project their evidence."""


def _validate_document_id(document_id: str) -> None:
    if not isinstance(document_id, str) or not document_id.strip():
        raise InvalidSourceReferenceError("document_id", "must be a non-empty string")
    if document_id != document_id.strip():
        raise InvalidSourceReferenceError(
            "document_id",
            "must not contain leading or trailing whitespace",
        )


def _validate_primary_key_value(primary_key_value: str) -> None:
    if not isinstance(primary_key_value, str) or not primary_key_value.strip():
        raise InvalidSourceReferenceError("primary_key_value", "must be a non-empty string")


def _validate_header(header: list[str], document_id: str) -> None:
    if not header:
        raise ValueError(f"CSV document {document_id!r} has an empty header record")  # noqa: TRY003
    seen: set[str] = set()
    for column in header:
        if not column.strip():
            raise ValueError(f"CSV document {document_id!r} has a blank header field")  # noqa: TRY003
        if column in seen:
            raise ValueError(  # noqa: TRY003
                f"CSV document {document_id!r} repeats the header field {column!r}"
            )
        seen.add(column)
