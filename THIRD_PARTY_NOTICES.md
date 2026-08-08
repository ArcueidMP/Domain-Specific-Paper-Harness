# Third-Party Notices

No third-party source code is vendored in this repository at M1. Package and
container dependencies remain governed by their upstream licenses and are pinned
in the applicable lockfiles or image references. A complete distributable-image
license inventory is required before the M5 release gate.

## Direct integration

### arxiv.py 4.0.0

- Project: <https://github.com/lukasschwab/arxiv.py>
- License: MIT
- Integration: installed Python package behind `ArxivPort`; no source files are
  copied or modified.

The upstream distribution contains its license text. The integration and update
constraints are recorded in `docs/reuse-register.yaml`.

## Evaluated reuse candidates

PaperQA2, Scholar QA, PaSa, SPECTER2, and STORM have not been copied or integrated
at M1. Their audits must record an exact revision, license, dependency graph,
Python 3.13.13 compatibility, integration boundary, and update strategy before
any code or model artifact is added.
