"""Runtime probes for the UBI base (issue #325).

The unit shards skip this file unless KGE_IMAGE_PROBES=1. Building the image needs Docker and the network.
With the flag set, the probes build both runtime images and the KGE Dockerfile with BASE_IMAGE set to the previous
Debian pin, then check each runtime uid, RHEL, both /healthz endpoints, removed build tools, the fixed system library
versions, that no Kerberos package or link is left (issue #472), that Ladybug imports, bash's Python path, the shipped legal files, and data volumes written by an older image.

    KGE_IMAGE_PROBES=1 .venv/bin/python -m pytest tests/unit/test_image_base_probe.py -q
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[2]
DEBIAN = "python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70"
UBI = "kge-probe:ubi"
OLD = "kge-probe:debian"
PORTAL = "kge-probe:portal"
VOLUME = "kge-probe-upgrade"

pytestmark = pytest.mark.skipif(
    os.environ.get("KGE_IMAGE_PROBES") != "1",
    reason="set KGE_IMAGE_PROBES=1 to build the image and probe uid, healthz, the build-arg override and an upgraded volume",
)


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True)


def build(tag: str, *extra: str) -> None:
    done = docker("build", "--platform", "linux/amd64", "-t", tag, *extra, str(DEMO))
    assert done.returncode == 0, done.stderr[-2000:]


@pytest.fixture(scope="module")
def images() -> None:
    build(UBI)
    build(OLD, "--build-arg", f"BASE_IMAGE={DEBIAN}")


@pytest.fixture(scope="module")
def portal_image() -> None:
    docker("build", "--platform", "linux/amd64", "-t", PORTAL, str(DEMO / "portal"))


@pytest.mark.parametrize("image", [UBI, PORTAL])
def test_runtime_images_have_no_build_package_tools(images, portal_image, image):
    fixed = {"libblkid": "2.37.4-28.el9_9.1", "libmount": "2.37.4-28.el9_9.1", "libsmartcols": "2.37.4-28.el9_9.1",
             "libuuid": "2.37.4-28.el9_9.1", "libxml2": "2.9.13-18.el9_9.1"}
    if image == UBI:
        fixed["expat"] = "2.5.0-9.el9_9.4"
    versions = docker("run", "--rm", "--platform", "linux/amd64", "--entrypoint", "rpm", image,
                      "-q", "--qf", "%{NAME} %{VERSION}-%{RELEASE}\n", *fixed)
    assert versions.stdout.splitlines() == [f"{name} {version}" for name, version in fixed.items()], versions.stdout
    packages = ["microdnf", "libdnf", "json-glib", "python3.12-pip-wheel"]
    if image == PORTAL:
        packages += ["npm", "nodejs-nodemon"]
    for package in packages:
        done = docker("run", "--rm", "--platform", "linux/amd64", "--entrypoint", "rpm", image,
                      "-q", package, check=False)
        assert done.returncode == 1 and "is not installed" in done.stdout, done.stdout + done.stderr
    tools = "microdnf npm nodemon pip pip3" if image == PORTAL else "microdnf pip pip3"
    done = docker("run", "--rm", "--platform", "linux/amd64", "--entrypoint", "sh", image, "-c",
                  f"for tool in {tools}; do if command -v \"$tool\"; then exit 1; fi; done")
    assert done.returncode == 0, done.stdout + done.stderr


KERBEROS_PACKAGES = ["krb5-libs", "libcurl-minimal", "curl-minimal", "librepo", "cyrus-sasl-lib", "openldap", "gnupg2",
                     "gpgme", "libtirpc", "libnsl2"]
KERBEROS_LIBRARIES = ("libgssapi_krb5", "libkrb5", "libk5crypto", "libkrb5support", "libgssrpc", "libkdb5", "libkrad")
LINKS = r"""
for f in $(find / -xdev \( -path /proc -o -path /sys \) -prune -o -type f \( -perm -u+x -o -name '*.so' -o -name '*.so.*' -o -name '*.node' \) -print); do
  head -c 4 "$f" | grep -q ELF || continue
  if out=$(ldd "$f" 2>&1); then printf '== ok %s\n%s\n' "$f" "$out"
  elif printf %s "$out" | grep -q 'not a dynamic executable'; then printf '== static %s\n' "$f"
  else printf '== failed %s\n%s\n' "$f" "$out"; fi
