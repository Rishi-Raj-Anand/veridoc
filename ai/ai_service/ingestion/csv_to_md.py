"""Convert a CSV into a Markdown document that can be uploaded to the knowledge base.

Uploads accept only PDF, TXT, MD and DOCX (CLAUDE.md §1), so tabular data is converted first.

Two layouts:

* ``sections`` (default): one ``## <title>`` section per entry, with every other column as a
  ``- **Column:** value`` line. Each entry is then its own section for retrieval, and a
  citation's "p. N" is the N-th entry.
* ``table``: one Markdown table. The chunker splits tables on row boundaries and repeats the
  header (§6.1), which suits narrow tables.

Rows that share a title (common in public datasets: one row per symptom of the same disease)
are merged in the ``sections`` layout, each column listing its distinct values once. Numbered
columns such as ``Symptom_1``, ``Symptom_2`` ... are merged into one ``Symptom`` line.
``--underscores-to-spaces`` turns ``high_fever`` into ``high fever``, which embeds better.

Usage (from ``ai/``)::

    uv run python -m ai_service.ingestion.csv_to_md diseases.csv
    uv run python -m ai_service.ingestion.csv_to_md diseases.csv --title-column Disease
    uv run python -m ai_service.ingestion.csv_to_md wide.csv --layout table --out wide.md
    uv run python -m ai_service.ingestion.csv_to_md kaggle.csv --underscores-to-spaces

Standard library only. Reads UTF-8 (a byte-order mark from Excel is fine) and detects comma,
semicolon, tab or pipe delimiters.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DELIMITERS = ",;\t|"
SNIFF_BYTES = 64 * 1024
VALUE_SEPARATOR = "; "
_NUMBERED = re.compile(r"^(.*?\S)[\s_-]*\d+$")


class CsvConversionError(ValueError):
    """The CSV cannot be turned into a useful document."""


@dataclass
class Entry:
    title: str
    values: dict[str, list[str]] = field(default_factory=dict)  # column -> distinct values

    def add(self, column: str, value: str) -> None:
        bucket = self.values.setdefault(column, [])
        if value not in bucket:
            bucket.append(value)


def clean_cell(value: str | None) -> str:
    """One line, single spaces: newlines inside a cell would break headings and tables."""
    return " ".join((value or "").split())


def _heading_text(value: str) -> str:
    # A leading '#' would change the heading level; the parser splits sections on headings.
    return value.lstrip("#").strip()


def read_rows(text: str) -> tuple[list[str], list[list[str]]]:
    """Header and data rows, with blank rows dropped and every cell cleaned."""
    sample = text[:SNIFF_BYTES]
    try:
        dialect: type[csv.Dialect] | csv.Dialect = csv.Sniffer().sniff(
            sample, delimiters=DELIMITERS
        )
    except csv.Error:
        dialect = csv.excel  # comma-separated
    rows = [
        [clean_cell(cell) for cell in row]
        for row in csv.reader(io.StringIO(text), dialect)
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        raise CsvConversionError("the CSV is empty")
    header = [name or f"Column {i + 1}" for i, name in enumerate(rows[0])]
    if len(rows) == 1:
        raise CsvConversionError("the CSV has a header row but no data rows")
    return header, rows[1:]


def merged_column_names(header: list[str]) -> list[str]:
    """``Symptom_1``, ``Symptom_2`` -> ``Symptom``, when at least two columns share the stem."""
    stems = [m.group(1) if (m := _NUMBERED.match(name)) else None for name in header]
    counts: dict[str, int] = {}
    for stem in stems:
        if stem is not None:
            counts[stem] = counts.get(stem, 0) + 1
    return [
        stem if stem is not None and counts[stem] > 1 else name
        for name, stem in zip(header, stems, strict=True)
    ]


def build_entries(header: list[str], rows: list[list[str]], title_column: str) -> list[Entry]:
    if title_column not in header:
        raise CsvConversionError(
            f"no column named {title_column!r}; columns are: {', '.join(header)}"
        )
    title_index = header.index(title_column)
    labels = merged_column_names(header)
    entries: dict[str, Entry] = {}
    for number, row in enumerate(rows, start=1):
        row = row + [""] * (len(header) - len(row))  # short rows: missing cells are empty
        title = _heading_text(row[title_index]) or f"Row {number}"
        entry = entries.setdefault(title.casefold(), Entry(title))
        for index, (label, value) in enumerate(zip(labels, row, strict=False)):
            if index != title_index and value:
                entry.add(label, value)
    return list(entries.values())


def render_sections(entries: list[Entry]) -> str:
    blocks = []
    for entry in entries:
        lines = [f"## {entry.title}", ""]
        lines += [
            f"- **{column}:** {VALUE_SEPARATOR.join(vals)}" for column, vals in entry.values.items()
        ]
        if len(lines) == 2:
            lines.append("- (no other details in the source CSV)")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _table_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def render_table(header: list[str], rows: list[list[str]]) -> str:
    width = len(header)
    lines = [
        "| " + " | ".join(_table_cell(h) for h in header) + " |",
        "|" + "---|" * width,
    ]
    for row in rows:
        cells = (row + [""] * width)[:width]
        lines.append("| " + " | ".join(_table_cell(c) for c in cells) + " |")
    return "\n".join(lines) + "\n"


def convert(
    text: str,
    *,
    layout: str = "sections",
    title_column: str | None = None,
    underscores_to_spaces: bool = False,
) -> str:
    """CSV text in, Markdown text out."""
    header, rows = read_rows(text)
    if underscores_to_spaces:
        rows = [[" ".join(cell.replace("_", " ").split()) for cell in row] for row in rows]
    if layout == "table":
        return render_table(header, rows)
    entries = build_entries(header, rows, title_column or header[0])
    return render_sections(entries)


def _output_path(source: Path, out: Path | None) -> Path:
    return out if out is not None else source.with_suffix(".md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ai_service.ingestion.csv_to_md",
        description="Convert a CSV into a Markdown document for the VeriDoc knowledge base.",
    )
    parser.add_argument("csv_file", type=Path, help="the CSV to convert")
    parser.add_argument("--out", type=Path, help="output .md path (default: next to the CSV)")
    parser.add_argument(
        "--layout",
        choices=["sections", "table"],
        default="sections",
        help="one section per entry (default) or a single Markdown table",
    )
    parser.add_argument(
        "--title-column", help="column used as each section's heading (default: the first)"
    )
    parser.add_argument(
        "--underscores-to-spaces",
        action="store_true",
        help="turn values like high_fever into high fever (better retrieval)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite an existing output file")
    args = parser.parse_args(argv)

    source: Path = args.csv_file
    target = _output_path(source, args.out)
    if target.suffix.lower() != ".md":
        parser.error("--out must end in .md")
    if target.exists() and not args.force:
        parser.error(f"{target} already exists; pass --force to overwrite it")
    try:
        text = source.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        parser.error(f"{source} does not exist")
    except UnicodeDecodeError:
        parser.error(f"{source} is not UTF-8; re-save it as 'CSV UTF-8' and try again")
    try:
        markdown = convert(
            text,
            layout=args.layout,
            title_column=args.title_column,
            underscores_to_spaces=args.underscores_to_spaces,
        )
    except CsvConversionError as exc:
        parser.error(str(exc))

    target.write_text(markdown, encoding="utf-8", newline="\n")
    entries = markdown.count("\n## ") + markdown.startswith("## ")
    summary = f"{entries} sections" if args.layout == "sections" else "1 table"
    sys.stdout.write(f"wrote {target} ({summary}, {len(markdown):,} characters)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
