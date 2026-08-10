# Current Status

## Current milestone

M3 - PaSa and Semantic Scholar comparison is complete locally. M1 ingestion and
M2 structured analysis remain intact on exact CPython 3.13.13. M4 has not
started.

## Completed capabilities

- The authenticated Semantic Scholar adapter provides bounded paper search,
  metadata, arXiv source mapping, references, citations, and recommendations.
  Production scholarly operations require `SEMANTIC_SCHOLAR_API_KEY`; Web/API
  remains keyless and read-only.
- The first-party PaSa-derived Crawler/Selector persists exact source analysis
  and year scope, generated Crawler decisions, actions, candidates, discovery
  provenance, score components, Selector decisions, model usage, limits, and
  terminal stop reasons. No PaSa code, prompts, models, or providers are copied.
- Six-month backfill persists its exact query plan, resumable cursor, normalized
  external identities, corpus membership, representative ranks, and complete
  embedding contract. Explicit retries resume the same failed window only with
  identical configuration.
- v0.1 deliberately uses official `allenai/specter2_base` revision
  `3447645e1def9117997203454fa4495937bfbd83`, with the tokenizer at the same
  revision. Title + separator token + abstract is truncated to 512 tokens; the
  unnormalized final-layer CLS vector has dimension 768.
- The SPECTER2 Base runtime is Daily-only and pins Transformers 5.3.0, CPU
  PyTorch 2.13.0, Hugging Face Hub 1.27.0, and safetensors 0.8.0. Build-time
  preparation verifies the official source weight SHA-256, loads with
  `trust_remote_code=False` and `weights_only=True`, converts to safetensors,
  and writes a strict manifest. Runtime loading is local-only and offline.
- PostgreSQL/pgvector persists model and tokenizer identifiers/revisions,
  dimension, preprocessing contract, model provenance, source, and
  `generated_at` with every embedding. Retrieval requires the complete matching
  contract; there is no generic, commercial, adapter, or alternate-model
  fallback.
- Evidence-linked comparisons pin both exact analysis IDs/scopes, fixed ordered
  dimensions, comparability, evidence, usage, and relation provenance in one
  atomic bundle. The API and React views expose session/action provenance,
  candidate scores, comparison evidence, and clearly labelled AI inference.
- PaSa and Ai2 Scholar QA remain architecture-only audits. The SPECTER2
  proximity adapter is intentionally not used in v0.1 because Adapters 1.3.0
  requires Transformers 4.57.x, below the project's patched Transformers 5.3+
  security floor. Adopting it later requires a new explicit architecture
  decision after upstream compatibility exists.

## Verification

- `scripts/verify.ps1` and the command paths used by GitHub Actions pass with
  exact CPython 3.13.13, frozen locks, Ruff, formatting, Pyright, generated API
  contracts, Python/frontend/Playwright tests, Compose, Terraform, Alembic,
  real PostgreSQL/pgvector repositories, and model-free CI images.
- The explicit real SPECTER2 Base smoke passed locally: the pinned model loaded
  under Transformers 5.3.0, returned finite 768-dimensional vectors, repeated
  deterministic evaluation was identical, and real pgvector persistence plus
  cosine retrieval succeeded.
- The model-bearing Daily production target built successfully, loaded its
  baked artifact offline as the non-root runtime user, and produced a finite
  768-dimensional vector. The measured local image size is 785,573,507 bytes.
- Semantic Scholar fixture, malformed-response, authentication, pagination,
  retry, rate, and bounded-search tests pass. Its opt-in live smoke is skipped
  because `SEMANTIC_SCHOLAR_API_KEY` is not configured.

## Deployment

- No foundation, Web/API, Daily, GROBID, Scheduler, secret version, or endpoint
  is deployed. The configured target remains the existing GCP project in
  `asia-southeast1`.
- Deployment builds the explicit model-bearing Daily target. Its pinned model
  is prepared once in the image; each Job runs offline and does not redownload
  weights. Normal CI builds the model-free Daily target.
- Terraform can attach a fixed Semantic Scholar secret version only to the
  Daily service account. Web/API receives neither Semantic Scholar nor DeepSeek
  credentials.

## Current blockers

- `SEMANTIC_SCHOLAR_API_KEY` is intentionally not configured. This blocks the
  opt-in live adapter smoke and real historical calls, but not M3 deterministic
  verification, the read API, or the completed implementation.
- Production deployment still requires owner-supplied PostgreSQL, DeepSeek, and
  Semantic Scholar secret versions as applicable, plus successful Google API
  connectivity. These are deployment blockers, not M3 implementation blockers.

## Next milestone

M4 - Knowledge graph, trends, reports, and product UI. M4 has not started.
