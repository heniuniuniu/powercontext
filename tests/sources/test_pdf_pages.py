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

"""Contract tests for page-level PDF capture and evidence retrieval."""

from __future__ import annotations

import asyncio
import hashlib
import io

import pytest
from pypdf import PdfWriter

from powercontext.artifacts.models import ArtifactRef
from powercontext.builtin.artifacts.memory.models import MemoryEntryVersion
from powercontext.builtin.runtime.models import (
    GetMemoryEntryRequest,
    MemoryEntriesPage,
    MemoryEntryRecord,
)
from powercontext.sources.definitions import SourceDefinitionRegistry
from powercontext.sources.models import SourceRef
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    parse_pdf_document,
    pdf_page_source_id,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

_MEMORY_REF = ArtifactRef(family="memory", artifact_id="memory-1", revision=1)


def _pdf_bytes(page_texts: list[str]) -> bytes:
    """Build a small PDF whose pages carry the given text."""

    objects: list[bytes] = []
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(len(page_texts)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_texts)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, text in enumerate(page_texts):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * index} 0 R >>"
            ).encode()
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode())

    document = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    startxref = len(document)
    document += f"xref\n0 {len(objects) + 1}\n".encode()
    document += b"0000000000 65535 f \n"
    for offset in offsets:
        document += f"{offset:010d} 00000 n \n".encode()
    document += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{startxref}\n%%EOF\n".encode()
    return bytes(document)


def _parse(page_texts: list[str], *, document_id: str = "report"):
    return parse_pdf_document(PDFDocumentInput(document_id=document_id, content=_pdf_bytes(page_texts)))


class _StoredScope:
    """Read-only Memory scope holding the pages of one parsed document."""

    def __init__(self, pages: list[PDFPageInput]) -> None:
        self._records: dict[tuple[str, str], MemoryEntryRecord] = {}
        for index, page in enumerate(pages):
            source_id = pdf_page_source_id(page.document_id, page.document_sha256, page.page_number)
            entry = MemoryEntryVersion(
                memory_artifact_id="memory-1",
                entry_id=f"entry-{index}",
                entry_version_id=f"version-{index}",
                version=1,
                previous_version_id=None,
                kind="evidence",
                text=f"{page.document_id}\n{page.text}",
                entry_content_hash=hashlib.sha256(page.text.encode()).hexdigest(),
                created_in_revision=1,
                sources=(SourceRef(source_type="pdf-page", source_id=source_id),),
            )
            record = MemoryEntryRecord(memory_ref=_MEMORY_REF, state="active", entry=entry)
            self._records[(entry.entry_id, entry.entry_version_id)] = record

    async def list(self) -> MemoryEntriesPage:
        return MemoryEntriesPage(memory_ref=_MEMORY_REF, entries=tuple(self._records.values()))

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord:
        citation = request.citation
        return self._records[(citation.entry_id, citation.entry_version_id)]


def test_pages_are_kept_in_physical_order_and_counted_from_one():
    pages = _parse(["Cover page", "Revenue grew to 100", "Risk factors"])

    assert [page.page_number for page in pages] == [1, 2, 3]
    assert [page.text for page in pages] == ["Cover page", "Revenue grew to 100", "Risk factors"]
    assert all(page.document_id == "report" for page in pages)


def test_blank_pages_are_kept_as_pages_without_text():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.add_blank_page(width=100, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    pages = parse_pdf_document(PDFDocumentInput(document_id="blank", content=buffer.getvalue()))

    assert [(page.page_number, page.text) for page in pages] == [(1, ""), (2, "")]


def test_the_document_digest_covers_the_whole_file():
    content = _pdf_bytes(["One page"])

    pages = parse_pdf_document(PDFDocumentInput(document_id="report", content=content))

    assert pages[0].document_sha256 == hashlib.sha256(content).hexdigest()
    assert pages[0].document_sha256 == pages[0].document_sha256.lower()
    assert len(pages[0].document_sha256) == 64


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"not a PDF",
        b"%PDF-1.7\ntruncated",
    ],
)
def test_unreadable_documents_fail_without_partial_pages(raw):
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="invalid", content=raw))


def test_a_document_id_must_not_be_blank():
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="   ", content=_pdf_bytes(["Body"])))


def test_a_permission_marker_with_an_empty_password_stays_readable():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.encrypt(user_password="", owner_password="owner")  # noqa: S106 - synthetic PDF owner password.
    buffer = io.BytesIO()
    writer.write(buffer)

    pages = parse_pdf_document(PDFDocumentInput(document_id="permissions", content=buffer.getvalue()))

    assert len(pages) == 1
    assert pages[0].page_number == 1


def test_source_ids_are_stable_bounded_and_unique():
    digest = "a" * 64

    assert pdf_page_source_id("report", digest, 1) == pdf_page_source_id("report", digest, 1)
    assert pdf_page_source_id("report", digest, 1) != pdf_page_source_id("report", digest, 2)
    assert pdf_page_source_id("report", digest, 1) != pdf_page_source_id("other", digest, 1)
    assert pdf_page_source_id("report", digest, 1) != pdf_page_source_id("report", "b" * 64, 1)
    assert len(pdf_page_source_id("report", digest, 1)) <= 256
    assert len(pdf_page_source_id("r" * 400, digest, 1)) <= 256


def test_a_revised_document_gets_a_new_identity():
    original = _parse(["Revenue grew to 100"])
    revised = _parse(["Revenue grew to 120"])
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))

    first = asyncio.run(registry.resolve(original[0]))
    second = asyncio.run(registry.resolve(revised[0]))

    assert first.name != second.name
    assert asyncio.run(registry.read(first)) == original[0]


def test_the_definition_projects_one_complete_citation():
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    page = _parse(["Revenue grew to 100"])[0]

    source = asyncio.run(registry.resolve(page))
    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))

    assert source.name == source.source_id
    assert source.materialization.value == "captured"
    assert source.definition_version == "1"
    assert asyncio.run(registry.read(source)) == page
    assert evidence.source_type == "pdf-page"
    assert evidence.source_id == source.name
    assert evidence.content == f"{page.document_id}\n{page.text}"
    assert evidence.metadata == {
        "document_id": page.document_id,
        "document_sha256": page.document_sha256,
        "page_number": page.page_number,
    }


def test_search_returns_one_exact_citation_per_page():
    pages = _parse(["Cover page", "Total net revenue was 91,854", "Risk factors"])
    scope = _StoredScope(pages)

    citations = asyncio.run(search_pdf_pages(scope, "What was the total net revenue in 2022?", limit=10))

    assert citations
    assert len(citations) <= 10
    identities = set()
    for citation in citations:
        record = asyncio.run(scope.get(GetMemoryEntryRequest(citation=citation)))
        assert record.citation == citation
        reference = record.entry.sources[0]
        identities.add((record.entry.text.split("\n", 1)[0], int(reference.source_id.rsplit(":", 1)[1])))
    assert len(identities) == len(citations)
    assert ("report", 2) in identities
