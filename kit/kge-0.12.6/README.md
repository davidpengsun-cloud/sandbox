# KGE release kit 0.12.6

These are the files of the release `kge-kit-v0.12.6`, with the prebuilt images, built from the same code as this repository's `demos/kge`. Use them to run KGE without building anything.

Both images are built on Red Hat's signed UBI 9 minimal bases, at their current digests: Python 3.12 for KGE, Node.js 22 for the portal. Neither runtime image carries npm, nodemon, the package installer or the pip wheel. The release's Trivy scan found no fixable HIGH or CRITICAL finding in either image; each remaining finding is listed in `images/scan-exceptions.json`, with its reason and expiry date.

GitHub refuses any file over 100 MB in a branch, so the two app images are split into parts under 50 MB. Join them before loading. Every other release file is as the release published it, and `SHA256SUMS` is the release's own. `README.md`, `join.cmd` and `.gitignore` are added here.

## 1. Join the images

On Windows, run `join.cmd` from this folder: in Command Prompt (`join.cmd`) or PowerShell (`.\join.cmd`), or by double-clicking it. It writes `images\kge-0.12.6.tar` and `images\kge-portal-0.12.6.tar` and prints their SHA-256. Compare those with the two lines for them in `SHA256SUMS`.

On Linux or macOS, or in Git Bash:

```
cat images/kge-0.12.6.tar.part-* > images/kge-0.12.6.tar
cat images/kge-portal-0.12.6.tar.part-* > images/kge-portal-0.12.6.tar
sha256sum -c SHA256SUMS
```

## 2. Load them

```
docker load -i images/kge-0.12.6.tar
docker load -i images/kge-portal-0.12.6.tar
docker load -i images/fake-gcs-server-1.56.1.tar
```

They load as `kge:0.12.6` and `kge-portal:0.12.6`, plus the Cloud Storage emulator that the Compose install runs. With Docker's containerd image store, each `.digest` file holds the image id that `docker image inspect --format '{{.Id}}'` shows once the image is loaded, as `docs/INSTALL.md` describes.

## 3. Run

Unzip `kge-0.12.6.zip` and follow the Compose section of `docs/INSTALL.md`. To upgrade from an earlier 0.12 kit, replace both images as the guide's *Upgrading within a line* describes. 0.12.4 added two optional portal settings, `PORTAL_CONTEXT_ASK_TITLE` and `PORTAL_CONTEXT_ASK_SUBTITLE`; unset, the Ask header reads as before. To upgrade an installation from a 0.11 kit, follow the guide's section *Upgrading from 0.11*. It adds four portal settings whose defaults keep today's behaviour: `PORTAL_ASK_PROVIDER`, `PORTAL_ASK_MODEL`, `PORTAL_ASK_MODEL_PROVIDER` and `PORTAL_DQ_FILTERS`. On Google Cloud, apply the new kit's Terraform to pass them. The KGE image runs the demo without a model key or a cloud account. If your registry refuses unsigned images, see the guide's section on pushing the images to your registry and signing them.

## What is here

| Path | What it is |
|---|---|
| `images/kge-0.12.6.tar.part-aa` to `-ad` | The KGE image (`docker save`, linux/amd64), split |
| `images/kge-portal-0.12.6.tar.part-aa` to `-ac` | The portal's image, split |
| `images/fake-gcs-server-1.56.1.tar` | The Cloud Storage emulator the Compose install runs |
| `images/*.digest`, `images/*.sbom.cdx.json` | Each image's id and its bill of materials (CycloneDX 1.5) |
| `images/*.trivy.json`, `images/scan-exceptions.json` | Each image's Trivy scan report, and the findings with no fix yet, each accepted until its expiry date |
| `kge-0.12.6.zip` | The pilot's Terraform, the Compose file, the `kge` helper, the operator's installation playbook (`guides/operator.md`, run with `kge playbook operator`), the agent skills and the plugin for Claude and Copilot (`plugin/`, with `check-pack`), the agent tool spec (`openapi/kge-tools.openapi.yaml`), and the invented example content (Meridian's pack 1.8.1) |
| `docs/INSTALL.md`, `docs/ONBOARDING.md` | The install guide, and a note pointing to the installation playbook and to Context's Docs, where each person finds the guides for their own work |
| `notices/` | The third-party notices of both images |
| `SHA256SUMS` | The release's checksums, for the joined images and every other file |
| `join.cmd` | Joins the image parts on Windows |

Both images carry the pilot's terms and their notices inside them: KGE in `/usr/share/doc/kge/`, the portal in `/usr/share/doc/kge-portal/`.
