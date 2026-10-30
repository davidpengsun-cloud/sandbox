# One image for the demo's app and its jobs (issue #48): the platform code and the page, the simulated estate's
# generator (seed runs it), the meta-model, the platform's preloaded industry standards, the Makefile, the requirements,
# the entry script and the legal notices, and nothing else. No client's pack or deployment, no build, no .env, no token and no secret: at run time clients/ and
# deploy/ are mounted read only, and build/ is a volume. It holds everything the jobs write: the estate, the landing,
# the artifacts, the graph stores and their run state, and the tokens among it. .dockerignore keeps the rest out.
# entrypoint.sh says what each command runs: serve (the default), seed, onboard, sign-off, release and eval.
#
#   docker build -t kge-demo .
#   docker run --rm -v "$PWD/clients:/app/clients:ro" -v "$PWD/deploy:/app/deploy:ro" -v kge-build:/app/build kge-demo seed
#   ... the same for onboard, sign-off PRINCIPAL=<steward>, release and passwords (kge-demo passwords), then serve:
#   docker run --rm -it -p 8000:8000 -v "$PWD/clients:/app/clients:ro" -v "$PWD/deploy:/app/deploy:ro" -v kge-build:/app/build kge-demo
#
# -it gives it a terminal, through which Ctrl-C reaches the API; without one, docker stop stops it. -e STORE_URL=<store>
# runs it on an object store instead of the mounts (entrypoint.sh, kge/pipeline/objstore.py).
# -e PROFILE=<id> picks another profile, -e PORT=<port> another port (Cloud Run sets PORT), -e API_URL=<url> the address
# the API names itself by. It serves with SIGNIN=passwords, since demo mode signs no one in from outside the container:
# a person signs in with the password the passwords command made (the README's Running it in a container).
#
# A client's base (issue #173). The base is a build argument: any image with Python 3.12 as python3, and sh, whatever
# its default user, needing no package manager, useradd or pip. The default is the Debian slim base, held to the digest
# its tag had when pinned in October 2026, so a rebuild pushed under the tag changes nothing. The other build arguments:
#
#   UV_IMAGE          where the pinned uv comes from, held to its digest: a build-only stage, mounted for the install
#                     steps and never copied into the image
#   UV_BIN            unset: the pinned uv; a path, the base's own uv instead (its version decides which settings it reads)
#   UV_DEFAULT_INDEX  the Python package index; unset, PyPI
#   UV_SYSTEM_CERTS   true: uv trusts the base's certificate store (an older uv reads UV_NATIVE_TLS, set from it)
#   CA_BUNDLE         a path, inside the base, to a corporate CA bundle: SSL_CERT_FILE for the install steps alone,
#                     never set in the image, so kit/build.sh's guard still refuses an image that trusts one
#
# A credential for the index is never a build argument, whose value the image's history keeps: it is a BuildKit secret,
# a netrc file (--secret id=netrc,src=<file>), mounted for the install steps alone.
ARG BASE_IMAGE=python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.24@sha256:3af4716e991d6956a41e573eab705d0ee08500cd829ed30293eb8472f372c65a

FROM ${UV_IMAGE} AS uv

# The install steps run as root, whatever the base's default user; the image runs as its own uid, set at the end.
FROM ${BASE_IMAGE}
USER 0

ARG KGE_VERSION=dev
ENV KGE_VERSION=$KGE_VERSION
ARG UV_BIN=
ARG UV_DEFAULT_INDEX=
ARG UV_SYSTEM_CERTS=
ARG CA_BUNDLE=

WORKDIR /app
COPY requirements.txt constraints.txt ./
# The dependencies, installed with uv into a virtual environment made from the base's own python3, never a Python uv
# downloads; the build stops, naming the version, when that is not 3.12. constraints.txt pins every distribution, so a
# rebuild installs exactly what was tested and what the notices name. Each setting above reaches uv only when it is
# given, and uv keeps no cache in the image. The environment has no pip.
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=secret,id=netrc,required=false \
    set -eu; \
    version=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])'); \
    if [ "$version" != 3.12 ]; then echo "the base's python3 is Python $version; the image needs Python 3.12" >&2; exit 1; fi; \
    if [ -n "$UV_DEFAULT_INDEX" ]; then export UV_DEFAULT_INDEX; else unset UV_DEFAULT_INDEX; fi; \
    if [ -n "$UV_SYSTEM_CERTS" ]; then export UV_SYSTEM_CERTS UV_NATIVE_TLS="$UV_SYSTEM_CERTS"; else unset UV_SYSTEM_CERTS; fi; \
    if [ -n "$CA_BUNDLE" ]; then export SSL_CERT_FILE="$CA_BUNDLE"; fi; \
    if [ -f /run/secrets/netrc ]; then export NETRC=/run/secrets/netrc; fi; \
    export UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1; \
    uv=${UV_BIN:-/bin/uv}; \
    "$uv" venv --python "$(command -v python3)" /app/.venv; \
    "$uv" pip install --python /app/.venv/bin/python -r requirements.txt -c constraints.txt

