# KGE release kit 0.11.8

These are the files of fde's release `kge-kit-v0.11.8`, with the prebuilt images. fde's Release kit workflow built them from fde commit `9868955`, the commit this branch's `demos/kge` is a copy of. Use them to run KGE without building anything.

The KGE image is built on Python's Debian slim base. The portal image is built on Red Hat's signed UBI 9 Node.js 22 minimal base.

GitHub refuses any file over 100 MB in a branch, so the two app images are split into parts under 50 MB. Join them before loading. Every other release file is as the release published it, and `SHA256SUMS` is the release's own. `README.md`, `join.cmd` and `.gitignore` are added here.

## 1. Join the images

On Windows, run `join.cmd` from this folder: in Command Prompt (`join.cmd`) or PowerShell (`.\join.cmd`), or by double-clicking it. It writes `images\kge-0.11.8.tar` and `images\kge-portal-0.11.8.tar` and prints their SHA-256. Compare those with the two lines for them in `SHA256SUMS`.

On Linux or macOS, or in Git Bash:

```
cat images/kge-0.11.8.tar.part-* > images/kge-0.11.8.tar
cat images/kge-portal-0.11.8.tar.part-* > images/kge-portal-0.11.8.tar
sha256sum -c SHA256SUMS
```

## 2. Load them

```
docker load -i images/kge-0.11.8.tar
docker load -i images/kge-portal-0.11.8.tar
docker load -i images/fake-gcs-server-1.56.1.tar
```

They load as `kge:0.11.8` and `kge-portal:0.11.8`, plus the Cloud Storage emulator that the Compose install runs. With Docker's containerd image store, each `.digest` file holds the image id that `docker image inspect --format '{{.Id}}'` shows once the image is loaded, as `docs/INSTALL.md` describes.

## 3. Run

Unzip `kge-0.11.8.zip` and follow the Compose section of `docs/INSTALL.md`. The KGE image runs the demo without a model key or a cloud account. If your registry refuses unsigned images, see the guide's section on pushing the images to your registry and signing them.

## What is here

| Path | What it is |
|---|---|
| `images/kge-0.11.8.tar.part-aa` to `-ad` | The KGE image (`docker save`, linux/amd64), split |
| `images/kge-portal-0.11.8.tar.part-aa` to `-ac` | The portal's image, split |
| `images/fake-gcs-server-1.56.1.tar` | The Cloud Storage emulator the Compose install runs |
| `images/*.digest`, `images/*.sbom.cdx.json` | Each image's id and its bill of materials (CycloneDX 1.5) |
| `kge-0.11.8.zip` | The pilot's Terraform, the Compose file, the `kge` helper, the operator's installation playbook (`guides/operator.md`, run with `kge playbook operator`), the client skills, and the invented example content (Meridian's pack 1.6.0, 59 products) |
| `docs/INSTALL.md`, `docs/ONBOARDING.md` | The install guide, and a note pointing to the installation playbook and to Context's Docs, where each person finds the guides for their own work |
| `notices/` | The third-party notices of both images |
| `SHA256SUMS` | The release's checksums, for the joined images and every other file |
| `join.cmd` | Joins the image parts on Windows |

The KGE image carries the pilot's terms inside it, in `/usr/share/doc/kge/LICENSE`.
