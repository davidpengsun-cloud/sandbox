#!/bin/sh
# The release kit (issue #56): what a platform team installs the pilot from, with no repository. make kit VERSION=<x.y.z>
# runs it from the demo folder, into build/kit/kge-<version>/ (KIT_OUT names another parent folder):
#
#   kge-<version>.zip                      VERSION; terraform/ (infra/'s pilot/, modules/ and tests/, the pilot's test
#                                          helper, less working files and state: never prod/, QA and PROD);
#                                          compose/ (compose.yaml for kge:<version>, .env.example); examples/ (Meridian's
#                                          invented pack, lock and baselines, and the same pack with one phrase added)
#   images/kge-<version>.tar               docker save kge:<version>, linux/amd64, with .digest (its image id) and
#                                          .sbom.cdx.json (CycloneDX 1.5, kit/sbom.py)
#   images/fake-gcs-server-1.56.1.tar      the Cloud Storage emulator the Compose install runs, with .digest
#   docs/INSTALL.md                        kit/INSTALL.md for this version
#   docs/ONBOARDING.md                     the page's business onboarding guide
#   SHA256SUMS                             every file above
#
# IMAGES=0 leaves the images out (no Docker). BUILD_FLAGS goes to both docker builds as given (empty by default), and
# names no BASE_IMAGE: KGE_BASE_IMAGE and PORTAL_BASE_IMAGE (empty: each Dockerfile's default) give each image its own
# base, as its BASE_IMAGE build argument, and its bill of materials records the base given. The zip is
# the same bytes for the same files: entries sorted, timestamps fixed. Nothing is read from outside this folder.
#
# The guard: an image that sets SSL_CERT_FILE, SSL_CERT_DIR, REQUESTS_CA_BUNDLE, CURL_CA_BUNDLE, PIP_CERT or
# NODE_EXTRA_CA_CERTS trusts a certificate authority given at build time (a TLS-intercepting proxy's, its base swapped
# for one that trusts it), and is for local tests only: it is refused, naming the variable, before it is saved.
# LOCAL_ONLY=1 lets it through, into kge-<version>-local-only/ with a LOCAL-ONLY note at its root: a kit never shipped.
# Its image says so itself, in the label kge.local-only, which goes wherever the image goes and into its SBOM.
# The shippable kit is the Release kit workflow's (.github/workflows/kit.yml), built on GitHub's runners.
#
# The images are saved as Docker's containerd image store saves them (the workflow's, and where the kit was tested):
# the ids and the digest below hold there. The classic store's docker save writes manifests of its own.
set -eu
cd "$(dirname "$0")/.."
PY=${PY:-python3}
VERSION=${VERSION:-}
echo "$VERSION" | grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+' || { echo "make kit VERSION=<x.y.z>: the kit's version, three numbers (got '$VERSION')" >&2; exit 2; }
IMAGES=${IMAGES:-1}
BUILD_FLAGS=${BUILD_FLAGS:-}
KGE_BASE_IMAGE=${KGE_BASE_IMAGE:-}
PORTAL_BASE_IMAGE=${PORTAL_BASE_IMAGE:-}
case "$BUILD_FLAGS" in *BASE_IMAGE*) echo "BUILD_FLAGS names BASE_IMAGE, which would give both images one base and their bills of materials the wrong one: set KGE_BASE_IMAGE and PORTAL_BASE_IMAGE instead" >&2; exit 2 ;; esac
LOCAL_ONLY=${LOCAL_ONLY:-0}
OUT=${KIT_OUT:-build/kit}/kge-$VERSION
if [ "$LOCAL_ONLY" = 1 ]; then OUT=$OUT-local-only; fi
TRUST_VARS="SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE CURL_CA_BUNDLE PIP_CERT NODE_EXTRA_CA_CERTS"
TRUSTED=
EMULATOR=fsouza/fake-gcs-server:1.56.1
EMULATOR_PIN=$EMULATOR@sha256:797ce226d62f947c009dc40246b30cfb456b8473d8241407f9d6f2c04e4d69ef

rm -rf "$OUT"
mkdir -p "$OUT/docs"

# The zip, written by Python's zipfile so that its bytes depend on the files alone.
"$PY" - "$VERSION" "$OUT/kge-$VERSION.zip" <<'EOF'
import fnmatch
import sys
import zipfile
from pathlib import Path