COPY Makefile meta-model.yaml entrypoint.sh pyproject.toml ./
COPY kge kge
COPY standards standards
COPY ui ui
# Every shared object the dependencies installed is loaded, as its first use would load it (kge/cli/check_libraries.py),
# so a base that lacks a system library a pinned wheel needs (libz.so.1, libssl.so.3, ...) fails here, naming the
# library and the package, not at serve. It runs before the platform's own install, which adds no shared object, because
# that install builds with the base's Python, which may need the missing library itself (a UBI base's Python links
# libz) and would then fail without naming it.
RUN /app/.venv/bin/python /app/kge/cli/check_libraries.py
# The platform code installed in place (editable), as in development and CI, so the modules import by name and find
# the demo's folder, /app, as their root, and kge, the console script, is on PATH (/app/.venv/bin/kge): the image has
# no make, and the entry script runs each command with kge. Its dependencies are the ones installed above. The pinned
# setuptools builds it in a build environment of its own, so the image ships none. The entry script is rewritten with
# LF line ends, by the base's Python: a Windows checkout or copy may give it CRLF, which sh cannot run, and a minimal
# base has no sed or dos2unix.
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    --mount=type=secret,id=netrc,required=false \
    set -eu; \
    if [ -n "$UV_DEFAULT_INDEX" ]; then export UV_DEFAULT_INDEX; else unset UV_DEFAULT_INDEX; fi; \
    if [ -n "$UV_SYSTEM_CERTS" ]; then export UV_SYSTEM_CERTS UV_NATIVE_TLS="$UV_SYSTEM_CERTS"; else unset UV_SYSTEM_CERTS; fi; \
    if [ -n "$CA_BUNDLE" ]; then export SSL_CERT_FILE="$CA_BUNDLE"; fi; \
    if [ -f /run/secrets/netrc ]; then export NETRC=/run/secrets/netrc; fi; \
    export UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1; \
    uv=${UV_BIN:-/bin/uv}; \
    "$uv" pip install --python /app/.venv/bin/python --no-deps --build-constraints constraints.txt -e .; \
    python3 -c 'import pathlib; p = pathlib.Path("/app/entrypoint.sh"); p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))'

# Ship the proprietary terms and dependency notices with every distributed image, and the notices of the standards the
# preload takes concepts from (they ship in standards/ too, beside the preload).
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/doc/kge/
COPY standards/NOTICES.md /usr/share/doc/kge/STANDARDS_NOTICES.md

# The image runs as uid 10001 and group 0, named by number, since a client's base may have no useradd. That uid owns
# build/, clients/ and deploy/ alone, group-writable, so a platform that runs it as another uid in group 0 writes them
# too; the code and the environment stay root's, read only to the jobs. A named volume mounted on build/ starts as the
# image's folder, owned so. With STORE_URL set, the entry script empties clients/ and deploy/ and pulls them from the
# object store before serve and each job command but setup-client and put-example (kge/pipeline/objstore.py), so they
# must be writable: a host's read-only mount of them is for a run without a store. HOME is a folder of that uid's
# outside build/: a uid with no passwd entry gets HOME=/, and the job's work upload takes all of build/.
RUN mkdir -p build clients deploy /home/kge \
 && chown 10001:0 build clients deploy /home/kge \
 && chmod 0775 build clients deploy /home/kge
USER 10001:0
ENV HOME=/home/kge VIRTUAL_ENV=/app/.venv PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
VOLUME ["/app/build"]
EXPOSE 8000
ENTRYPOINT ["sh", "/app/entrypoint.sh"]
CMD ["serve"]
