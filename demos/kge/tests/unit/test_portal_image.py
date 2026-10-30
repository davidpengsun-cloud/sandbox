"""The portal image's Dockerfile (issue #173 requirement 8): it builds on a client's Node base, as a build argument.

    .venv/bin/python -m pytest tests/unit/test_portal_image.py -q

A client's base may have no node user, a non-root default user and an entrypoint of its own, and may reach packages
only through its own registry, behind its own certificate authority. So the base is a build argument with Red Hat's
signed UBI 9 Node.js 22 minimal base as its default (issue #176), its RHEL minor release held to its index digest,
every npm ci runs as uid 0, the registry is a build argument of each stage that runs
npm ci, a CA reaches those steps alone, credentials enter only as a BuildKit secret, the image runs as a uid and group
0, and it sets its own ENTRYPOINT. Each stage checks Node first: the UI stage's bundler needs 22.12, the server's
node:sqlite 22.5. These tests read the Dockerfile; building the image needs Docker and a registry, so no test here does.
"""
from __future__ import annotations

import re
from pathlib import Path

DEMO = Path(__file__).resolve().parents[2]
DOCKERFILE = DEMO / "portal/Dockerfile"
PINNED = r"registry\.access\.redhat\.com/ubi9/nodejs-22-minimal:9\.\d+@sha256:[0-9a-f]{64}"
SECRETS = re.compile(r"token|auth|passw|secret|npmrc|credential|_key\b", re.I)


def instructions() -> list[tuple[str, str]]:
    """The Dockerfile's instructions, (KEYWORD, arguments), comments and continuations joined."""
    out, held = [], ""
    for line in DOCKERFILE.read_text().splitlines():
        if not held and (not line.strip() or line.lstrip().startswith("#")):
            continue
        held += line.rstrip("\\").strip() + " "
        if not line.endswith("\\"):
            word, _, rest = held.strip().partition(" ")
            out.append((word.upper(), rest.strip()))
            held = ""
    return out


def stages() -> tuple[list[tuple[str, str]], list[list[tuple[str, str]]]]:
    """The instructions before the first FROM, and each stage's, its FROM first."""
    preamble, split = [], []
    for word, args in instructions():
        if word == "FROM":
            split.append([])
        (split[-1] if split else preamble).append((word, args))
    return preamble, split


def runs_npm_ci(args: str) -> bool:
    return re.search(r"\bnpm ci\b", args) is not None


def test_the_base_is_a_build_argument_whose_default_is_pinned():
    """ARG BASE_IMAGE comes before the first FROM, its default Red Hat's signed UBI 9 Node.js 22 minimal base, its RHEL
    minor release (9.<minor>, never latest) held to its multi-arch index digest, and every stage is FROM it: no other
    image, no tag that moves."""
    preamble, split = stages()
    args = [a for w, a in preamble if w == "ARG"]
    assert len(args) == 1 and re.fullmatch(rf"BASE_IMAGE={PINNED}", args[0]), args
    assert [w for w, _ in preamble] == ["ARG"], preamble
    froms = [stage[0][1] for stage in split]
    assert len(froms) == 2 and all(re.fullmatch(r"\$\{BASE_IMAGE\}( AS \w+)?", f) for f in froms), froms


def test_each_npm_ci_runs_as_root_with_the_registry_a_build_argument():
    """In each stage that runs npm ci, USER 0 comes right after FROM, before any npm ci, and npm_config_registry is
    declared there, with no default: Docker gives an unset one to the step as empty, which npm reads as its
    public registry."""
    _, split = stages()
    with_ci = [stage for stage in split if any(w == "RUN" and runs_npm_ci(a) for w, a in stage)]
    assert len(with_ci) == 2, "npm ci in both stages"
    for stage in with_ci:
        assert stage[1] == ("USER", "0"), stage[:2]
        first_ci = next(i for i, (w, a) in enumerate(stage) if w == "RUN" and runs_npm_ci(a))
        declared = [a for w, a in stage[:first_ci] if w == "ARG"]
        assert "npm_config_registry" in declared and "CA_BUNDLE" in declared, declared


