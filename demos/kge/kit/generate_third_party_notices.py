#!/usr/bin/env python3
"""Generate THIRD_PARTY_NOTICES.md on Linux x86-64 from requirements and installed metadata.

Licence files can vary between platform wheels. Generate the committed notices
in the image's Linux x86-64 Python 3.12 environment, never from a macOS venv.
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import platform
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
OUTPUT = ROOT / "THIRD_PARTY_NOTICES.md"
DOCKERFILE = ROOT / "Dockerfile"
DIST_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
EXTRA = re.compile(r"\bextra\s*==\s*(['\"])([^'\"]+)\1")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement(raw: str) -> tuple[str, set[str]] | None:
    """Return a requirement's name and requested extras; comments and markers are ignored here."""
    value = raw.split("#", 1)[0].strip()
    if not value:
        return None
    match = DIST_NAME.match(value)
    if not match:
        raise ValueError(f"cannot read requirement: {raw.rstrip()}")
    rest = value[match.end():].lstrip()
    extras: set[str] = set()
    if rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            raise ValueError(f"unclosed extras: {raw.rstrip()}")
        extras = {canonical(item.strip()) for item in rest[1:end].split(",") if item.strip()}
    return canonical(match.group()), extras


def installed() -> dict[str, metadata.Distribution]:
    return {canonical(dist.metadata["Name"]): dist for dist in metadata.distributions() if dist.metadata.get("Name")}


def dependency_closure() -> list[metadata.Distribution]:
    """Walk installed dependencies reachable from the file's top-level requirements. A clean installed environment
    already embodies the non-extra environment markers, since an inapplicable dependency is absent, so following only
    the distributions present needs no marker parser."""
    available = installed()
    pending: list[tuple[str, set[str]]] = []
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        parsed = requirement(line)
        if parsed:
            pending.append(parsed)
    found: dict[str, metadata.Distribution] = {}
    while pending:
        name, active_extras = pending.pop()
        if name in found:
            continue
        try:
            dist = available[name]
        except KeyError as exc:
            raise RuntimeError(f"{name} from the dependency closure is not installed") from exc
        found[name] = dist
        for raw in dist.requires or ():
            parsed = requirement(raw)
            if not parsed:
                continue
            dependency, extras = parsed
            marker = raw.partition(";")[2]
            marker_extras = {canonical(match.group(2)) for match in EXTRA.finditer(marker)}
            if marker_extras and not marker_extras.intersection(active_extras):
                continue
            if dependency in available:
                pending.append((dependency, extras))
    return [found[name] for name in sorted(found)]


def licence_label(dist: metadata.Distribution) -> str:
    expression = dist.metadata.get("License-Expression")
    if expression:
        return expression.strip()
    classifiers = [item.rsplit("::", 1)[-1].strip() for item in dist.metadata.get_all("Classifier", [])
                   if item.startswith("License ::")]
    legacy = (dist.metadata.get("License") or "").strip()
    return "; ".join(dict.fromkeys(classifiers)) or legacy or "Not declared in package metadata"


def licence_files(dist: metadata.Distribution) -> list[tuple[str, str]]:
    """A distribution's licence files, their words kept and only the packaging's line-end whitespace removed, which
    the repository's Markdown hygiene rule refuses and which has no effect on the notice."""
    prefix = f"{canonical(dist.metadata['Name']).replace('-', '_')}-{dist.version}.dist-info"
    out = []
    for item in dist.files or ():
        parts = item.parts
        in_dist_info = any(part.endswith(".dist-info") for part in parts)
        filename = parts[-1].lower()
        if in_dist_info and ("licenses" in parts or filename.startswith(("license", "licence", "copying", "notice"))):
            path = dist.locate_file(item)
            try:
                text = "\n".join(line.rstrip() for line in path.read_text(encoding="utf-8").splitlines()).strip()
            except (OSError, UnicodeDecodeError):
                continue
            if text:
                display = "/".join(parts[parts.index(next(p for p in parts if p.endswith('.dist-info'))) + 1:])
                out.append((display or prefix, text))
    return sorted(set(out))


