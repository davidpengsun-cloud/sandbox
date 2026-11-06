"""Execute the shipped Markdown commands without a shell or a second command list."""
from __future__ import annotations

import argparse
import configparser
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


def steps(source):
    result = []
    current = None
    lines = iter(source.splitlines())
    for line in lines:
        if line.startswith("## "):
            match = re.fullmatch(r"## (\d+)\. (.+) \[(read-only|mutating)\]", line)
            if not match:
                raise ValueError(f"invalid step heading: {line}")
            current = {"name": f"{match[1]}. {match[2]}", "mutating": match[3] == "mutating", "commands": [], "expect": [], "targets": []}
            result.append(current)
        elif line.startswith("Targets: ") and current is not None:
            current["targets"] = [value.strip() for value in line[9:].split(",")]
            if not set(current["targets"]) <= {"local", "compose", "gcp"}:
                raise ValueError(f"invalid targets: {line}")
        elif line.startswith("```"):
            kind = line[3:]
            block = []
            for value in lines:
                if value == "```":
                    break
                block.append(value)
            else:
                raise ValueError("unclosed code block")
            if kind not in {"kge", "expect"}:
                continue
            if current is None:
                raise ValueError("command outside a step")
            if kind == "kge":
                for command in block:
                    args = shlex.split(command)
                    if not args or args[0] != "kge" or len(args) < 2 or args[1] == "playbook":
                        raise ValueError("each command must start with kge; no nested playbooks")
                    current["commands"].append(args[1:])
            else:
                current["expect"].extend(x for x in block if x)
    if not result or any(not s["commands"] or not s["expect"] for s in result):
        raise ValueError("every step needs commands and expectations")
    return result


def check_expectations(expected_lines, actual, values):
    """Match substantive results; named captures allow later steps to verify release transitions."""
    for expected in expected_lines:
        if expected.startswith("capture:"):
            name, pattern = expected[8:].split("=", 1)
            match = re.search(pattern, actual, re.MULTILINE)
            matched = match and match.lastindex and match[1]
            if matched:
                values[name] = match[1]
        elif expected.startswith(("different:", "equal:")):
            comparison, names = expected.split(":", 1)
            left, right = names.split(",", 1)
            matched = values.get(left) and values.get(right) and ((values[left] == values[right]) == (comparison == "equal"))
        elif expected.startswith("json:"):
            path, wanted = expected[5:].split("=", 1)
            wanted = json.loads(wanted)
            capture = re.fullmatch(r"\{\{([a-z]+)\}\}", wanted) if isinstance(wanted, str) else None
            if capture:
                if capture[1] not in values:
                    raise ValueError(f"expected capture {capture[1]!r}")
                wanted = values[capture[1]]
            matched = False
            for line in reversed(actual.splitlines()):
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(value, dict):
                    continue
                try:
                    for key in path.split("."):
                        value = value[key]
                except (KeyError, TypeError):
                    break
                matched = type(value) is type(wanted) and value == wanted
                break
        else:
            def replace(match):
                name = match[1]
                if name not in values:
                    raise ValueError(f"expected value {name!r}")
                return re.escape(values[name]) if expected.startswith("regex:") else values[name]
            expected = re.sub(r"\{\{([a-z]+)\}\}", replace, expected)
            matched = re.search(expected[6:], actual, re.MULTILINE) if expected.startswith("regex:") else expected in actual
        if not matched:
            raise ValueError(f"expected {expected!r}")


def run(guide, helper, env, values, safe=False, dry_run=False, first=1, last=None):
    parsed = steps(guide.read_text())
    if first < 1 or first > len(parsed) or (last is not None and (last < first or last > len(parsed))):
        raise ValueError("step range must name existing steps in order")
    for step in parsed[first - 1:last]:
        label = f"playbook {guide.stem}: step {step['name']}"
        if step["targets"] and env.split("/")[0] not in step["targets"]:
            print(label + " skipped (target)", flush=True)
            continue
        if safe and step["mutating"]:
            print(label + " skipped (mutating)", flush=True)
            continue
        print(label, flush=True)
        output = []
        for command in step["commands"]:
            def replace(match):
                key = match[1]
                if key not in values and dry_run:
                    if os.environ.get("DRY_RUN") == "yes":
                        return "UNIT=<unit>" if key == "decision" else "<" + key + ">"
                    return match[0]
                if key not in values:
                    raise ValueError(f"{label}: supply {key.upper()}=<value>")
                return values[key]
            args = [re.sub(r"\{\{([a-z]+)\}\}", replace, arg) for arg in command]
            print("+ " + shlex.join(["kge", *args]), flush=True)
            if dry_run and os.environ.get("DRY_RUN") != "yes":
                continue
            done = subprocess.run(["bash", str(helper), *args, f"ENV={env}"], text=True, stdout=subprocess.PIPE,
                                  env={**os.environ, **({"KGE_PLAYBOOK_SAFE": "1"} if safe else {})})
            output.append(done.stdout)
            print(done.stdout, end="", flush=True)
            if done.returncode:
                raise ValueError(f"{label}: command failed ({done.returncode})")
        if dry_run:
            continue
        actual = "\n".join(output)
        try:
            check_expectations(step["expect"], actual, values)
        except ValueError as exc:
            raise ValueError(f"{label}: {exc}") from None
    print(f"playbook {guide.stem}: {'dry-run' if dry_run else 'passed'}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("helper", type=Path)
    parser.add_argument("environment")
    parser.add_argument("role", choices=["operator", "access-admin", "knowledge-engineer", "business", "steward"])
    parser.add_argument("--from-step", type=int, default=1)
    parser.add_argument("--through-step", type=int)
    parser.add_argument("--safe", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    options, pairs = parser.parse_known_args()
    helper = options.helper.resolve()
    folder = helper.parent.parent.parent / "docs" / "playbooks"
    if not folder.is_dir():
        folder = helper.parent.parent / "guides"
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    config.read(os.environ.get("KGE_STATE", str(Path.home() / ".config/kge/state")))
    values = {"env": options.environment, "client": options.environment.split("/")[-1],
              "folder": config.get(options.environment, "FOLDER", fallback=".")}
    kit = helper.parent.parent
    demo = helper.parent.parent.parent
    examples = kit / "examples" if (kit / "examples").is_dir() else demo / "examples"
    sample = kit / "examples/meridian/clients/meridian" if (kit / "examples/meridian").is_dir() else demo / "clients/meridian"
    values.update(examples=str(examples), upload=str(sample), unit="leasing")
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or key not in {"UNIT", "PROPOSAL", "UPLOAD"} or not value:
            parser.error("playbook accepts UNIT=, PROPOSAL= and UPLOAD=")
        values[key.lower()] = value
    if "proposal" in values:
        values["decision"] = "PROPOSAL=" + values["proposal"]
    elif "unit" in values:
        values["decision"] = "UNIT=" + values["unit"]
    try:
        run(folder / f"{options.role}.md", helper, options.environment, values, options.safe, options.dry_run or os.environ.get("DRY_RUN") == "yes",
            options.from_step, options.through_step)
    except (OSError, ValueError, re.error) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
