"""Check a pack with the input validations onboarding runs, and do not build a graph.

``upfront.check`` is the check ``kge onboard`` runs before it writes a fixture, a
lock or a graph. This command reports each of those findings with the file and
line the finding names, and it does not compile, load or release a graph.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from kge.compiler.pack import Finding
from kge.estate import upfront
from kge.pipeline.profile import Profile, ProfileError, resolve


@dataclass(frozen=True)
class Reported:
    """One input finding, plus the file and line an author can open."""

    rule: str
    path: str
    message: str
    file: str
    line: int

    def __str__(self) -> str:
        if self.line:
            return f"{self.rule} {self.file}:{self.line}: {self.message}"
        return f"{self.rule} {self.path}: {self.message}"


def report(prof: Profile) -> list[Reported]:
    """The findings ``upfront.check`` returns for this profile, each with a file and a line."""
    folder = prof.clients / prof.client
    return [locate(folder, finding) for finding in upfront.check(prof).findings]


def check_folder(folder: Path) -> tuple[upfront.Report, list[Reported]]:
    """Check one client folder, using a scratch profile so the check writes no graph."""
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a pack folder")
    with tempfile.TemporaryDirectory(prefix="check-pack-") as directory:
        scratch = Path(directory)
        prof = Profile(folder.name, folder.name, folder.parent, scratch / "artifacts", scratch / "stores",
                       scratch / "landing", scratch / "warehouses", scratch / "identities.yaml", None,
                       scratch / "profiles.yaml")
        for path in (prof.artifacts, prof.store, prof.landing, prof.warehouses):
            path.mkdir(parents=True)
        result = upfront.check(prof)
        return result, [locate(folder, finding) for finding in result.findings]


def locate(folder: Path, finding: Finding) -> Reported:
    """The finding's file and 1-based line, or line 0 where it names no file."""
    file_part, separator, location = finding.path.partition(":")
    target = _file(folder, file_part)
    if target is None or not separator:
        line = 1 if target is not None else 0
        return Reported(finding.rule, finding.path, finding.message, _shown(folder, target, file_part), line)
    if location.startswith("row ") and location[4:].isdigit():
        return Reported(finding.rule, finding.path, finding.message, _shown(folder, target, file_part), int(location[4:]))
    if target.suffix.lower() != ".md":
        sidecar = target.with_name(target.name + ".yaml")
        if location in {"front matter", "unit"} and sidecar.is_file():
            target = sidecar
    text = _text(target)
    if location == "front matter":
        line = 1
    elif target.suffix.lower() == ".md":
        line = _front_line(text, location)
    elif target.suffix.lower() in {".yaml", ".yml"}:
        line = _yaml_line(text, location)
    elif target.suffix.lower() == ".json":
        line = _json_line(text, location)
    else:
        line = None
    return Reported(finding.rule, finding.path, finding.message, _shown(folder, target, file_part), line or 1)


def _file(folder: Path, name: str) -> Path | None:
    raw = Path(name)
    if raw.is_absolute() and raw.is_file():
        return raw
    local = folder / name
    return local if local.is_file() else None


def _shown(folder: Path, target: Path | None, fallback: str) -> str:
    if target is None:
        return fallback
    try:
        return target.resolve().relative_to(folder.resolve()).as_posix()
    except ValueError:
        return str(target)


def _text(path: Path) -> str:
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return ""


def _parts(location: str) -> list:
    parts = []
    for token in re.findall(r"[^.\[\]]+|\[\d+\]", location):
        parts.append(int(token[1:-1]) if token.startswith("[") else token)
    return parts


def _yaml_line(text: str, location: str) -> int | None:
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return None
    for part in _parts(location):
        node = _yaml_child(node, part)
        if node is None:
            return None
    mark = getattr(node, "start_mark", None)
    return None if mark is None else mark.line + 1


def _yaml_child(node, part):
    if node is None:
        return None
    if isinstance(part, int):
        if not isinstance(node, yaml.SequenceNode) or part >= len(node.value):
            return None
        return node.value[part]
    if not isinstance(node, yaml.MappingNode):
        return None
    for key, value in node.value:
        if isinstance(key, yaml.ScalarNode) and str(key.value) == part:
            return value
    return None


def _front_line(text: str, key: str) -> int | None:
    if key == "front matter":
        return 1
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    if not match:
        return None
    base = text[:match.start(1)].count("\n")
    for offset, line in enumerate(match.group(1).splitlines()):
        if line.startswith(key + ":"):
            return base + offset + 1
    return None


def _json_line(text: str, location: str) -> int | None:
    try:
        line, _ = _json_walk(text, 0, _parts(location))
    except (ValueError, IndexError):
        return None
    return line


def _json_walk(text: str, index: int, parts: list) -> tuple[int, int]:
    index = _json_skip_space(text, index)
    line = text.count("\n", 0, index) + 1
    if index >= len(text):
        raise ValueError(text)
    if not parts:
        return line, _json_skip(text, index)
    if text[index] == "{":
        return _json_object(text, index, parts)
    if text[index] == "[":
        return _json_array(text, index, parts)
    return line, _json_skip(text, index)


