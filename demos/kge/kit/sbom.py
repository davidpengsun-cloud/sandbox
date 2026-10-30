"""The image's software bill of materials, CycloneDX 1.5 JSON, for the release kit (kit/build.sh).

    python3 kit/sbom.py kge:<version> Dockerfile [--base <image>] > kge-<version>.sbom.cdx.json

Read from the image itself, with no network and no tool of its own to pin: every Python distribution in the image's
virtual environment, read with importlib.metadata by the environment's own Python (name, version and licence: the
distribution's licence expression, else its licence field when that is a name, else its classifiers' licence names);
every OS package, from dpkg-query on a Debian base or rpm on a RHEL or UBI base, whichever the image has, or from the
rpm database itself on a RHEL base whose rpm was stripped out (with none of them, the bill says so in the property
kge:os-packages, and so does standard error); and the base image. The base is the Dockerfile's ARG BASE_IMAGE default, held to the digest it names, or the base the image was built on instead, given as
--base (or, without it, KGE_BASE_IMAGE): recorded as given, by its digest when it names one, and marked unpinned when
it does not. A build-only stage the final stage never copies from (the uv stage, bind-mounted for the install steps)
puts nothing into the image and is left out. The same image gives the same bytes: no timestamp, and a serial number
drawn from the image's id.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

PYTHON = "/app/.venv/bin/python3"
DISTRIBUTIONS = ("import importlib.metadata as m, json; print(json.dumps([{'name': d.metadata['Name'], "
                 "'version': d.metadata['Version'], 'license_expression': d.metadata.get('License-Expression'), "
                 "'license': d.metadata.get('License'), 'classifier': d.metadata.get_all('Classifier') or []} "
                 "for d in m.distributions()]))")
OS_PACKAGES = ("if [ -r /etc/os-release ]; then . /etc/os-release; fi; echo \"${ID:-unknown}-${VERSION_ID:-}\"; "
               "if command -v dpkg-query >/dev/null 2>&1; then echo dpkg; "
               "dpkg-query -W -f '${Package}\\t${Version}\\t${Architecture}\\n'; "
               "elif command -v rpm >/dev/null 2>&1; then echo rpm; "
               "rpm -qa --qf '%{NAME}\\t%{EPOCH}\\t%{VERSION}-%{RELEASE}\\t%{ARCH}\\n'; "
               "elif [ -r /var/lib/rpm/rpmdb.sqlite ] && command -v python3 >/dev/null 2>&1; then echo rpmdb; "
               "python3 -c \"$1\" /var/lib/rpm/rpmdb.sqlite; "
               "else echo none; fi")
RPMDB = ("import sqlite3, struct, sys\n"
         "db = sqlite3.connect('file:' + sys.argv[1] + '?immutable=1', uri=True)\n"
         "for (blob,) in db.execute('select blob from Packages'):\n"
         "    count = struct.unpack('>i', blob[:4])[0]\n"
         "    data, tags = blob[8 + 16 * count:], {}\n"
         "    for i in range(count):\n"
         "        tag, kind, offset, _ = struct.unpack('>4i', blob[8 + 16 * i:24 + 16 * i])\n"
         "        if kind == 6:\n"
         "            tags[tag] = data[offset:data.index(b'\\0', offset)].decode()\n"
         "        elif kind == 4:\n"
         "            tags[tag] = str(struct.unpack('>i', data[offset:offset + 4])[0])\n"
         "    print('\\t'.join([tags.get(1000, ''), tags.get(1003, '(none)'), tags.get(1001, '') + '-' + tags.get(1002, ''),"
         " tags.get(1022, '(none)')]))\n")
NO_PACKAGE_TOOL = "neither dpkg-query nor rpm nor an rpm database is in the image: its OS packages are not listed"


def run(image: str, *command: str) -> str:
    """A command in a fresh container of the image, as uid 0 with no network and the image's entrypoint set aside;
    its standard output."""
    entry, *args = command
    out = subprocess.run(["docker", "run", "--rm", "--platform", "linux/amd64", "--network", "none", "--user", "0",
                          "--entrypoint", entry, image, *args], capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"{image}: {' '.join(command)} failed: {out.stderr.strip()[-400:]}")
    return out.stdout


def licences(meta: dict) -> list[dict]:
    """A distribution's licence as its metadata states it: its SPDX expression, else its licence field when that is a
    name rather than a whole text, else its classifiers' licence names."""
    if meta.get("license_expression"):
        return [{"expression": meta["license_expression"]}]
    named = (meta.get("license") or "").strip()
    if named and "\n" not in named and len(named) <= 80:
        return [{"license": {"name": named}}]
    names = [c.split(" :: ")[-1] for c in meta.get("classifier", []) if c.startswith("License :: ")]
    return [{"license": {"name": n}} for n in sorted(set(names))]


def normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def python_components(listing: list[dict]) -> list[dict]:
    """Each distribution importlib.metadata found (DISTRIBUTIONS' list), once by its normalised name: the first on the
    path, the one Python imports."""
    out, seen = [], set()
    for meta in listing:
        name = normal(meta["name"])
        if name in seen:
            continue
        seen.add(name)
        component = {"type": "library", "bom-ref": f"pkg:pypi/{name}@{meta['version']}", "name": name,
                     "version": meta["version"], "purl": f"pkg:pypi/{name}@{quote(meta['version'], safe='')}"}
        found = licences(meta)
        if found:
            component["licenses"] = found
        out.append(component)
    return out


def os_components(listing: str) -> tuple[list[dict], str]:
    """The OS packages in what OS_PACKAGES prints (the distribution as <id>-<version>, the tool, then one package a
    line), and the tool that listed them: dpkg, rpm, rpmdb, or none. rpmdb is a base with an rpm database but no rpm
    (a package manager stripped out): RPMDB, run by the image's python3, reads each package's header from the
    database (name, epoch, version, release and arch) and prints it as rpm -qa would."""
    distro, tool, *lines = [*listing.splitlines(), "", ""]
    vendor = distro.split("-")[0]
    out = []
    for line in filter(None, lines):
        if tool == "dpkg":
            name, version, arch = line.split("\t")
            purl = f"pkg:deb/{vendor}/{name}@{quote(version, safe='')}?arch={arch}&distro={distro}"
        elif tool in ("rpm", "rpmdb"):
            name, epoch, release, arch = line.split("\t")
            if name == "gpg-pubkey":
                continue
            pinned = "" if epoch == "(none)" else f"&epoch={epoch}"
            purl = f"pkg:rpm/{vendor}/{name}@{quote(release, safe='')}?arch={arch}{pinned}&distro={distro}"
            version = release if epoch == "(none)" else f"{epoch}:{release}"
        else:
            continue
        out.append({"type": "library", "bom-ref": purl, "name": name, "version": version, "purl": purl})
    return out, tool if tool in ("dpkg", "rpm", "rpmdb") else "none"


def resolve(ref: str, args: dict[str, str], dockerfile: Path) -> str:
    """A FROM reference with the Dockerfile's global ARG values put in; an ARG with no value stops it."""
    def value(m: re.Match) -> str:
        name = m.group(1) or m.group(2)
        if not args.get(name):
            sys.exit(f"{dockerfile}: FROM {ref} names ARG {name}, which has no value")
        return args[name]
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", value, ref)


