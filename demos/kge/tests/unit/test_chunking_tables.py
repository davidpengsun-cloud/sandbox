"""Body tables remain in passages and are screened on both document paths."""

import pytest

from kge.estate import certify
from kge.pipeline.chunking import document_chunks, strip_header
from kge.pipeline.settings import ROOT


HEADER = "# Example\n\n| | |\n|---|---|\n| Shelf | certified |\n| Document | D1 |\n| Kind | policy |\n"


def test_metadata_only_is_stripped_and_two_and_three_column_tables_remain():
    body = ("\nFirst paragraph.\n\n| A | B |\n|---|---|\n| one | two |\n\n"
            "| A | B | C |\n|---|---|---|\n| three | four | five |")
    chunks = document_chunks("Example", HEADER + body)
    assert "\n\n".join(chunks) == "Example\n\n" + body.strip()
    assert "Shelf" not in "\n\n".join(chunks)


@pytest.mark.parametrize("separator", ["|---|---|", "| --- | --- |"])
@pytest.mark.parametrize("prefix", ["", "\n", "\n<!-- page: 1; read: model -->\n", "\n<!-- page: 1; read: text-layer -->\n"])
def test_table_directly_after_metadata_and_page_markers(separator, prefix):
    body = f"| Name | Contact |\n{separator}\n| A | jane@example.com |"
    assert strip_header(HEADER + prefix + body) == body
    texts = document_chunks("Example", HEADER + prefix + body)
    row = certify.certify({"doc_id": "D1", "title": "Example", "kind": "policy", "shelf": "certified"}, texts)
    assert row["ai_ready"] == "no"
    assert "personal_data:email" in row["evidence"]


@pytest.mark.parametrize("separator", ["|---|---|", "| :--- | ---: "])
def test_body_table_header_matching_an_optional_metadata_key_is_kept(separator):
    body = f"| Owner | Contact |\n{separator}\n| A | B |"
    assert strip_header(HEADER + body) == body


def test_body_table_without_generated_metadata_is_not_stripped():
    body = "| | |\n|---|---|\n| Data | Value |"
    assert strip_header("# Example\n\n" + body) == body


def test_meridian_fixture_body_table_rows_are_kept():
    docs = ROOT / "clients/meridian/fixture/docs"
    affected = [path for path in docs.rglob("*.md") if "|---|---|" in path.read_text()]
    assert len(affected) == 13
    for path in affected:
        body = path.read_text().split("---\n", 2)[2].strip()
        passages = "\n\n".join(document_chunks("Example", HEADER + "\n" + body))
        assert all(line in passages.splitlines() for line in body.splitlines() if line.startswith("|")), path.name