def _json_object(text: str, index: int, parts: list) -> tuple[int, int]:
    key = parts[0]
    if not isinstance(key, str):
        return text.count("\n", 0, index) + 1, _json_skip(text, index)
    index = _json_skip_space(text, index + 1)
    if index < len(text) and text[index] == "}":
        return text.count("\n", 0, index) + 1, index + 1
    while index < len(text):
        index = _json_skip_space(text, index)
        name, index = _json_string(text, index)
        index = _json_skip_space(text, index)
        if text[index] != ":":
            raise ValueError(text[index])
        index += 1
        if name == key:
            return _json_walk(text, index, parts[1:])
        index = _json_skip(text, index)
        index = _json_skip_space(text, index)
        if text[index] == "}":
            return text.count("\n", 0, index) + 1, index + 1
        if text[index] != ",":
            raise ValueError(text[index])
        index += 1
    raise ValueError(text)


def _json_array(text: str, index: int, parts: list) -> tuple[int, int]:
    wanted = parts[0]
    if not isinstance(wanted, int):
        return text.count("\n", 0, index) + 1, _json_skip(text, index)
    index = _json_skip_space(text, index + 1)
    seen = 0
    if index < len(text) and text[index] == "]":
        return text.count("\n", 0, index) + 1, index + 1
    while index < len(text):
        if seen == wanted:
            return _json_walk(text, index, parts[1:])
        index = _json_skip(text, index)
        seen += 1
        index = _json_skip_space(text, index)
        if text[index] == "]":
            return text.count("\n", 0, index) + 1, index + 1
        if text[index] != ",":
            raise ValueError(text[index])
        index += 1
    raise ValueError(text)


def _json_skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _json_string(text: str, index: int) -> tuple[str, int]:
    if text[index] != '"':
        raise ValueError(text[index])
    index += 1
    chars = []
    while index < len(text):
        char = text[index]
        if char == '"':
            return "".join(chars), index + 1
        if char == "\\":
            index += 1
            escaped = text[index]
            chars.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(escaped, escaped))
        else:
            chars.append(char)
        index += 1
    raise ValueError(text)


def _json_skip(text: str, index: int) -> int:
    index = _json_skip_space(text, index)
    if index >= len(text):
        raise ValueError(text)
    char = text[index]
    if char == '"':
        _, index = _json_string(text, index)
        return index
    if char in "{[":
        depth = 0
        quoted = False
        escaped = False
        while index < len(text):
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char in "{[":
                depth += 1
            elif char in "}]":
                depth -= 1
                if depth == 0:
                    return index + 1
            index += 1
        raise ValueError(text)
    while index < len(text) and text[index] not in ",}] \t\r\n":
        index += 1
    return index


def _print(result: upfront.Report, reported: list[Reported]) -> None:
    for name in result.ignored:
        print(f"check-pack: ignored: {name}")
    if result.suggestion:
        print("check-pack: pack.yaml declares no inputs; add this to its manifest:")
        print("\n".join("  " + line for line in result.suggestion.splitlines()))
    for finding in reported:
        print(f"check-pack: {finding}")
    if reported:
        print(f"check-pack: {len(reported)} finding(s)")
    else:
        print("check-pack: no findings")


def _arguments(argv: list[str]) -> list[str]:
    """PACK= and PROFILE= are arguments, as the image and the plugin pass them.

    An exported PACK is not an argument. This does not read the environment.
    """
    translated: list[str] = []
    expect = False
    for arg in argv:
        if expect:
            translated.append(arg)
            expect = False
            continue
        if arg.startswith("PACK="):
            translated.extend(("--pack", arg.split("=", 1)[1]))
        elif arg.startswith("PROFILE="):
            translated.extend(("--profile", arg.split("=", 1)[1]))
        else:
            translated.append(arg)
            expect = arg in {"--pack", "--profile", "--json"}
    return translated


def _blank_pack(argv: list[str]) -> bool:
    """Whether a pack was named and its path is blank. A blank path is not the current directory or the profile."""
    pending = False
    named = False
    value = ""
    for arg in argv:
        if pending:
            named, value, pending = True, arg, False
            continue
        if arg == "--pack":
            pending = True
            continue
        if arg.startswith("PACK="):
            named, value = True, arg.split("=", 1)[1]
    return pending or (named and not value.strip())


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else list(argv)
    if _blank_pack(raw):
        print("check-pack: refused: PACK is empty")
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, help="a client folder to check, without using the profile registry")
    parser.add_argument("--profile", help="the profile whose pack to check (default: PROFILE, else the registry's default)")
    parser.add_argument("--json", type=Path, help="write the findings, with file and line, as JSON")
    args = parser.parse_args(_arguments(raw))
    try:
        if args.pack:
            result, reported = check_folder(args.pack)
        else:
            prof = resolve(args.profile) if args.profile else resolve()
            result = upfront.check(prof)
            reported = [locate(prof.clients / prof.client, finding) for finding in result.findings]
    except (ProfileError, ValueError) as exc:
        print(f"check-pack: refused: {exc}")
        return 2
    _print(result, reported)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {"findings": [asdict(finding) for finding in reported], "ignored": result.ignored}
        args.json.write_text(json.dumps(payload, indent=2) + "\n")
    return 2 if reported else 0


if __name__ == "__main__":
    raise SystemExit(main())
