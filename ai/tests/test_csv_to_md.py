"""CSV -> Markdown converter for knowledge-base uploads (CLAUDE.md §1, §12)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_service.config import Settings
from ai_service.ingestion.chunker import chunk_document
from ai_service.ingestion.csv_to_md import CsvConversionError, convert, main
from ai_service.ingestion.parsers import parse_markdown

DISEASES = (
    "Disease,Symptoms,Treatment\n"
    "Malaria,Fever and chills,Antimalarial drugs\n"
    "Common cold,Runny nose,Rest and fluids\n"
)


def test_each_row_becomes_its_own_section() -> None:
    assert convert(DISEASES) == (
        "## Malaria\n\n"
        "- **Symptoms:** Fever and chills\n"
        "- **Treatment:** Antimalarial drugs\n\n"
        "## Common cold\n\n"
        "- **Symptoms:** Runny nose\n"
        "- **Treatment:** Rest and fluids\n"
    )


def test_rows_sharing_a_title_are_merged_with_distinct_values() -> None:
    csv_text = (
        "Disease,Symptom\n"
        "Malaria,fever\n"
        "Malaria,chills\n"
        "malaria,fever\n"  # same title, different case: still the same entry
        "Dengue,rash\n"
    )

    markdown = convert(csv_text)

    assert markdown.count("## ") == 2
    assert "- **Symptom:** fever; chills\n" in markdown


def test_semicolon_csv_with_an_excel_bom_and_blank_cells() -> None:
    csv_text = "﻿Disease;Symptoms;Notes\nFlu;Fever;\n\n;Cough;orphan row\n".lstrip("﻿")

    markdown = convert(csv_text)

    assert "## Flu\n\n- **Symptoms:** Fever\n" in markdown
    assert "Notes" not in markdown.split("## Row 2")[0]  # an empty cell adds no line
    assert "## Row 2" in markdown  # a row with no title still gets a heading


def test_a_chosen_title_column_and_cell_cleanup() -> None:
    csv_text = 'Id,Name,Advice\n1,"# Migraine","Rest in a\ndark room"\n'

    markdown = convert(csv_text, title_column="Name")

    assert markdown.startswith("## Migraine\n")  # the stray '#' cannot change the level
    assert "- **Id:** 1\n- **Advice:** Rest in a dark room\n" in markdown


def test_numbered_columns_merge_and_underscores_can_become_spaces() -> None:
    csv_text = (
        "Disease,Symptom_1,Symptom_2,Symptom 3,Precaution_1\n"
        "Malaria,chills,high_fever,,use_mosquito_net\n"
        "Malaria,vomiting,chills,,\n"
    )

    plain = convert(csv_text)
    spaced = convert(csv_text, underscores_to_spaces=True)

    assert "- **Symptom:** chills; high_fever; vomiting\n" in plain
    assert "- **Precaution_1:** use_mosquito_net\n" in plain  # a lone numbered column is kept
    assert "- **Symptom:** chills; high fever; vomiting\n" in spaced
    assert "- **Precaution_1:** use mosquito net\n" in spaced


def test_table_layout_escapes_pipes_and_pads_short_rows() -> None:
    markdown = convert("A,B,C\nx|y,2\n", layout="table")

    assert markdown == "| A | B | C |\n|---|---|---|\n| x\\|y | 2 |  |\n"


@pytest.mark.parametrize(
    ("csv_text", "message"),
    [("", "empty"), ("Disease,Symptoms\n", "no data rows"), ("\n\n", "empty")],
)
def test_unusable_csvs_are_rejected(csv_text: str, message: str) -> None:
    with pytest.raises(CsvConversionError, match=message):
        convert(csv_text)


def test_an_unknown_title_column_names_the_real_ones() -> None:
    with pytest.raises(CsvConversionError, match="columns are: Disease, Symptoms, Treatment"):
        convert(DISEASES, title_column="Illness")


def test_the_output_parses_into_one_citable_section_per_entry(tmp_path: Path) -> None:
    """Through the real parser and chunker: entry N is section ("page") N."""
    source = tmp_path / "diseases.md"
    source.write_text(convert(DISEASES), encoding="utf-8")

    pages = parse_markdown(source)
    parents, children = chunk_document(1, pages, Settings(_env_file=None))

    assert [p.page for p in pages] == [1, 2]
    assert pages[1].text.startswith("## Common cold")
    assert children
    assert "Antimalarial drugs" in parents[0].text


def test_the_command_writes_next_to_the_csv_and_refuses_to_overwrite(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "diseases.csv"
    source.write_text(DISEASES, encoding="utf-8")

    assert main([str(source)]) == 0
    assert (tmp_path / "diseases.md").read_text(encoding="utf-8").startswith("## Malaria")
    assert "2 sections" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        main([str(source)])
    assert main([str(source), "--force", "--layout", "table"]) == 0
    assert (tmp_path / "diseases.md").read_text(encoding="utf-8").startswith("| Disease |")


def test_the_command_rejects_a_non_markdown_output(tmp_path: Path) -> None:
    source = tmp_path / "d.csv"
    source.write_text(DISEASES, encoding="utf-8")

    with pytest.raises(SystemExit):
        main([str(source), "--out", str(tmp_path / "d.txt")])
