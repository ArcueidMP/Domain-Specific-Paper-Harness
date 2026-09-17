# Model Compatibility

## DeepSeek V4.1 Flash — 2026-09-14

**Decision: configure `deepseek-flash`.** This is the documented canonical API
name for DeepSeek V4.1 Flash. The application accepts this explicit model only;
it does not substitute a legacy alias, a temporary route, or another model.
The September 8 assessment of an expiring beta no longer describes the current
provider release.

### Provider evidence

The official [September 10 release](https://deepseek.com/news/deepseek-v4-1-flash/)
announces the stable Flash route. The current
[model reference](https://api-docs.deepseek.com/quick_start/pricing/) identifies
`deepseek-flash` as DeepSeek-V4.1-Flash. Legacy `deepseek-v4-flash` and
`deepseek-v4-flash-vision-exp` requests now reach V4.1 Flash; their names no longer
establish that the retired V4 models generated a response.

An authenticated `GET /models` on September 14 returned `deepseek-flash` and
`deepseek-v4-pro`. A bounded synthetic JSON probe to the existing Chat
Completions endpoint returned HTTP 200, response model `deepseek-flash`, valid
`{"ok": true}`, `finish_reason=stop`, and 47 total tokens. Credentials and
complete provider responses were not recorded.

All five synthetic operation probes passed through the normal adapter methods,
without bypassing settings or output validation. Each completed in one call;
configured and provider-returned model identities were both `deepseek-flash`.

| Operation | Observed total tokens |
| --- | ---: |
| Analysis | 379 |
| Crawler plan | 412 |
| Candidate selector | 533 |
| Paper comparison | 2,170 |
| Report narrative | 1,600 |
| Total | 5,094 |

These probes validate synthetic request transport and the five structured
operation schemas. They do not establish full-paper scientific quality or
guarantee future provider availability. No production analyses were persisted.
An arXiv HTTP 429 remains a separate discovery-provider limitation; changing
the LLM model does not resolve it.

### Request and provenance boundaries

The adapter retains non-streaming Chat Completions, text-only system/user
messages, `thinking.type=disabled`, JSON output, the existing 16,000-token output
bound, response-size limits, and bounded transient retries. Paper analysis has
no tools, filesystem access, arbitrary network access, or code execution.
The provider documents a 1M context and a 384K maximum output; the application's
smaller limits remain deliberate.

Every operation validates its structured output before persistence. New
records use configured model `deepseek-flash` and retain the provider's response
model verbatim. The response may name the API route rather than an immutable
model snapshot; the application does not invent a dated model version.
Prompt versions remain unchanged because the prompts and output contracts are
unchanged.

Existing analyses, comparisons, reports, and fixture histories retain their
original provenance. Analysis identities include configured and returned model
identities; reuse requires the configured model, prompt, scope, and parser
contract to match. An old V4 analysis is therefore not silently reused as a new
Flash analysis. Changing the configuration does not rewrite historical results
or require a database migration.

### Usage estimates

The official [USD tariff](https://api-docs.deepseek.com/quick_start/pricing/),
verified September 14, lists these per-million-token prices:

| Token category | Off-peak USD | Peak USD |
| --- | ---: | ---: |
| Input, cache hit | 0.003 | 0.006 |
| Input, cache miss | 0.15 | 0.30 |
| Output | 0.60 | 1.20 |

Peak hours are Monday through Friday, 01:00–04:00 and 06:00–10:00 UTC; all other
times are off-peak. The configured 20:00, 20:20, and 20:40 Asia/Kuala_Lumpur
schedules begin off-peak.

The inspected tariff does not specify which request timestamp fixes the billing
band. The application estimates cost using normalized returned token usage and
the recorded `generated_at` time. A request spanning a peak boundary can differ
from the provider's invoice. Persisted historical estimates are unchanged, and
these amounts are estimates rather than a spending cap or exact invoice.

### Rollout

Deploy the matching Daily image and `LLM_MODEL=deepseek-flash` configuration
together after the normal adapter probes and targeted verification pass. For
this maintenance rollout, preserve the enabled 20:00/20:20/20:40 schedules and
await the next normal scheduled executions; the owner requested no manual
Daily or REPROCESS run. Image upload is performed by the owner before deployment.
Preserve prior report history and existing secret versions; the model change
does not authorize corpus deletion or unrelated platform work.
