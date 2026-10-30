"""The shipped notices match the runtime dependency closure."""

import importlib.util
import platform
import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("notices", DEMO / "kit" / "generate_third_party_notices.py")
NOTICES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NOTICES)


@pytest.mark.skipif(
    not (sys.platform.startswith("linux") and platform.machine() in ("x86_64", "AMD64")),
    reason="committed notices describe the image's Linux x86-64 wheels",
)
def test_committed_notices_are_current():
    assert (DEMO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8") == NOTICES.render()


def test_every_distribution_in_the_closure_appears():
    text = (DEMO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    closure = NOTICES.dependency_closure()
    assert closure
    for dist in closure:
        assert f"### {dist.metadata['Name']} {dist.version}\n" in text


def test_the_container_image_pin_comes_from_the_dockerfile(tmp_path, monkeypatch):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.12.15-slim-bookworm@sha256:" + "b" * 64 + "\n", encoding="utf-8")
    monkeypatch.setattr(NOTICES, "DOCKERFILE", dockerfile)
    monkeypatch.setattr(NOTICES, "dependency_closure", lambda: [])
    rendered = NOTICES.render()
    assert "`python:3.12.15-slim-bookworm`" in rendered and "`sha256:" + "b" * 64 + "`." in rendered
    assert "GNU Make" not in rendered


PIN = "python:3.12.15-slim-bookworm@sha256:" + "b" * 64
UV = "ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.24@sha256:" + "c" * 64 + "\n"


def test_the_base_given_as_a_build_argument_is_the_pin_and_uv_s_stage_is_not_shipped(tmp_path, monkeypatch):
    """Issue #173: the base is the BASE_IMAGE build argument's default, and the build-only uv stage has no notice."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(f"ARG BASE_IMAGE={PIN}\n{UV}\nFROM ${{UV_IMAGE}} AS uv\n\nFROM ${{BASE_IMAGE}}\nUSER 0\n"
                          "RUN --mount=from=uv,source=/uv,target=/bin/uv uv pip install -r requirements.txt\n",
                          encoding="utf-8")
    monkeypatch.setattr(NOTICES, "DOCKERFILE", dockerfile)
    monkeypatch.setattr(NOTICES, "dependency_closure", lambda: [])
    assert NOTICES.container_image() == PIN
    rendered = NOTICES.render()
    assert "`python:3.12.15-slim-bookworm`" in rendered and "`sha256:" + "b" * 64 + "`." in rendered
    assert "uv" not in rendered.replace("uvicorn", "")


def test_the_repository_s_dockerfile_names_its_pinned_default_base():
    assert NOTICES.container_image().startswith("python:3.12.15-slim-bookworm@sha256:")


@pytest.mark.parametrize("text", [
    "FROM python:3.12.15-bookworm@sha256:" + "a" * 64 + " AS make\nFROM " + PIN + "\n",
    "FROM " + PIN + " AS app\n",
    "FROM python:3.12.15-slim-bookworm\n",
    "ARG BASE_IMAGE=python:3.12.15-slim-bookworm\nFROM ${BASE_IMAGE}\n",
    "FROM ${BASE_IMAGE}\n",
    "FROM ${BASE_IMAGE}\nARG BASE_IMAGE=" + PIN + "\n",
    f"ARG BASE_IMAGE={PIN}\n{UV}FROM ${{BASE_IMAGE}}\nFROM ${{UV_IMAGE}} AS uv\n",
    f"ARG BASE_IMAGE={PIN}\n{UV}FROM ${{UV_IMAGE}} AS uv\nFROM ${{BASE_IMAGE}}\nCOPY --from=uv /uv /usr/local/bin/uv\n",
    f"ARG BASE_IMAGE={PIN}\n{UV}FROM ${{UV_IMAGE}} AS tools\nFROM ${{BASE_IMAGE}}\n",
])
def test_a_dockerfile_of_another_shape_is_refused(tmp_path, monkeypatch, text):
    """A second runtime stage, an unpinned base, a base argument without a default before the first FROM, the base not
    the last stage, uv copied into the image, or a build-only stage of another name."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(text, encoding="utf-8")
    monkeypatch.setattr(NOTICES, "DOCKERFILE", dockerfile)
    with pytest.raises(RuntimeError):
        NOTICES.container_image()
