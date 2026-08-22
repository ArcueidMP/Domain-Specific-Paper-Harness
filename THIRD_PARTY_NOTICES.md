# Third-Party Notices

No third-party source code is vendored in this repository. Package and container
dependencies remain governed by their upstream licenses and are pinned in the
applicable lockfiles or immutable image references. The Daily production build
embeds a hash-verified conversion of the approved SPECTER2 Base model artifact;
no model weights are checked into Git. Production images are published only to
the private Artifact Registry and are deployed by immutable digest.

## M5 package-license review

`scripts/check_dependency_licenses.py` is the deterministic, credential-free
package review. It traverses the installed first-party Python production
closure, including the `specter2` extra, and reads SPDX or classifier metadata
from each installed distribution. It also reads pnpm's production license
inventory with Corepack and npm network access disabled. Output is restricted
to package, version, and license. Known incompatible AGPL, GPL, SSPL, BUSL,
Commons-Clause, proprietary, and related terms fail verification; missing or
new metadata remains visible for review without becoming a format-only blocker.

The exact 2026-08-10 local review accepted the locked Python and bundled
frontend closures. The approved set is permissive except for the reviewed
MPL-2.0 components and the unmodified `psycopg` and `psycopg-binary` libraries
under LGPL-3.0-only. Their installed `.dist-info/licenses` files are retained in
the Python virtual environment copied into the runtime images. CI repeats the
Python gate on the production Linux platform and repeats the frontend gate after
the frozen pnpm install.

`scripts/export_frontend_licenses.mjs` converts pnpm's installed production
inventory into a path-free manifest and copies every discovered license,
notice, or copying file beside it. Package roots must resolve inside the installed
`.pnpm` store and match the declared package identity; output is confined to a
new directory beneath the repository/build root. The final Web/API image carries
this material under `/opt/licenses/frontend` and carries this notice under
`/opt/licenses/paper-harness`. Direct SPECTER2 and GROBID Apache-2.0 texts and
upstream image material are retained in their respective runtime images.

This focused result is not an image-wide legal attestation. An OS-package SBOM
was not generated, and installed package metadata does not enumerate every
component embedded in native wheels or the upstream Java image. Runtime images
retain their operating-system and upstream notices. No incompatible license
was found in the focused review. The residual lack of a scanner-generated SBOM
is an accepted limitation for this private, unmodified internal deployment,
not a publication blocker. Dependency, base-digest, or distribution-model
changes require a renewed review.

## Direct integration

### arxiv.py 4.0.0

- Project: <https://github.com/lukasschwab/arxiv.py>
- License: MIT
- Integration: installed Python package behind `ArxivPort`; no source files are
  copied or modified.

The upstream distribution contains its license text. The integration and update
constraints are recorded in `docs/reuse-register.yaml`.

### GROBID 0.9.0 CRF runtime

- Project: <https://github.com/grobidOrg/grobid>
- Release: <https://github.com/grobidOrg/grobid/releases/tag/0.9.0>
- Release commit: `b2251cb`
- License: Apache-2.0
- Integration: the external `grobid/grobid:0.9.0-crf` container is wrapped with
  first-party provenance labels and a health contract, then called through
  `PdfParserPort`; no upstream source or model files are copied or modified in
  this repository.

The Docker Hub manifest was independently pulled and inspected on 2026-08-08 as
`sha256:24ba90eb1c959f65d812bcdb2cf79c677fa5fd7b95235de616b8bc9fa1317849`.
Cloud deployment still requires the first-party wrapper to be mirrored into
Artifact Registry and referenced by its resulting immutable digest. The upstream
image contains its own transitive components. The retained upstream and direct
notice review is the M5 release evidence; it is not a complete SBOM, as recorded
in the accepted limitations.

### SPECTER2 Base

- Model: <https://huggingface.co/allenai/specter2_base>
- Revision: `3447645e1def9117997203454fa4495937bfbd83`
- Upstream project: <https://github.com/allenai/SPECTER2>
- License: Apache-2.0
- Integration: the Daily production image downloads the exact official revision
  during its explicit model-preparation build target, verifies the pinned source
  weights, converts them to safetensors, and loads the resulting artifact offline.

No upstream Python source is copied or modified. The SPECTER2 proximity adapter
is not distributed in the initial release. The Daily image includes this notice
and the Apache-2.0 license text under `/opt/licenses`; model provenance and update
constraints are recorded in `docs/reuse-register.yaml`.

## Evaluated reuse candidates

### PaperQA2 2026.3.18

- Project: <https://github.com/Future-House/paper-qa>
- Revision: `v2026.03.18`
- Commit: `ac4ff91ad703e6816cb620ea579a98ca0c42c36f`
- License: Apache-2.0
- Decision: reviewed but not installed, copied, or integrated.

The lower-level chunking and evidence path was rejected for M2 because it is
coupled to a broad provider and retrieval dependency graph, defaults to PyPDF and
OpenAI-backed behavior, owns a separate index and MD5 identities, repairs invalid
JSON, and does not preserve the GROBID section and coordinate provenance required
by this product. The detailed compatibility record and update conditions are in
`docs/reuse-register.yaml`.

### PaSa

- Project: <https://github.com/bytedance/pasa>
- Commit: `2aaa6a9b1e48d24a2b7e21e8551f863dad9eeb84`
- License: Apache-2.0
- Decision: architecture-only review; no package, source, prompt, checkpoint,
  training code, custom fork, or database is installed, copied, or invoked.

### Ai2 Scholar QA 0.8.13

- Project: <https://github.com/allenai/ai2-scholarqa-lib>
- Commit: `db1fdf3746d6ae338473f0176110082228ee8635`
- License: Apache-2.0
- Decision: architecture-only review; no package, source, prompt, provider,
  retriever, reranker, or model artifact is installed, copied, or invoked.

### STORM / knowledge-storm 1.1.1

- Project: <https://github.com/stanford-oval/storm>
- Audited commit: `fb951af7744dab086e34962e9bc6fe878e145f83`
- License: MIT
- Decision: architecture-only review; no STORM package, source, prompt,
  retriever, provider wrapper, embedding stack, or filesystem persistence is
  installed, copied, or invoked.

The STORM audit recorded the exact commit, release/sdist provenance, dependency
graph, CPython 3.13.13 packaging/import smoke, incompatible source/retrieval
boundaries, and update strategy. M4 reimplements only the coverage-aware
outline-to-section concept as first-party typed synthesis over persisted local
records and Evidence IDs. Full details for all three reviews are in
`docs/reuse-register.yaml`.
