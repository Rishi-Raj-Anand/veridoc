"""Parsers (CLAUDE.md §6.1.3): one fixture per file type, generated at test time."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from docx import Document

from ai_service.ingestion.parsers import (
    NO_TEXT_MESSAGE,
    SCANNED_PDF_MESSAGE,
    ParseError,
    normalise_text,
    parse_document,
)

PARAGRAPH = "Paracetamol is used to treat mild to moderate pain and to reduce fever. " * 3


def make_pdf(path: Path, pages: list[str]) -> Path:
    pdf = pymupdf.open()
    for text in pages:
        page = pdf.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 790), text, fontsize=11)
    pdf.save(path)
    pdf.close()
    return path


def test_pdf_keeps_real_page_numbers(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "ref.pdf", [PARAGRAPH, PARAGRAPH + "second", PARAGRAPH + "third"])

    pages = parse_document(path)

    assert [p.page for p in pages] == [1, 2, 3]
    assert "Paracetamol" in pages[0].text


def test_pdf_with_blank_middle_page_keeps_numbering(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "gap.pdf", [PARAGRAPH, "", PARAGRAPH])

    assert [p.page for p in parse_document(path)] == [1, 3]


def test_image_only_pdf_is_rejected_with_the_scanned_message(tmp_path: Path) -> None:
    pdf = pymupdf.open()
    pdf.new_page()
    pdf.new_page()
    path = tmp_path / "scan.pdf"
    pdf.save(path)
    pdf.close()

    with pytest.raises(ParseError, match=SCANNED_PDF_MESSAGE):
        parse_document(path)


def test_corrupt_pdf_is_a_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7 this is not really a pdf")

    with pytest.raises(ParseError):
        parse_document(path)


def test_docx_headings_start_new_sections(tmp_path: Path) -> None:
    document = Document()
    document.add_paragraph("Preamble before any heading.")
    document.add_heading("Headache", level=1)
    document.add_paragraph("Take rest and fluids.")
    document.add_heading("Fever", level=1)
    document.add_paragraph("Use paracetamol.")
    path = tmp_path / "guide.docx"
    document.save(str(path))

    pages = parse_document(path)

    assert [p.page for p in pages] == [1, 2, 3]
    assert "Preamble" in pages[0].text
    assert pages[1].text.startswith("Headache")
    assert "Use paracetamol." in pages[2].text


def test_docx_without_headings_is_one_section(tmp_path: Path) -> None:
    document = Document()
    document.add_paragraph("First paragraph.")
    document.add_paragraph("Second paragraph.")
    path = tmp_path / "flat.docx"
    document.save(str(path))

    assert [p.page for p in parse_document(path)] == [1]


def test_docx_table_text_is_extracted(tmp_path: Path) -> None:
    document = Document()
    document.add_paragraph("Dosage table")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Adults"
    table.rows[0].cells[1].text = "500 mg"
    path = tmp_path / "table.docx"
    document.save(str(path))

    assert "Adults | 500 mg" in parse_document(path)[0].text


def test_txt_is_a_single_page(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("# not a heading in txt\n\nSome notes.\n", encoding="utf-8")

    pages = parse_document(path)

    assert [p.page for p in pages] == [1]


def test_markdown_headings_start_new_sections(tmp_path: Path) -> None:
    path = tmp_path / "guide.md"
    path.write_text("# Cold\nRest.\n\n## Flu\nFluids.\n\nMore flu text.\n", encoding="utf-8")

    pages = parse_document(path)

    assert [p.page for p in pages] == [1, 2]
    assert pages[0].text.startswith("# Cold")
    assert "More flu text." in pages[1].text


def test_txt_falls_back_to_cp1252(tmp_path: Path) -> None:
    path = tmp_path / "legacy.txt"
    path.write_bytes("Café au lait – mild".encode("cp1252"))  # noqa: RUF001

    assert "Café" in parse_document(path)[0].text


def test_unsupported_suffix_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "photo.png"
    path.write_bytes(b"\x89PNG")

    with pytest.raises(ParseError, match="unsupported file type"):
        parse_document(path)


def test_empty_text_file_has_no_extractable_text(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("  \n\n ", encoding="utf-8")

    with pytest.raises(ParseError, match=NO_TEXT_MESSAGE):
        parse_document(path)


def test_suffix_match_is_case_insensitive(tmp_path: Path) -> None:
    path = tmp_path / "UPPER.TXT"
    path.write_text("hello world", encoding="utf-8")

    assert parse_document(path)[0].text == "hello world"


def test_normalise_text_collapses_runs_but_keeps_paragraphs() -> None:
    assert normalise_text("a   b\t c\n\n\n\nd  e") == "a b c\n\nd e"  # noqa: RUF001
