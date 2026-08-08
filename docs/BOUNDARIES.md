# Product and Trust Boundaries

## Product scope

The product covers planning, reasoning, memory, tool use, web and computer-use
agents, multi-agent coordination, evaluation, and agent safety where an LLM-agent
workflow is material. It excludes generic chatbots, pure RAG, conventional
reinforcement-learning agents, agent-based simulation, non-LLM chemical or
biological agents, and embodied systems without a material LLM-agent component.

## Source boundaries

- Daily discovery uses arXiv only.
- arXiv metadata and PDF URLs enter through `ArxivPort`; application code owns
  query construction, cursors, overlap, version identity, and persistence.
- Semantic Scholar and PaSa-derived historical search begin in M3 and may not
  become daily discovery providers.
- Publisher scraping, paywall bypass, publisher PDF download, arbitrary web
  search, and hidden metadata providers are prohibited.
- Only arXiv-hosted PDFs may enter full-text analysis.

## Runtime boundaries

- FastAPI is read-oriented. It does not schedule work, perform migrations, or
  expose a public execution endpoint.
- Manual work enters through the project CLI, `scripts/run-daily.ps1`, or
  `gcloud run jobs execute`.
- The browser calls only the web/API service and never receives database or
  service-role credentials.
- API and Daily runtimes share only the PostgreSQL contract and domain schemas;
  they are independently deployed Cloud Run units.
- GROBID is the only scientific PDF parser and remains a separately authenticated
  private service when M2 deploys it.

## Code boundaries

Ports exist only for arXiv, scholarly search, LLM, PDF parsing, evidence
processing, and persistence. Deterministic normalization, mapping, ranking, and
aggregation remain ordinary typed functions. Production wiring cannot import
test doubles.

The allowed dependency direction is adapter to port to application to domain.
Domain code cannot depend on FastAPI, SQLAlchemy, arxiv.py, GROBID, DeepSeek,
Semantic Scholar, PaperQA2, or Google Cloud libraries.

## Trust boundaries

Titles, abstracts, author names, PDFs, TEI, citations, and model output are
untrusted data. Instructions found in papers are never executable instructions.
Paper analysis receives no shell, arbitrary network, code execution, or
filesystem-write access. External responses are validated before domain objects
are constructed or transactions begin.

Production has no implicit fallback. In particular, PostgreSQL cannot become
SQLite or files; GROBID cannot become another parser; DeepSeek cannot become
another model; full-text failure cannot become abstract-only analysis; and
malformed model JSON cannot be repaired heuristically. Test fixtures and explicit
preselected modes are not production fallbacks.

## Cloud boundary

The deployed browser boundary is direct Cloud Run IAP with an explicit owner
allowlist. Runtime resources are disabled in Terraform until immutable images and
an enabled production database secret exist. There is no public unauthenticated
binding, exported service-account key, paid load balancer, Cloud SQL instance,
Kubernetes cluster, VPS, Redis, or permanent worker.
