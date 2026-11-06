"""Brand the invented sample's identifiers and text without changing its data structure."""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from kge.pipeline.settings import ROOT

SAMPLE = yaml.safe_load((ROOT / "kge/pipeline/sample.yaml").read_text())


def display_name(client: str, name: str | None = None) -> str:
    value = (name or "").strip() or (SAMPLE["name"] if client == SAMPLE["client"] else client.replace("-", " ").title())
    if any(ord(char) < 32 for char in value):
        raise ValueError("NAME must be a single display name without control characters")
    return value


def brand(value, client: str, name: str):
    if isinstance(value, str):
        replacements = {SAMPLE["name"]: name, SAMPLE["label"]: name, SAMPLE["client"]: client,
                        SAMPLE["client"].upper(): client.upper()}
        pattern = "|".join(re.escape(word) for word in sorted(replacements, key=len, reverse=True))
        return re.sub(pattern, lambda match: replacements[match[0]], value)
    if isinstance(value, list):
        return [brand(item, client, name) for item in value]
    if isinstance(value, dict):
        return {brand(key, client, name): brand(item, client, name) for key, item in value.items()}
    return value


def branded_file(path: str, data: bytes, client: str, name: str) -> bytes:
    if client == SAMPLE["client"] and name == SAMPLE["name"]:
        return data
    text = data.decode("utf-8")
    suffix = Path(path).suffix
    if suffix in {".yaml", ".yml"}:
        edits = []
        visited = set()

        def visit(node):
            if id(node) in visited:
                return
            visited.add(id(node))
            if isinstance(node, yaml.ScalarNode):
                value = brand(node.value, client, name)
                if value != node.value:
                    original = text[node.start_mark.index:node.end_mark.index]
                    replacement = brand(original, client, name)
                    if node.style == '"':
                        replacement = json.dumps(value, ensure_ascii=False)
                    elif node.style == "'":
                        replacement = "'" + value.replace("'", "''") + "'"
                    elif node.style is None:
                        try:
                            valid = yaml.safe_load("value: " + replacement) == {"value": value}
                            valid = valid and yaml.safe_load("[" + replacement + "]") == [value]
                        except yaml.YAMLError:
                            valid = False
                        if not valid:
                            replacement = json.dumps(value, ensure_ascii=False)
                    edits.append((node.start_mark.index, node.end_mark.index, replacement))
            elif isinstance(node, yaml.MappingNode):
                for key, value in node.value:
                    visit(key)
                    visit(value)
            elif isinstance(node, yaml.SequenceNode):
                for value in node.value:
                    visit(value)

        node = yaml.compose(text)
        if node is not None:
            visit(node)
        pieces = []
        cursor = 0
        for start, end, replacement in sorted(edits):
            pieces.extend((brand(text[cursor:start], client, name), replacement))
            cursor = end
        pieces.append(brand(text[cursor:], client, name))
        branded = "".join(pieces)
        if yaml.safe_load(branded) != brand(yaml.safe_load(text), client, name):
            raise ValueError("the branded YAML does not preserve the sample's structure")
        return branded.encode()
    if suffix in {".json", ".lock"}:
        return (json.dumps(brand(json.loads(text), client, name), indent=2, ensure_ascii=False) + "\n").encode()
    return brand(text, client, name).encode()