done
"""


def kerberos_links(output: str) -> tuple[int, list[str], list[str]]:
    """How many ELF files ldd inspected, each line that names a Kerberos library with its file, and each file ldd
    could not inspect. A static file links nothing and is neither counted nor failed."""
    inspected, linked, failed, current = 0, [], [], ""
    for line in output.splitlines():
        if line.startswith("== "):
            state, _, current = line[3:].partition(" ")
            inspected += state == "ok"
            if state not in ("ok", "static"):
                failed.append(current)
        elif any(library in line for library in KERBEROS_LIBRARIES):
            linked.append(f"{current}: {line.strip()}")
    return inspected, linked, failed


@pytest.mark.parametrize("image", [UBI, PORTAL])
def test_runtime_images_ship_no_kerberos_package_or_library(images, portal_image, image):
    """Issue #472: krb5-libs and every package that needed it are gone, no file of them is left, and no executable,
    shared library, Python extension or Node module links a Kerberos library, directly or through another library."""
    for package in KERBEROS_PACKAGES:
        done = docker("run", "--rm", "--platform", "linux/amd64", "--entrypoint", "rpm", image,
                      "-q", package, check=False)
        assert done.returncode == 1 and "is not installed" in done.stdout, done.stdout + done.stderr
    files = docker("run", "--rm", "--platform", "linux/amd64", "--entrypoint", "sh", image, "-c",
                   "ls /usr/lib64; ls /usr/lib64/python3*/lib-dynload 2>/dev/null || true")
    assert not [name for name in files.stdout.split() if name.startswith(KERBEROS_LIBRARIES) or name.startswith("nis.")]
    scan = docker("run", "--rm", "--platform", "linux/amd64", "--user", "0", "--entrypoint", "sh", image, "-c", LINKS)
    assert scan.returncode == 0, scan.stderr[-2000:]
    inspected, linked, failed = kerberos_links(scan.stdout)
    assert inspected > 100, scan.stdout[-2000:]
    assert not failed, failed
    assert not linked, "\n".join(linked)


def test_nothing_imports_the_removed_nis_module(images):
    done = docker("run", "--rm", "--platform", "linux/amd64", "--entrypoint", "sh", UBI, "-c",
                  "grep -rlE --include='*.py' '^[[:space:]]*(import nis|from nis import)' /app /usr/lib64/python3.12 || true")
    assert done.returncode == 0 and done.stdout.strip() == "", done.stdout
    gone = docker("run", "--rm", "--platform", "linux/amd64", "--entrypoint", "/app/.venv/bin/python", UBI, "-c",
                  "import importlib.util, ssl; assert importlib.util.find_spec('nis') is None; print(ssl.OPENSSL_VERSION)")
    assert gone.returncode == 0 and "OpenSSL" in gone.stdout, gone.stdout + gone.stderr


def identity(tag: str) -> str:
    return docker("run", "--rm", "--platform", "linux/amd64", "--entrypoint", "id", tag).stdout.strip()


def os_id(tag: str) -> str:
    script = ". /etc/os-release; printf %s \"$ID\""
    return docker("run", "--rm", "--platform", "linux/amd64", "--entrypoint", "sh", tag, "-c", script).stdout.strip()


def owns_uid(tag: str, uid: int) -> None:
    text = identity(tag)
    assert text.startswith(f"uid={uid}(") or text.startswith(f"uid={uid} "), text
    assert "gid=0" in text, text


@pytest.mark.parametrize("image,uid", [(UBI, 10001), (PORTAL, 1000)])
def test_the_default_images_are_rhel_and_run_as_their_own_uids(images, portal_image, image, uid):
    assert os_id(image) == "rhel"
    owns_uid(image, uid)


def test_the_base_argument_still_builds_the_previous_debian_image(images: None):
    """BASE_IMAGE overrides the default. The previous pin still builds, and that image is Debian, still uid 10001."""
    assert os_id(OLD) == "debian"
    owns_uid(OLD, 10001)


def test_ladybug_imports_on_the_ubi_image(images: None):
    done = docker(
        "run", "--rm", "--platform", "linux/amd64", "--entrypoint", "/app/.venv/bin/python", UBI, "-c",
        "import os, ladybug; assert os.geteuid() == 10001; print(ladybug.__file__)",
    )
    assert done.returncode == 0, done.stderr
    assert "ladybug" in done.stdout


@pytest.mark.parametrize("image,uid,mount", [(UBI, 10001, "/app/build"), (PORTAL, 1000, "/data")])
def test_a_volume_the_older_image_wrote_stays_writable(images, portal_image, image, uid, mount):
    """The previous Debian image writes as the prior runtime uid; each new image appends to that volume."""
    volume = f"{VOLUME}-{uid}"
    docker("volume", "rm", "-f", volume, check=False)
    docker("volume", "create", volume)
    try:
        wrote = docker(
            "run", "--rm", "--platform", "linux/amd64", "--user", f"{uid}:0", "-v", f"{volume}:/app/build",
            "--entrypoint", "sh", OLD, "-c",
            "printf from-debian > /app/build/marker && ls -ln /app/build/marker",
        )
        assert wrote.returncode == 0, wrote.stderr
        assert wrote.stdout.split()[2:4] == [str(uid), "0"], wrote.stdout
        added = docker(
            "run", "--rm", "--platform", "linux/amd64", "-v", f"{volume}:{mount}",
            "--entrypoint", "sh", image, "-c",
            f"printf ' from-ubi' >> {mount}/marker && cat {mount}/marker && echo && ls -ln {mount}/marker",
        )
        assert added.returncode == 0, added.stderr
        assert added.stdout.splitlines()[0] == "from-debian from-ubi"
    finally:
        docker("volume", "rm", "-f", volume, check=False)


def test_docker_exec_bash_keeps_the_virtual_environment_first(images):
    started = docker("run", "-d", "--platform", "linux/amd64", "--network", "none",
                     "--entrypoint", "sh", UBI, "-c", "sleep 60")
    container = started.stdout.strip()
    try:
        for mode in ("-c", "-ic"):
            reply = docker("exec", container, "bash", mode,
                           "command -v python; command -v python3; python -c 'import sys; print(sys.prefix)'")
            assert reply.stdout.splitlines() == ["/app/.venv/bin/python", "/app/.venv/bin/python3", "/app/.venv"], reply
        settings = docker("exec", container, "/app/.venv/bin/python", "-c",
                          "import os, json; print(json.dumps([os.environ.get(k) for k in "
                          "('BASH_ENV', 'ENV', 'PROMPT_COMMAND')]))")
        assert json.loads(settings.stdout) == ["", "", ""]
    finally:
        docker("rm", "-f", container, check=False)


@pytest.mark.parametrize("image,folder,notices", [(UBI, "kge", DEMO / "THIRD_PARTY_NOTICES.md"),
                                               (PORTAL, "kge-portal", DEMO / "portal/THIRD_PARTY_NOTICES.md")])
def test_both_images_carry_the_pilot_licence_and_their_notices(images, portal_image, image, folder, notices):
    for filename, source in (("LICENSE", DEMO / "LICENSE"), ("THIRD_PARTY_NOTICES.md", notices)):
        copied = docker("run", "--rm", "--platform", "linux/amd64", "--entrypoint", "cat", image,
                        f"/usr/share/doc/{folder}/{filename}")
        assert copied.stdout.encode() == source.read_bytes(), f"{image}: {filename} differs"


def test_the_ubi_image_answers_healthz_as_uid_10001(images: None):
    """serve, as the image's own user, answers /healthz without a release."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    started = docker(
        "run", "-d", "--platform", "linux/amd64",
        "-p", f"127.0.0.1:{port}:{port}",
        "-v", f"{DEMO}/clients:/app/clients:ro",
        "-v", f"{DEMO}/deploy:/app/deploy:ro",
        "-e", "MODEL_PROVIDER=none",
        "-e", f"PORT={port}",
        UBI,
    )
    assert started.returncode == 0, started.stderr
    name = started.stdout.strip()
    try:
        body = ""
        last = ""
        for _ in range(60):
            user = docker("exec", name, "id", "-u", check=False)
            if user.returncode == 0:
                assert user.stdout.strip() == "10001", user.stdout
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as reply:
                    raw = reply.read().decode()
                    if reply.status == 200:
                        body = raw
                        break
                    last = f"{reply.status} {raw}"
            except Exception as exc:
                last = str(exc)
            time.sleep(1)
        else:
            logs = docker("logs", name, check=False)
            pytest.fail(f"healthz did not answer: {last}\n{logs.stdout[-1500:]}\n{logs.stderr[-1500:]}")
        parsed = json.loads(body)
        assert parsed.get("status") in ("ok", "up", "healthy") or "status" in parsed, parsed
    finally:
        docker("rm", "-f", name, check=False)


def test_the_portal_image_answers_healthz_without_npm(portal_image):
    started = docker("run", "-d", "--platform", "linux/amd64", "--network", "none", "-e", "PORT=18419",
                     "-e", "KGE_URL=http://127.0.0.1:18420", "-e", f"PORTAL_SYNC_TOKEN={'x' * 48}", PORTAL)
    container = started.stdout.strip()
    try:
        for _ in range(60):
            reply = docker("exec", container, "node", "-e",
                           "fetch('http://127.0.0.1:18419/healthz').then(async r=>{if(!r.ok)process.exit(1);"
                           "console.log(await r.text())}).catch(()=>process.exit(1))", check=False)
            if reply.returncode == 0:
                assert json.loads(reply.stdout)["status"] == "ok"
                break
            time.sleep(1)
        else:
            pytest.fail("portal /healthz did not answer")
    finally:
        docker("rm", "-f", container, check=False)
