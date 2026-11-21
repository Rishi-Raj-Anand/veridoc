"""Document parsing (CLAUDE.md §6.1.3).

Turns an uploaded file into ``PageText`` units. PDFs keep their real page numbers; for
DOCX, TXT and MD the "page" is a heading-based section index (section 1 until the first
heading, then one per heading), or simply 1 when the file has no headings. A CSV is converted
to Markdown first (one section per entry, ``csv_to_md``), so its "page" N is the N-th entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from docx import Document as load_docx

from ai_service.ingestion.csv_to_md import CsvConversionError, convert

SUPPORTED_SUFFIXES = frozenset({".pdf", ".txt", ".md", ".docx", ".csv"})

# A PDF averaging fewer non-whitespace characters per page than this is treated as
# scanned / image-only. We do not OCR (§1).
MIN_CHARS_PER_PDF_PAGE = 50

SCANNED_PDF_MESSAGE = "scanned or image-only PDF is not supported"
NO_TEXT_MESSAGE = "document contains no extractable text"

_MD_HEADING = re.compile(r"^#{1,2}\s+\S")
_DOCX_HEADING_STYLES = frozenset({"heading 1", "heading 2", "title"})

# Invisible characters (zero-width space, word joiner, BOM) that word processors such as
# Google Docs scatter through exported text. They only add noise to embeddings. ZWJ/ZWNJ are
# deliberately kept: they carry meaning in some scripts.
_INVISIBLE = dict.fromkeys((0x200B, 0x2060, 0xFEFF))


class ParseError(ValueError):
    """The file cannot be turned into text. The message is safe to show the admin."""


@dataclass(frozen=True)
class PageText:
    page: int
    text: str


def normalise_text(text: str) -> str:
    """Collapse whitespace runs but keep blank-line paragraph breaks."""
    text = text.translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    joined = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


def _pages_from_sections(sections: list[list[str]]) -> list[PageText]:
    pages: list[PageText] = []
    for section in sections:
        text = normalise_text("\n".join(section))
        if text:
            pages.append(PageText(page=len(pages) + 1, text=text))
    return pages


def parse_pdf(path: Path) -> list[PageText]:
    try:
        pdf = pymupdf.open(path)
    except Exception as exc:
        raise ParseError(f"could not open PDF: {exc}") from exc
    with pdf:
        if pdf.needs_pass:
            raise ParseError("password-protected PDF is not supported")
        page_count = pdf.page_count
        pages: list[PageText] = []
        non_space_chars = 0
        for index in range(page_count):
            raw = pdf[index].get_text("text", sort=True)
            non_space_chars += len(re.sub(r"\s", "", raw))
            text = normalise_text(raw)
            if text:
                pages.append(PageText(page=index + 1, text=text))
    if page_count == 0 or non_space_chars / page_count < MIN_CHARS_PER_PDF_PAGE:
        raise ParseError(SCANNED_PDF_MESSAGE)
    return pages


def parse_docx(path: Path) -> list[PageText]:
    try:
        document = load_docx(str(path))
    except Exception as exc:
        raise ParseError(f"could not open DOCX: {exc}") from exc
    sections: list[list[str]] = [[]]
    for paragraph in document.paragraphs:
        style = (paragraph.style.name if paragraph.style is not None else "").lower()
        if style in _DOCX_HEADING_STYLES and paragraph.text.strip() and sections[-1]:
            sections.append([])
        sections[-1].append(paragraph.text)
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                sections[-1].append(" | ".join(cells))
    return _pages_from_sections(sections)


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def parse_txt(path: Path) -> list[PageText]:
    return _pages_from_sections([_read_text(path).splitlines()])


def _markdown_pages(text: str) -> list[PageText]:
    sections: list[list[str]] = [[]]
    for line in text.splitlines():
        if _MD_HEADING.match(line) and sections[-1]:
            sections.append([])
        sections[-1].append(line)
    return _pages_from_sections(sections)


def parse_markdown(path: Path) -> list[PageText]:
    return _markdown_pages(_read_text(path))


def parse_csv(path: Path) -> list[PageText]:
    """One section per entry. Underscores become spaces: "high_fever" embeds as "high fever"."""
    try:
        markdown = convert(_read_text(path), underscores_to_spaces=True)
    except CsvConversionError as exc:
        raise ParseError(f"CSV could not be converted: {exc}") from exc
    return _markdown_pages(markdown)


_PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".txt": parse_txt,
    ".md": parse_markdown,
    ".csv": parse_csv,
}


def parse_document(path: Path) -> list[PageText]:
    """Parse ``path`` by extension. Raises ``ParseError`` with a user-safe message."""
    suffix = path.suffix.lower()
    parser = _PARSERS.get(suffix)
    if parser is None:
        raise ParseError(f"unsupported file type '{suffix or path.name}'")
    pages = parser(path)
    if not pages:
        raise ParseError(NO_TEXT_MESSAGE)
    return pages