def base_images(dockerfile: Path, base: str = "") -> list[dict]:
    """Each FROM of the Dockerfile whose stage reaches the image (the final stage, and any stage it copies from), with
    ARG BASE_IMAGE the Dockerfile's default or the base given. A reference of the Dockerfile's own with no digest stops
    it; a base given without one is recorded by its name and tag and marked unpinned. A base two stages share is listed
    once, as the last stage's."""
    text = re.sub(r"\\\r?\n", " ", dockerfile.read_text())
    first = re.search(r"^\s*FROM\s", text, re.M | re.I)
    args = {m.group(1): (m.group(2) or "").strip("\"'")
            for m in re.finditer(r"^\s*ARG\s+(\w+)(?:=(\S*))?", text[:first.start() if first else 0], re.M | re.I)}
    if base:
        args["BASE_IMAGE"] = base
    stages = list(re.finditer(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", text, re.M | re.I))
    copied = {s.lower() for s in re.findall(r"^\s*COPY\s+(?:--\S+\s+)*?--from=(\S+)", text, re.M | re.I)}
    out = []
    for index, m in enumerate(stages):
        raw, stage = m.group(1), m.group(2)
        final = index == len(stages) - 1
        if not final and (stage or "").lower() not in copied:
            continue
        ref = resolve(raw, args, dockerfile)
        given = bool(base) and re.search(r"\$\{?BASE_IMAGE\b", raw) is not None
        found = re.fullmatch(r"(?P<repo>[^@]+?)(?::(?P<tag>[^:/@]+))?(?:@sha256:(?P<digest>[0-9a-f]{64}))?", ref)
        if not found or (not found["digest"] and not given):
            sys.exit(f"{dockerfile}: FROM {ref} is not held to a digest")
        repo = found["repo"] if "/" in found["repo"] else f"library/{found['repo']}"
        properties = [{"name": "kge:dockerfile:stage", "value": "final" if final else stage}]
        if given:
            properties.append({"name": "kge:base:given", "value": ref})
        component = {"type": "container", "bom-ref": ref, "name": repo, "version": found["tag"] or ""}
        if found["digest"]:
            component["purl"] = f"pkg:docker/{repo}@{quote('sha256:' + found['digest'], safe='')}"
            component["hashes"] = [{"alg": "SHA-256", "content": found["digest"]}]
        else:
            component["purl"] = f"pkg:docker/{repo}@{quote(found['tag'] or 'latest', safe='')}"
            properties.append({"name": "kge:base:pinned", "value": "false: given without a digest"})
        component["properties"] = properties
        out = [c for c in out if c["bom-ref"] != ref] + [component]
    if base and not any(any(p["name"] == "kge:base:given" for p in c["properties"]) for c in out):
        sys.exit(f"{dockerfile}: a base was given ({base}), but no stage that reaches the image is FROM its ARG BASE_IMAGE")
    return out


def main(image: str, dockerfile: str, base: str) -> None:
    """Print the image's bill of materials. A local-only kit's image (build.sh, LOCAL_ONLY=1) says so, and so does its
    bill of materials."""
    info = json.loads(subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True,
                                     check=True).stdout)[0]
    image_id = info["Id"]
    labels = info.get("Config", {}).get("Labels") or {}
    version = labels.get("org.opencontainers.image.version", "")
    marks = [{"name": "kge:local-only", "value": labels["kge.local-only"]}] if "kge.local-only" in labels else []
    bases = base_images(Path(dockerfile), base)
    packages, tool = os_components(run(image, "sh", "-c", OS_PACKAGES, "sh", RPMDB))
    if tool == "none":
        print(f"{image}: {NO_PACKAGE_TOOL}", file=sys.stderr)
        marks.append({"name": "kge:os-packages", "value": NO_PACKAGE_TOOL})
    distributions = python_components(json.loads(run(image, PYTHON, "-c", DISTRIBUTIONS)))
    read_with = "importlib.metadata and " + {"dpkg": "dpkg-query", "rpm": "rpm", "rpmdb": "the rpm database"}.get(tool, "no OS package tool")
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'kge-image:' + image_id)}",
        "version": 1,
        "metadata": {
            "tools": {"components": [{"type": "application", "name": "kit/sbom.py",
                                      "description": f"{read_with}, read from the image"}]},
            "component": {"type": "container", "bom-ref": image, "name": image.split(":")[0], "version": version,
                          "hashes": [{"alg": "SHA-256", "content": image_id.split(":", 1)[-1]}],
                          "properties": [{"name": "kge:image:id", "value": image_id}, *marks]},
        },
        "components": sorted(bases + distributions + packages, key=lambda c: (c["type"], c["purl"])),
    }
    json.dump(bom, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    argv, given = sys.argv[1:], os.environ.get("KGE_BASE_IMAGE", "")
    if len(argv) == 4 and argv[2] == "--base":
        argv, given = argv[:2], argv[3]
    if len(argv) != 2:
        sys.exit("usage: sbom.py <image> <Dockerfile> [--base <image>]")
    main(argv[0], argv[1], given)