def container_image() -> str:
    """Return the runtime image pinned in the Dockerfile, its one base. The base may be named in the runtime stage's FROM
    or given there as ${BASE_IMAGE}, a build argument declared before the first FROM, whose default is then the pin. The
    one other stage allowed is uv's, `FROM ${UV_IMAGE} AS uv`, which the install steps mount and which the image never
    copies from: uv is not shipped, so it has no notice."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    defaults: dict[str, str] = {}
    images: list[tuple[str, str | None]] = []
    for line in text.splitlines():
        declared = re.fullmatch(r"ARG\s+([A-Za-z_][A-Za-z0-9_]*)=(\S+)", line.strip(), re.IGNORECASE)
        if declared and not images:
            defaults[declared.group(1)] = declared.group(2)
        match = re.fullmatch(r"FROM\s+(\S+)(?:\s+AS\s+(\S+))?", line.strip(), re.IGNORECASE)
        if match:
            images.append((match.group(1), match.group(2)))
    build_only = [image for image in images if image == ("${UV_IMAGE}", "uv")]
    runtime = [image for image in images if image not in build_only]
    if len(runtime) != 1 or runtime[0][1] is not None or images[-1] != runtime[0]:
        raise RuntimeError("Dockerfile must name one runtime image, its last stage, and no stage but uv's")
    if re.search(r"^\s*COPY\s+[^\n]*--from=uv\b", text, re.IGNORECASE | re.MULTILINE):
        raise RuntimeError("Dockerfile must not copy from the uv stage: uv is for the build alone")
    base = runtime[0][0]
    argument = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", base)
    if argument:
        if argument.group(1) not in defaults:
            raise RuntimeError(f"Dockerfile base {base} has no default before the first FROM")
        base = defaults[argument.group(1)]
    if re.fullmatch(r"python:\S+@sha256:[0-9a-f]{64}", base) is None:
        raise RuntimeError("Dockerfile base image must be a Python image pinned to a SHA-256 digest")
    return base


def render() -> str:
    """The notices file. MPL-2.0 (section 3.2) asks that a recipient of the executable form be told how to obtain the
    source form; the image carries such a distribution unmodified, so its source is the published release of that
    version."""
    distributions = dependency_closure()
    runtime_tag, runtime_digest = container_image().split("@", 1)
    lines = [
        "# Third-party notices", "",
        "Generated by `generate_third_party_notices.py` from the dependency closure of `requirements.txt`",
        f"in the current environment. The closure contains **{len(distributions)} distributions**. Do not edit this file by hand.", "",
        "## Container base and system software", "",
        f"The runtime base is `{runtime_tag}` pinned to",
        f"`{runtime_digest}`.",
        "It is based on Debian bookworm. Its Debian packages are each governed by their own licence, including",
        "GPL and LGPL packages. Corresponding source for the packages in that release is available from Debian's",
        "source archive and `https://snapshot.debian.org/`; package copyright and licence records are under",
        "`/usr/share/doc/<package>/copyright` in the image.", "",
        "## Python distributions", "",
    ]
    for dist in distributions:
        name = dist.metadata["Name"]
        label = licence_label(dist)
        lines += [f"### {name} {dist.version}", "", f"Licence: {label}", ""]
        if "MPL" in label or "Mozilla Public" in label:
            lines += [
                f"Source: unmodified. The source form of {name} {dist.version} is its published release, at "
                f"`https://pypi.org/project/{name}/{dist.version}/#files`; on request, the licensor supplies a copy "
                "at no more than the cost of distribution.",
                "",
            ]
        files = licence_files(dist)
        if not files:
            lines += ["No licence file was present in this distribution's installed `.dist-info` metadata.", ""]
        for filename, text in files:
            lines += [f"#### `{filename}`", "", "```text", text, "```", ""]
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if the committed notices differ")
    args = parser.parse_args()
    if not (sys.platform.startswith("linux") and platform.machine() in ("x86_64", "AMD64")):
        print("generate the notices in the image's Linux x86-64 environment", file=sys.stderr)
        return 2
    generated = render()
    if args.check:
        committed = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if committed != generated:
            print(f"{OUTPUT}: stale; run {Path(__file__).name}", file=sys.stderr)
            return 1
    else:
        OUTPUT.write_text(generated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
