# Model Compatibility

## DeepSeek V4.1 Flash assessment — 2026-09-08

**Decision: retain `deepseek-v4-flash`.** The proposed stable V4.1 identifier
is unavailable, and the accessible V4.1 identifier is explicitly temporary.
This is unsuitable as the default for unattended Daily Jobs. No automatic model
substitution or fallback is introduced.

### Evidence

The official [Chat Completions reference](https://api-docs.deepseek.com/api/create-chat-completion/)
lists `deepseek-v4-flash`, `deepseek-v4-pro`, and
`deepseek-v4-flash-vision-exp`. An authenticated `GET /models` against the existing
DeepSeek account returned these same three identifiers on the assessment date.

Two bounded, synthetic `POST /chat/completions` probes used the existing base URL
and authentication, `thinking.type=disabled`, `response_format.type=json_object`,
and `max_tokens=48`. They contained no paper, repository, or private user data.
Credentials and complete provider responses were not recorded.

| Requested model | Observed result | Meaning |
| --- | --- | --- |
| `deepseek-v4.1-flash` | HTTP 400, `invalid_request_error`; provider listed the supported V4 model names | The proposed stable identifier is not available to this account. |
| `deepseek-v4.1-flash-expires-on-0910` | HTTP 200; matching response model, valid JSON object, `finish_reason=stop`, 49 total tokens | Basic request and response compatibility works for this temporary route only. |

The temporary model name contains an explicit September 10 expiry. A supported
stable replacement and its lifecycle are not documented in the inspected
reference or model catalog. Successful JSON generation is not evidence that a
scheduled service can continue using this route after that date.

### Compatibility boundary

The existing adapter uses non-streaming Chat Completions, system/user messages,
disabled thinking, JSON output, bounded completion tokens, response model
provenance, and token-usage accounting. It validates the resulting analysis,
crawler, selector, comparison, and report objects before persistence.

The small probe validates transport and basic JSON compatibility, not the full
production output budget, grounded analysis quality, all five operation schemas,
pricing, or long-term model availability. Production settings, model allowlist,
prompt versions, images, and persisted historical provenance remain unchanged.

Reassess when a stable V4.1 identifier is officially available. Before changing
the default, verify its limits and pricing, run representative requests through
each existing adapter operation, and retain returned model identities on newly
generated records without rewriting earlier analyses.
