# The portal image (issue #173 requirement 8). The base is a build argument, so a client builds on its own Node base,
# which needs npm, sh and Node 22.12 or later (the UI's bundler, rolldown, needs 22.12; the server's node:sqlite, 22.5),
# and nothing else: no node user, no package manager.
# The default is Red Hat's signed UBI 9 Node.js 22 minimal base (issue #176), its RHEL 9.8 tag held to its multi-arch
# index digest, so that a registry whose policy admits only signed bases admits it. That base runs as uid 1001 by
# default, in /opt/app-root/src, with CMD /bin/bash: each stage switches to uid 0 for npm ci, whatever the base's default
# user, the image runs as uid 1000 in group 0, set by number, not by a named user, and it sets its own ENTRYPOINT, which
# clears the base's CMD, so neither the base's entrypoint nor its CMD is inherited.
#
# A client's package sources are build arguments, not edits: npm_config_registry (empty: the public npm registry; npm
# rewrites the lockfiles' public-registry URLs to it) and CA_BUNDLE (a path, inside the base, to a CA bundle that
# includes the corporate CA; npm reads it as its cafile, which replaces npm's own list, and Node as
# NODE_EXTRA_CA_CERTS). Both reach the npm ci steps alone and never ENV, so kit/build.sh's guard still refuses an
# image that trusts a build-time CA. Credentials enter only as the BuildKit secret npmrc, an .npmrc file
# (--secret id=npmrc,src=<file>), never as a build argument, whose value the image history keeps.
ARG BASE_IMAGE=registry.access.redhat.com/ubi9/nodejs-22-minimal:9.8@sha256:dcbe5b45a3e7591a52fe4ae0092a39910924092feee749c040424a7a4519ffd2

FROM ${BASE_IMAGE} AS ui
USER 0
ARG npm_config_registry
ARG CA_BUNDLE
# Each stage checks Node first. The UI's bundler, rolldown, needs Node 22.12 or later (its native binding's engines), and
# npm ci skips that binding on an older Node, so the UI build would fail with a module not found; this says why instead.
RUN node -e "const [a, b] = process.versions.node.split('.').map(Number); process.exit(a > 22 || (a === 22 && b >= 12) ? 0 : 1)" \
    || { echo "the portal's UI build needs Node 22.12 or later (rolldown, its bundler); this base has Node $(node --version)" >&2; exit 1; }
WORKDIR /build/ui
COPY ui/package.json ui/package-lock.json ./
RUN --mount=type=secret,id=npmrc,required=false \
    if [ -n "${CA_BUNDLE:-}" ]; then export NODE_EXTRA_CA_CERTS="$CA_BUNDLE" npm_config_cafile="$CA_BUNDLE"; fi; \
    NPM_CONFIG_USERCONFIG=/run/secrets/npmrc npm ci
COPY ui/index.html ui/vite.config.mjs ./
COPY ui/public ./public
COPY ui/src ./src
RUN npm run build

FROM ${BASE_IMAGE}
USER 0
ARG npm_config_registry
ARG CA_BUNDLE
ENV NODE_ENV=production PORT=8080 PORTAL_UI_PATH=/app/ui
# The server opens its store with node:sqlite under the flag the ENTRYPOINT passes, so a base older than Node 22.5
# fails here, naming the version, not at the portal's first start.
RUN node --experimental-sqlite -e "new (require('node:sqlite').DatabaseSync)(':memory:').exec('select 1')" \
    || { echo "the portal needs Node 22.5 or later, for node:sqlite under --experimental-sqlite; this base has Node $(node --version)" >&2; exit 1; }
WORKDIR /app/server
COPY server/package.json server/package-lock.json ./
RUN --mount=type=secret,id=npmrc,required=false \
    if [ -n "${CA_BUNDLE:-}" ]; then export NODE_EXTRA_CA_CERTS="$CA_BUNDLE" npm_config_cafile="$CA_BUNDLE"; fi; \
    NPM_CONFIG_USERCONFIG=/run/secrets/npmrc npm ci --omit=dev && npm cache clean --force
COPY server/src ./src
COPY server/contract ./contract
COPY --from=ui /build/ui/build /app/ui
# What the server writes: /data (the Compose volume, PORTAL_SQLITE_PATH=/data/portal.db) and build/ under its working
# directory (the default PORTAL_SQLITE_PATH). Both are owned by the run-time uid and group 0 and group-writable, so a
# platform that runs the image under another uid in group 0 can write them too. The code stays root's. The uid is 1000,
# the old image's node user's, so a portal-data volume that image wrote stays writable after an upgrade.
RUN mkdir -p /data /app/server/build && chown -R 1000:0 /data /app/server/build && chmod -R g+rwX /data /app/server/build
USER 1000:0
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||8080)+'/healthz').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))"
ENTRYPOINT ["node", "--experimental-sqlite", "src/index.js"]