version, target = sys.argv[1], Path(sys.argv[2])
# The pilot alone: its configuration, the modules it calls and the test helper its tests read (tests/declared). QA and
# PROD (prod/) are not part of the kit. Never shipped either: infra/'s working files and state, what its .gitignore
# leaves out, and its README, which is the repository's (the kit's docs/INSTALL.md is the installer's).
SHIPPED = {"pilot", "modules", "tests"}
SKIP_DIRS = {".terraform"}
SKIP = ("README.md", "*.tfstate", "*.tfstate.*", "*.tfplan", "crash.log", "crash.*.log", "override.tf", "override.tf.json",
        "*_override.tf", "*_override.tf.json", ".terraformrc", "terraform.rc", "*.local.tfvars")
entries = {"VERSION": (version + "\n").encode()}
for path in sorted(Path("infra").rglob("*")):
    rel = path.relative_to("infra")
    if (not path.is_file() or path.is_symlink() or rel.parts[0] not in SHIPPED or SKIP_DIRS & set(rel.parts)
            or any(fnmatch.fnmatch(path.name, p) for p in SKIP)):
        continue
    entries[f"terraform/{rel.as_posix()}"] = path.read_bytes()
entries["compose/compose.yaml"] = Path("kit/compose/compose.yaml").read_text().replace("@VERSION@", version).encode()
entries["compose/.env.example"] = Path("kit/compose/.env.example").read_bytes()
for path in sorted(Path("skills").rglob("*")):
    if path.is_file() and not path.is_symlink():
        entries[path.as_posix()] = path.read_bytes()
# The example content, an optional upload after installation: Meridian's invented pack, its lock and its A/B baselines
# (issue-*.json, which a job's BASELINE names), not the snapshots only this folder's own checks read (parity-*.json,
# estate-*.json). The pack goes without its comment lines, which point into this folder: the same data, the same hash.
entries["examples/meridian-portal/catalogue.yaml"] = Path("examples/meridian-portal/catalogue.yaml").read_bytes()
pack = Path("clients/meridian")
text = "".join(line for line in (pack / "pack.yaml").read_text().splitlines(keepends=True) if not line.lstrip().startswith("#"))
entries["examples/meridian/clients/meridian/pack.yaml"] = text.encode()
for path in [pack / "pack.lock", pack / "catalogue.yaml", *sorted((pack / "baselines").glob("issue-*.json"))]:
    entries[f"examples/meridian/{path.as_posix()}"] = path.read_bytes()
# The same pack with the Demo script's one-line edit, the second release: takings read as revenue.
anchor = "    - {phrase: revenue, term: revenue}\n"
if text.count(anchor) != 1:
    sys.exit(f"{pack}/pack.yaml: no single line '{anchor.strip()}' to add the takings phrase under")
entries["examples/meridian-takings/clients/meridian/pack.yaml"] = text.replace(
    anchor, anchor + "    - {phrase: takings, term: revenue}\n").encode()
# Optional invented DDL and question sets for exercising page-led business-unit onboarding and improvement.
for name in ("heldout.json", "questions.json", "schema.sql"):
    entries[f"examples/leasing/{name}"] = Path(f"examples/leasing/{name}").read_bytes()
target.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
    for name in sorted(entries):
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        z.writestr(info, entries[name])
print(f"{target}: {len(entries)} files")
EOF

sed "s/@VERSION@/$VERSION/g" kit/INSTALL.md > "$OUT/docs/INSTALL.md"
cp ui/onboarding.md "$OUT/docs/ONBOARDING.md"
mkdir -p "$OUT/notices"
cp THIRD_PARTY_NOTICES.md "$OUT/notices/KGE_THIRD_PARTY_NOTICES.md"
cp portal/THIRD_PARTY_NOTICES.md "$OUT/notices/PORTAL_THIRD_PARTY_NOTICES.md"

