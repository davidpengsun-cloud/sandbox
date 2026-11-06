"""Every reader's declared formats onboard through the same certification and passage path, offline."""
from __future__ import annotations

import base64
import json

import pytest
import yaml

from kge.adapters import docreader
from kge.adapters.docreader.gemini import GeminiReader
from kge.adapters.docreader.text import TextReader
from kge.adapters.model import providers
from kge.adapters.model.errors import ExternalCallError, NoFallback
from kge.estate import certify, from_files
from kge.pipeline.tagger import RuleTagger
from tests.document_samples import CHART, TABLE, TEXT, pdf, sample
from tests.unit.test_from_files import DOC, fixture


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "none")
    monkeypatch.setenv("DOCUMENT_READER", "text")
    for name in ("MODEL_HIGH_PROVIDER", "MODEL_MEDIUM_PROVIDER", "MODEL_LOW_PROVIDER", "READER_MODEL_TIER", "STORE_URL", "TMP_URL",
                 "WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "GRAPH_STORE"):
        monkeypatch.delenv(name, raising=False)

    def refused(*args, **kwargs):
        pytest.fail("a document test reached a real model transport")
    monkeypatch.setattr(providers.LLM, "_post", staticmethod(refused))


@pytest.fixture
def vertex(monkeypatch):
    """Exercise the real model_for seam and LLM/Chat path with only the Vertex transport stood in."""
    from kge.adapters.docreader import gemini
    llm = providers.LLM("vertex-gemini", "stand-in", project="your-project-id", region="global")
    monkeypatch.setattr(providers.Chat, "token", lambda self: ("stand-in", "your-project-id"))
    selected, calls, replies = [], [], []

    def model_for(agent):
        selected.append(agent)
        return llm

    def post(url, headers, body):
        part = body["contents"][0]["parts"][1]
        data = part.get("inlineData") or part["fileData"]
        calls.append((url, data, body["generationConfig"]["maxOutputTokens"]))
        mime = data["mimeType"]
        finish = "STOP"
        if replies:
            pages = replies.pop(0)
            if isinstance(pages, dict):
                finish, pages = pages["finishReason"], pages["pages"]
        elif mime == "text/plain" and "data" in data:
            pages = [{"markdown": base64.b64decode(data["data"]).decode(), "method": "text-layer"}]
        elif mime == "text/plain":
            pages = [{"markdown": TEXT, "method": "text-layer"}]
        elif mime == "application/pdf":
            pages = [{"markdown": TEXT + "\n\n" + TABLE + "\n\n" + CHART, "method": "text-layer"},
                     {"markdown": "Scanned page: " + TEXT, "method": "model"}]
        else:
            pages = [{"markdown": CHART, "method": "model"}]
        raw = pages if isinstance(pages, str) else json.dumps({"pages": pages})
        return {"modelVersion": "stand-in", "candidates": [{"content": {"parts": [{"text": raw}]}, "finishReason": finish}]}

    monkeypatch.setattr(gemini, "model_for", model_for)
    monkeypatch.setattr(providers.LLM, "_post", staticmethod(post))
    return calls, selected, replies


def add(fx, format, content=None, identifier="READING"):
    path = fx / "docs/certified" / f"{identifier}.{format}"
    metadata = yaml.safe_load(DOC.split("---")[1])
    metadata.pop("certification")
    if format == "md":
        path.write_text("---\n" + yaml.safe_dump(metadata) + "---\n" + (content or sample(format)).decode())
    else:
        path.write_bytes(sample(format) if content is None else content)
        path.with_name(path.name + ".yaml").write_text(yaml.safe_dump(metadata))
    return path


FORMATS = [(reader.name, format) for reader in (TextReader(), GeminiReader()) for format in reader.formats]


@pytest.mark.parametrize("format", ["heic", "heif"])
def test_heif_samples_contain_a_real_encoded_image(format):
    content = sample(format)
    assert content[4:8] == b"ftyp" and b"mif1" in content[:36]
    assert all(box in content for box in (b"meta", b"hvcC", b"mdat"))
    assert len(content) > 100


@pytest.mark.parametrize("format", ["html", "htm"])
def test_nested_html_table_preserves_inner_and_outer_cells(format):
    content = (b"<table><tr><td>Outer before<table><tr><td>Inner</td></tr></table>"
               b"Outer after</td><td>Second cell</td></tr></table>")
    text = TextReader().read(content, format).markdown
    positions = [text.index(part) for part in ("Outer before", "Inner", "Outer after", "Second cell")]
    assert positions == sorted(positions)


@pytest.mark.parametrize("format", ["html", "htm"])
def test_html_table_cell_spans_keep_values_in_their_columns(format, tmp_path):
    raw = (b"<table><tr><th>Region</th><th colspan=2>Revenue</th></tr>"
           b"<tr><td rowspan=2>North</td><td>10</td><td>11</td></tr>"
           b"<tr><td>20</td><td>21</td></tr></table>")
    text = TextReader().read(raw, format).markdown
    assert "| Region | Revenue | Revenue |" in text
    assert "| North | 10 | 11 |\n| North | 20 | 21 |" in text
    fx = fixture(tmp_path)
    add(fx, format, content=raw)
    out = tmp_path / "estate"
    from_files.build(fx, out, "shop")
    doc = next(d for d in json.loads((out / "documents.json").read_text()) if d["doc_id"] == "READING")
    assert "| North | 10 | 11 |\n| North | 20 | 21 |" in "\n".join(certify.chunks(out, doc))


def test_html_spans_respect_row_groups_and_zero_extends_to_group_end():
    raw = (b'<table><thead><tr><th rowspan=" 2 ">Region</th><th>Revenue</th></tr></thead>'
           b'<tbody><tr><td rowspan=" 0 ">North</td><td>10</td></tr>'
           b'<tr><td>20</td></tr></tbody><tbody><tr><td>South</td><td>30</td></tr></tbody></table>')
    text = TextReader().read(raw, "html").markdown
    assert "| Region | Revenue |" in text
    assert "| North | 10 |\n| North | 20 |\n| South | 30 |" in text


def test_html_whitespace_in_span_values_is_respected():
    raw = (b'<table><tr><th>Region</th><th>Revenue</th></tr>'
           b'<tr><td rowspan=" 2 ">North</td><td>10</td></tr><tr><td>20</td></tr></table>')
    assert "| North | 10 |\n| North | 20 |" in TextReader().read(raw, "html").markdown


def test_html_block_elements_do_not_concatenate_words():
    raw = b"<dl><dt>Revenue</dt><dd>42</dd><dt>Profit</dt><dd>10</dd></dl><h4>First</h4><h4>Second</h4>"
    text = TextReader().read(raw, "html").markdown
    assert "Revenue\n\n42\n\nProfit\n\n10" in text
    assert "First\n\nSecond" in text


def test_nested_html_captions_stay_with_their_table_cells():
    raw = (b"<table><tr><td>Q1<table><caption>Revenue</caption><tr><td>42</td></tr></table></td>"
           b"<td>Q2<table><caption>Profit</caption><tr><td>10</td></tr></table></td></tr></table>")
    text = TextReader().read(raw, "html").markdown
    assert text.index("Q1") < text.index("Revenue") < text.index("42") < text.index("Q2") < text.index("Profit") < text.index("10")


@pytest.mark.parametrize("provider,format", FORMATS)
def test_every_declared_format_onboards_with_tagged_passages(tmp_path, monkeypatch, vertex, provider, format):
    monkeypatch.setenv("DOCUMENT_READER", provider)
    fx = fixture(tmp_path)
    add(fx, format)
    out = tmp_path / "estate"
    from_files.build(fx, out, "shop")
    index = json.loads((out / "documents.json").read_text())
    doc = next(d for d in index if d["doc_id"] == "READING")
    chunks = certify.chunks(out, doc)
    assert chunks
    grade = certify.certify(doc, chunks)
    assert (grade["ai_ready"], grade["tier"]) == ("yes", "gold")
    assert all(RuleTagger([]).propose(chunk, "certified", "metric_definition") for chunk in chunks)
    landed, rows, _ = certify.land(out, "shop", role_mailboxes=frozenset())
    passages = [json.loads(line) for line in landed.text("documents/chunks.jsonl").splitlines()]
    assert [p for p in passages if p["doc_id"] == "READING"]
    text = (out / doc["path"]).read_text()
    if format != "md":
        assert "<!-- page: 1; read:" in text
        assert (out / "originals" / doc["original"]).read_bytes() == sample(format)
    if format in {"html", "htm", "pdf", "csv", "tsv"}:
        assert "| Period | Takings |" in text and "| June | 42 |" in text
        assert "| Period | Takings |" in "\n".join(chunks) and "| June | 42 |" in "\n".join(chunks)
    if format in {"html", "htm", "pdf"}:
        assert CHART in text
    if format == "pdf":
        assert "<!-- page: 2; read: model -->" in text
    if provider == "gemini" and format != "md":
        assert vertex[0] and set(vertex[1]) == {"reader"}
        assert "/publishers/google/models/stand-in:generateContent" in vertex[0][0][0]


def test_converted_compact_table_uses_existing_chunker(tmp_path):
    fx = fixture(tmp_path)
    add(fx, "txt", content=b"# Confidentiality rule\n\nKeep this information safe.\n\n| A | B |\n|---|---|\n| 10 | 20 |")
    out = tmp_path / "estate"
    from_files.build(fx, out, "shop")
    doc = next(d for d in json.loads((out / "documents.json").read_text()) if d["doc_id"] == "READING")
    chunks = "\n".join(certify.chunks(out, doc))
    assert "Confidentiality rule" not in chunks
    assert "| 10 | 20 |" not in chunks


def test_compact_table_rows_are_removed_for_both_formats(tmp_path):
    fx = fixture(tmp_path)
    body = b"| A | B |\n|---|---|\n| 10 | 20 |"
    add(fx, "txt", content=body, identifier="TEXT")
    add(fx, "md", content=body, identifier="MARKDOWN")
    out = tmp_path / "estate"
    from_files.build(fx, out, "shop")
    docs = {d["doc_id"]: d for d in json.loads((out / "documents.json").read_text())}
    assert "| 10 | 20 |" not in "\n".join(certify.chunks(out, docs["TEXT"]))
    assert "| 10 | 20 |" not in "\n".join(certify.chunks(out, docs["MARKDOWN"]))
    rows = {d["asset_id"]: d for d in certify.certify_estate(out)}
    assert {k: v for k, v in rows["TEXT"].items() if k != "asset_id"} == {k: v for k, v in rows["MARKDOWN"].items() if k != "asset_id"}


@pytest.mark.parametrize("provider", ["text", "gemini"])
def test_converted_text_has_exactly_markdowns_certification_and_chunks(tmp_path, monkeypatch, vertex, provider):
    monkeypatch.setenv("DOCUMENT_READER", provider)
    fx = fixture(tmp_path)
    for format in ("txt", "md"):
        add(fx, format, identifier=format.upper())
    out = tmp_path / "estate"
    from_files.build(fx, out, "shop")
    docs = {d["doc_id"]: d for d in json.loads((out / "documents.json").read_text())}
    assert certify.chunks(out, docs["TXT"]) == certify.chunks(out, docs["MD"])
    rows = {d["asset_id"]: d for d in certify.certify_estate(out)}
    assert {k: v for k, v in rows["TXT"].items() if k != "asset_id"} == {k: v for k, v in rows["MD"].items() if k != "asset_id"}


@pytest.mark.parametrize("identity", ["text-v1", "text-v2", "text-v3"])
def test_corrected_html_is_read_again_instead_of_reusing_old_cache(tmp_path, identity):
    from kge.adapters.objstore import open_store
    from kge.ports import DocumentPage, DocumentReading

    class OldTextReader:
        def identity(self):
            return identity

        def read(self, content, format, name=""):
            return DocumentReading((DocumentPage("old incomplete reading", "text-layer"),))

    content = b"<table><tr><th>Period<th>Takings<tr><td>June<td>42</table>"
    store = open_store(str(tmp_path / "readings"))
    assert docreader.read_once(OldTextReader(), store, content, "html").pages[0].markdown == "old incomplete reading"
    fresh = docreader.read_once(TextReader(), store, content, "html")
    assert "| Period | Takings |\n| --- | --- |\n| June | 42 |" in fresh.markdown
    assert len(list((tmp_path / "readings").rglob("*.json"))) == 2


def test_kept_output_is_by_content_and_provider_through_the_store_port(tmp_path, monkeypatch, vertex):
    fx = fixture(tmp_path)
    add(fx, "txt")
    out = tmp_path / "estate"
    (fx.parent / "pack.yaml").write_text("manifest: {client: shop}\nterminology: {}\n")
    remote = tmp_path / "objects"
    monkeypatch.setenv("STORE_URL", str(remote))
    monkeypatch.setenv("DOCUMENT_READER", "gemini")
    from_files.build(fx, out, "shop")
    original = (out / "docs/certified/READING.md").read_bytes()
    assert len(vertex[0]) == 1
    unchanged = from_files.build(fx, out, "shop", if_changed=True)
    assert unchanged["rebuilt"] is False and unchanged["why"] == "unchanged"
    add(fx, "txt", identifier="COPY")
    from_files.build(fx, out, "shop")
    assert len(vertex[0]) == 1
    assert (out / "docs/certified/READING.md").read_bytes() == original
    assert len(list(remote.glob("readings/shop/**/*.json"))) == 1
    monkeypatch.setenv("DOCUMENT_READER", "text")
    changed = from_files.build(fx, out, "shop", if_changed=True, defer_landing=True)
    assert changed["rebuilt"] is True and changed["why"] == "reader-changed"
    assert len(list(remote.glob("readings/shop/**/*.json"))) == 2


def test_scan_without_a_text_layer_is_read_by_the_model(tmp_path, monkeypatch, vertex):
    monkeypatch.setenv("DOCUMENT_READER", "gemini")
    vertex[2].append([{"markdown": "A scanned page. " + TEXT, "method": "model"}] * 2)
    fx = fixture(tmp_path)
    add(fx, "pdf", pdf(scan=True))
    out = tmp_path / "estate"
    from_files.build(fx, out, "shop")
    text = (out / "docs/certified/READING.md").read_text()
    assert "<!-- page: 1; read: model -->" in text and "<!-- page: 2; read: model -->" in text
    assert "A scanned page." in text


@pytest.mark.parametrize("reply", ["not JSON", "{}", '{"pages":[]}', '{"pages":[{"markdown":"x","method":"guess"}]}'])
def test_malformed_model_reading_stops_the_pilot_without_fallback(tmp_path, monkeypatch, vertex, reply):
    monkeypatch.setenv("DOCUMENT_READER", "gemini")
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    vertex[2].append(reply)
    with pytest.raises(NoFallback, match="document reader"):
        GeminiReader().read(sample("txt"), "txt")


def test_failed_vertex_reading_stops_the_pilot_without_text_fallback(monkeypatch, vertex):
    monkeypatch.setenv("GRAPH_STORE", "bigquery")

    def failed(*args):
        raise ExternalCallError("Vertex AI unavailable")
    monkeypatch.setattr(providers.LLM, "_post", staticmethod(failed))
    with pytest.raises(NoFallback, match="Vertex AI unavailable"):
        GeminiReader().read(sample("txt"), "txt")


def test_unsupported_file_is_retained_with_no_passages_and_reported(tmp_path):
    fx = fixture(tmp_path)
    raw = b"PK\x03\x04original word export\x00\xff"
    path = add(fx, "docx", raw)
    out = tmp_path / "estate"
    result = from_files.build(fx, out, "shop")
    doc = next(d for d in json.loads((out / "documents.json").read_text()) if d["doc_id"] == "READING")
    assert (out / "originals" / doc["original"]).read_bytes() == raw
    doc["certification"] = {"ai_ready": "yes", "tier": "gold"}
    assert certify.chunks(out, doc) == []
    evidence = "content not read: docx isn't supported by text"
    assert certify.certify(doc, ["ignored"])["evidence"] == evidence
    landed, rows, _ = certify.land(out, "shop")
    grade = next(row for row in rows if row["asset_id"] == "READING")
    assert grade["ai_ready"] == "no" and grade["tier"] is None
    assert not any(json.loads(line)["doc_id"] == "READING" for line in landed.text("documents/chunks.jsonl").splitlines())
    assert result["unread_documents"] == [{"path": path.relative_to(fx).as_posix(), "evidence": evidence}]
    from kge.services.onboard.service import change_lines
    assert any(evidence in line for line in change_lines({"estate": result}))


@pytest.mark.parametrize("format", ["html", "htm"])
def test_html_table_preserves_cells_with_optional_closing_tags(format):
    raw = b"<table><thead><tr><th>Period<th>Takings</thead><tbody><tr><td>June<td>42</tbody></table>"
    text = TextReader().read(raw, format).markdown
    assert "| Period | Takings |\n| --- | --- |\n| June | 42 |" in text


def test_text_pages_preserve_tables_and_do_not_execute_html():
    raw = "<p>Words exactly.</p><script>secret instructions</script><table><tr><td>A</td><td>B</td></tr></table><img alt='Chart rises'>"
    reading = TextReader().read((raw + "\f<p>Page two.</p>").encode(), "html")
    assert len(reading.pages) == 2
    assert "secret instructions" not in reading.markdown
    assert "| A | B |" in reading.markdown and "[Picture: Chart rises]" in reading.markdown
    assert "<!-- page: 2; read: text-layer -->" in reading.markdown


def test_unknown_reader_is_refused(monkeypatch):
    monkeypatch.setenv("DOCUMENT_READER", "typo")
    with pytest.raises(ValueError, match="DOCUMENT_READER"):
        docreader.reader()


def test_both_guides_state_the_clients_responsibility():
    from kge.pipeline.settings import ROOT
    for name in ("kit/INSTALL.md", "ui/onboarding.md"):
        text = (ROOT / name).read_text()
        assert "no personal or sensitive data" in text
        assert "client's responsibility" in text


def test_upfront_checks_sidecars_without_reading_content(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from kge.estate import upfront
    fx = fixture(tmp_path)
    path = add(fx, "txt")
    ctx = SimpleNamespace(fixture=fx, pack=SimpleNamespace(folder=fx.parent), prof=SimpleNamespace(estate=lambda: tmp_path / "estate"))
    def refused(*args, **kwargs):
        pytest.fail("up-front checks must not convert or call a model")
    monkeypatch.setattr(TextReader, "read", refused)
    assert upfront.documents(ctx) == []
    path.with_name(path.name + ".yaml").unlink()
    findings = upfront.documents(ctx)
    assert len(findings) == 1 and findings[0].rule == "F08" and "READING.txt" in findings[0].path


def test_converted_document_uses_the_pilots_estate_port(tmp_path, monkeypatch, vertex):
    from tests import bigquery_fake
    from kge.adapters.warehouse import open_estate
    fake = bigquery_fake.install(monkeypatch)
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    monkeypatch.setenv("DOCUMENT_READER", "gemini")
    fx = fixture(tmp_path)
    add(fx, "txt")
    out = tmp_path / "estate"
    from_files.build(fx, out, "shop")
    assert not list(out.rglob("*.duckdb"))
    estate = open_estate(out / "warehouses", "shop")
    landed, rows, _ = certify.land(out, "shop", estate=estate)
    assert next(r for r in rows if r["asset_id"] == "READING")["tier"] == "gold"
    assert "Takings are what the shop sold" in landed.text("documents/READING.md")
    assert fake.loads


@pytest.mark.parametrize("tier", [None, "high", "medium", "low", "default"])
def test_reader_uses_its_own_medium_tier_and_override(monkeypatch, tier):
    monkeypatch.setenv("MODEL_NAME", "offline")
    monkeypatch.setenv("MODEL_PROVIDER", "mock")
    monkeypatch.setenv("ONBOARD_MODEL_TIER", "high")
    for name in ("high", "medium", "low"):
        monkeypatch.setenv(f"MODEL_{name.upper()}_PROVIDER", "mock")
        monkeypatch.setenv(f"MODEL_{name.upper()}_NAME", f"offline-{name}")
    if tier is not None:
        monkeypatch.setenv("READER_MODEL_TIER", tier)
    selected = providers.model_for("reader")
    expected = "medium" if tier is None else tier
    assert selected.tier == expected
    assert providers.model_for("onboard").tier == "high"


@pytest.mark.parametrize("format", ["heic", "heif"])
def test_heic_and_heif_samples_have_their_own_iso_bmff_brand(format):
    import struct
    content = sample(format)
    size, kind, brand, minor = struct.unpack(">I4s4sI", content[:16])
    assert size == 36 and kind == b"ftyp" and minor == 0
    assert brand == (b"heic" if format == "heic" else b"mif1")
    assert b"heic" in content[16:size] and b"mif1" in content[16:size]


def test_reader_identity_distinguishes_effective_default_and_explicit_model_budget(tmp_path, monkeypatch, vertex):
    from kge.adapters.docreader import gemini
    from kge.adapters.objstore import open_store

    monkeypatch.delenv("MODEL_MAX_TOKENS", raising=False)
    monkeypatch.delenv("MODEL_MEDIUM_MAX_TOKENS", raising=False)
    default = providers.LLM("vertex-gemini", "stand-in", project="your-project-id", region="global", tier="medium")
    explicit = providers.LLM("vertex-gemini", "stand-in", project="your-project-id", region="global", tier="medium", max_tokens=8192)
    assert default.identity()["config_hash"] == explicit.identity()["config_hash"]
    current = [default]
    monkeypatch.setattr(gemini, "model_for", lambda agent: current[0])
    reader = GeminiReader("shop")
    store = open_store(str(tmp_path / "readings"))
    first = reader.identity()
    docreader.read_once(reader, store, sample("pdf"), "pdf")
    current[0] = explicit
    assert reader.identity() != first
    docreader.read_once(reader, store, sample("pdf"), "pdf")
    assert [call[2] for call in vertex[0]] == [65536, 8192]
    assert len(list((tmp_path / "readings").rglob("*.json"))) == 2


@pytest.mark.parametrize("configured", [None, "MODEL_MAX_TOKENS", "MODEL_MEDIUM_MAX_TOKENS"])
def test_the_reader_asks_for_a_whole_documents_budget_unless_one_is_configured(monkeypatch, vertex, configured):
    from kge.adapters.docreader.gemini import READER_MAX_TOKENS
    if configured:
        monkeypatch.setenv(configured, "12000")
    reader = GeminiReader("shop")
    from kge.adapters.docreader import gemini
    monkeypatch.setattr(gemini, "model_for", lambda agent: providers.LLM("vertex-gemini", "stand-in", project="your-project-id",
                                                                         region="global", tier="medium"))
    reader.read(sample("txt"), "txt")
    assert vertex[0][-1][2] == (12000 if configured else READER_MAX_TOKENS) == (12000 if configured else 65536)


def test_a_reading_cut_off_at_its_budget_is_no_fallback_naming_the_document_and_the_setting(monkeypatch, vertex):
    monkeypatch.setenv("GRAPH_STORE", "bigquery")
    from kge.adapters.docreader import gemini
    monkeypatch.setattr(gemini, "model_for", lambda agent: providers.LLM("vertex-gemini", "stand-in", project="your-project-id",
                                                                         region="global", tier="medium"))
    vertex[2].append({"finishReason": "MAX_TOKENS", "pages": [{"markdown": "half a page", "method": "text-layer"}]})
    with pytest.raises(NoFallback, match=r"docs/certified/LONG\.pdf: the reading reached its 65536-token reply budget and was cut "
                                         r"off; raise MODEL_MEDIUM_MAX_TOKENS"):
        GeminiReader("shop").read(sample("pdf"), "pdf", "docs/certified/LONG.pdf")


def test_a_reading_cut_off_on_the_local_stack_is_refused_never_kept(tmp_path, monkeypatch, vertex):
    monkeypatch.setenv("DOCUMENT_READER", "gemini")
    vertex[2].append({"finishReason": "MAX_TOKENS", "pages": [{"markdown": "half a page", "method": "text-layer"}]})
    fx = fixture(tmp_path)
    add(fx, "pdf")
    with pytest.raises(ExternalCallError, match="cut off"):
        from_files.build(fx, tmp_path / "estate", "shop")
    assert not list((tmp_path / "estate").rglob("readings/**/*.json"))


@pytest.fixture
def tmp_bucket(monkeypatch):
    import os
    import uuid
    from kge.adapters.objstore import gcs as gcs_backend
    from tests.gcs_stub import serve
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, ",".join(x for x in (os.environ.get(name, ""), "127.0.0.1", "localhost") if x))
    monkeypatch.setattr(gcs_backend, "BACKOFF", 0.01)
    bucket = f"t{uuid.uuid4().hex[:12]}"
    monkeypatch.setenv("TMP_URL", f"gs://{bucket}")
    with serve() as srv:
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", f"127.0.0.1:{srv.server_address[1]}")
        yield bucket, srv


def test_a_file_over_the_inline_limit_goes_by_its_scratch_uri_and_is_deleted_after(monkeypatch, vertex, tmp_bucket):
    from kge.adapters.docreader import gemini
    bucket, srv = tmp_bucket
    monkeypatch.setattr(gemini, "INLINE_MAX_BYTES", len(gemini.PROMPT) + 8)
    reading = GeminiReader("shop").read(sample("pdf"), "pdf", "docs/certified/BIG.pdf")
    assert "<!-- page: 1; read: text-layer -->" in reading.markdown
    sent = vertex[0][-1][1]
    assert sent["mimeType"] == "application/pdf" and sent["fileUri"].startswith(f"gs://{bucket}/tmp/shop/readings/")
    assert sent["fileUri"].endswith(".pdf") and "data" not in sent
    methods = [method for method, path, _ in srv.seen]
    assert "POST" in methods and "DELETE" in methods, srv.seen
    assert not srv.buckets.get(bucket), "the scratch copy is deleted after the call"
    monkeypatch.setattr(gemini, "INLINE_MAX_BYTES", 20 * 1024 * 1024)
    GeminiReader("shop").read(sample("txt"), "txt")
    assert "data" in vertex[0][-1][1] and "fileUri" not in vertex[0][-1][1], "a file under the limit stays inline"


def test_a_file_over_the_inline_limit_with_a_local_scratch_area_is_refused_naming_the_limit(tmp_path, monkeypatch, vertex):
    from kge.adapters.docreader import gemini
    monkeypatch.setenv("TMP_URL", str(tmp_path / "scratch"))
    monkeypatch.setattr(gemini, "INLINE_MAX_BYTES", len(gemini.PROMPT) + 8)
    with pytest.raises(ExternalCallError, match="docs/certified/BIG.pdf is over Vertex AI's 0 MB inline request limit"):
        GeminiReader("shop").read(sample("pdf"), "pdf", "docs/certified/BIG.pdf")
    assert vertex[0] == []
