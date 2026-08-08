# Third-Party Notices

No third-party source code is vendored in this repository. Package and container
dependencies remain governed by their upstream licenses and are pinned in the
applicable lockfiles or image references. A complete distributable-image license
inventory remains required before the M5 release gate.

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
- License: Apache-2.0
- Integration: the external `grobid/grobid:0.9.0-crf` container is wrapped with
  first-party provenance labels and called through `PdfParserPort`; no upstream
  source or model files are copied or modified in this repository.

The Docker Hub manifest was independently pulled and inspected on 2026-08-08 as
`sha256:24ba90eb1c959f65d812bcdb2cf79c677fa5fd7b95235de616b8bc9fa1317849`.
Cloud deployment still requires the first-party wrapper to be mirrored into
Artifact Registry and referenced by its resulting immutable digest. The upstream
image contains its own transitive components; their complete image-license
inventory is part of the M5 release review.

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

Scholar QA, PaSa, SPECTER2, and STORM have not been copied or integrated. Their
milestone-specific audits must record an exact revision, license, dependency
graph, Python 3.13.13 compatibility, integration boundary, and update strategy
before any code or model artifact is added.