def test_credentials_are_a_secret_mount_and_the_ca_never_env():
    """Each npm ci reads .npmrc from the BuildKit secret npmrc, optional, and takes CA_BUNDLE as NODE_EXTRA_CA_CERTS
    and npm_config_cafile within that step. No ARG or ENV carries a credential, and no ENV names a CA, so the image's
    history and Env hold neither, and kit/build.sh's guard still refuses an image that trusts a build-time CA."""
    said = instructions()
    for word, args in said:
        if word == "RUN" and runs_npm_ci(args):
            assert "--mount=type=secret,id=npmrc,required=false" in args, args
            assert "NPM_CONFIG_USERCONFIG=/run/secrets/npmrc" in args, args
            assert re.search(r'NODE_EXTRA_CA_CERTS="\$CA_BUNDLE"', args) and re.search(r'npm_config_cafile="\$CA_BUNDLE"', args), args
    names = [args.split("=", 1)[0] for word, args in said if word == "ARG"]
    assert names and not [n for n in names if SECRETS.search(n)], names
    env = " ".join(args for word, args in said if word == "ENV")
    assert not SECRETS.search(env) and not re.search(r"NODE_EXTRA_CA_CERTS|cafile|SSL_CERT|CA_BUNDLE", env, re.I), env


def test_the_image_runs_as_a_uid_in_group_zero_with_no_named_user():
    """The run-time user is uid 1000 in group 0, set by number: a base need not have a node user. 1000 is the old image's
    node user's uid, so a portal-data volume that image wrote stays writable after an upgrade. What the server writes,
    /data and its default store under build/, is that uid's and group-writable."""
    said = instructions()
    users = [args for word, args in said if word == "USER"]
    assert users[-1] == "1000:0", users
    assert not re.search(r"\bnode:node\b|\bUSER node\b|useradd|adduser", DOCKERFILE.read_text()), "no named user"
    owned = " ".join(args for word, args in said if word == "RUN" and "chown" in args)
    for path in ("/data", "/app/server/build"):
        assert re.search(rf"chown -R 1000:0 [^&]*{path}\b", owned) and re.search(rf"chmod -R g\+rwX [^&]*{path}\b", owned), owned


def test_the_image_sets_its_own_entrypoint():
    """The ENTRYPOINT is the server under --experimental-sqlite, so neither a base's entrypoint nor its CMD is ever
    run, and the image sets no CMD of its own, which would be passed to the server as arguments."""
    said = instructions()
    assert [args for word, args in said if word == "ENTRYPOINT"] == ['["node", "--experimental-sqlite", "src/index.js"]']
    assert not [args for word, args in said if word == "CMD"]
    assert [w for w, _ in said][-1] == "ENTRYPOINT"


def test_each_stage_checks_node_before_npm_ci():
    """The UI stage, before its npm ci, fails naming Node 22.12 on an older base: the UI's bundler, rolldown, needs it,
    and npm ci skips its native binding on an older Node, so the UI build would fail with a module not found. The final
    stage, before its npm ci, opens a node:sqlite database under --experimental-sqlite and fails naming Node 22.5 on a
    base that cannot, so the run-time base is checked for the server's own need, not at the portal's first start."""
    _, (ui, final) = stages()
    for stage, needle, version in ((ui, "process.versions.node", "22.12"), (final, "node:sqlite", "22.5")):
        checks = [(i, a) for i, (w, a) in enumerate(stage) if w == "RUN" and needle in a]
        assert len(checks) == 1, checks
        at, check = checks[0]
        assert f"Node {version} or later" in check and "$(node --version)" in check and "exit 1" in check, check
        assert at < next(i for i, (w, a) in enumerate(stage) if w == "RUN" and runs_npm_ci(a)), "checked before npm ci"
    sqlite = next(a for w, a in final if w == "RUN" and "node:sqlite" in a)
    assert re.match(r"node --experimental-sqlite -e ", sqlite) and "DatabaseSync" in sqlite, sqlite
    assert re.search(r"a === 22 && b >= 12", next(a for w, a in ui if w == "RUN" and "process.versions.node" in a))


def test_the_image_keeps_its_contract_health_check_and_settings():
    said = instructions()
    assert ("COPY", "server/contract ./contract") in said
    assert ("ENV", "NODE_ENV=production PORT=8080 PORTAL_UI_PATH=/app/ui") in said
    assert ("EXPOSE", "8080") in said
    assert any(word == "HEALTHCHECK" and "/healthz" in args for word, args in said)