if [ "$IMAGES" != 0 ]; then
    mkdir -p "$OUT/images"
    # Each image on its own base: its BASE_IMAGE build argument when one is set, after BUILD_FLAGS so that it wins.
    build() {
        base=$1 && shift
        if [ -n "$base" ]; then set -- "$@" --build-arg "BASE_IMAGE=$base"; fi
        if [ "$LOCAL_ONLY" = 1 ]; then set -- "$@" --label "kge.local-only=for local tests only, never shipped (LOCAL_ONLY=1)"; fi
        context=$1 && shift
        # shellcheck disable=SC2086  # BUILD_FLAGS is a list of flags, as given
        docker build $BUILD_FLAGS "$@" "$context"
    }
    build "$KGE_BASE_IMAGE" . -t "kge:$VERSION" --build-arg "KGE_VERSION=$VERSION" --label "org.opencontainers.image.version=$VERSION"
    build "$PORTAL_BASE_IMAGE" portal -t "kge-portal:$VERSION" --label "org.opencontainers.image.version=$VERSION"
    for image in "kge:$VERSION" "kge-portal:$VERSION"; do
        platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")
        [ "$platform" = linux/amd64 ] || { echo "$image is $platform: the kit's images are linux/amd64" >&2; exit 2; }
        image_env=$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image")
        for var in $TRUST_VARS; do
            if printf '%s\n' "$image_env" | grep -q "^$var="; then TRUSTED="$TRUSTED $image:$var"; fi
        done
    done
    if [ -n "$TRUSTED" ] && [ "$LOCAL_ONLY" != 1 ]; then
        rm -rf "$OUT"
        echo "the kit's images set$TRUSTED: they trust a certificate authority given at build time (a TLS-intercepting proxy's) and are for local tests only, never shipped. LOCAL_ONLY=1 builds them into a kit marked local-only, kge-$VERSION-local-only/" >&2
        exit 2
    fi
    # Each bill of materials names its own image's base: the one given, else its Dockerfile's default.
    sbom() {
        base=$1 && shift
        if [ -n "$base" ]; then "$@" --base "$base"; else "$@"; fi
    }
    # Saved whole, as built, so that it loads under the id .digest holds (a platform filter would save another).
    docker save -o "$OUT/images/kge-$VERSION.tar" "kge:$VERSION"
    docker image inspect --format '{{.Id}}' "kge:$VERSION" > "$OUT/images/kge-$VERSION.digest"
    sbom "$KGE_BASE_IMAGE" "$PY" kit/sbom.py "kge:$VERSION" Dockerfile > "$OUT/images/kge-$VERSION.sbom.cdx.json"
    docker save -o "$OUT/images/kge-portal-$VERSION.tar" "kge-portal:$VERSION"
    docker image inspect --format '{{.Id}}' "kge-portal:$VERSION" > "$OUT/images/kge-portal-$VERSION.digest"
    sbom "$PORTAL_BASE_IMAGE" node kit/sbom_node.mjs "kge-portal:$VERSION" portal/Dockerfile > "$OUT/images/kge-portal-$VERSION.sbom.cdx.json"
    # The emulator, held to the digest Compose pins, and tagged so that it loads with its name. Saved whole, its index
    # with its linux/amd64 image, so that it loads under the digest Compose pins and Compose finds it with no pull.
    docker image inspect "$EMULATOR_PIN" >/dev/null 2>&1 || docker pull --platform linux/amd64 "$EMULATOR_PIN"
    docker tag "$EMULATOR_PIN" "$EMULATOR"
    docker save -o "$OUT/images/fake-gcs-server-1.56.1.tar" "$EMULATOR"
    docker image inspect --format '{{.Id}}' "$EMULATOR" > "$OUT/images/fake-gcs-server-1.56.1.digest"
    chmod 644 "$OUT"/images/*
fi

if [ "$LOCAL_ONLY" = 1 ]; then
    if [ "$IMAGES" = 0 ]; then built="No image was built (IMAGES=0)."; else built="The images set:${TRUSTED:- none of them}."; fi
    cat > "$OUT/LOCAL-ONLY" <<NOTE
LOCAL ONLY. This kit is for local tests: never ship it, install it anywhere else or load its image into a registry.

It was built with LOCAL_ONLY=1, which lets through an image that trusts a certificate authority given at build time
(a TLS-intercepting proxy's): one that sets SSL_CERT_FILE, SSL_CERT_DIR, REQUESTS_CA_BUNDLE, CURL_CA_BUNDLE, PIP_CERT
or NODE_EXTRA_CA_CERTS. $built

A shippable kit is a published one: the GitHub Release of its kge-kit-v<version> tag, published by the Release kit
workflow.
NOTE
fi

(cd "$OUT" && find . -type f ! -name SHA256SUMS | sed 's#^\./##' | LC_ALL=C sort | xargs sha256sum > SHA256SUMS)
echo "the kit: $OUT"
(cd "$OUT" && find . -type f | sed 's#^\./##' | LC_ALL=C sort)
