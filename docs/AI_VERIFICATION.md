# AI contract and evidence intelligence (P05)

> AI interprets the promise and explains the evidence. It never decides a verdict,
> never locks an agreement, and has no path to payments.

## Provider

| | |
|---|---|
| Provider | Anthropic Claude API, official Python SDK `anthropic` 1.14.0 |
| Model | `claude-opus-5` (`AI_MODEL`), effort `high` (`AI_EFFORT`), adaptive thinking (model default) |
| Structured output | `client.beta.messages.parse(output_format=<Pydantic model>)`, then re-validated with `model_validate_json` in our gateway |
| Refusal handling | `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`): if Claude Opus 5's classifiers decline, the API re-runs the request on Anthropic's recommended fallback model; `ai_calls.model_used` records who answered. A final `refusal` is a failure outcome. |
| Limits | timeout 120 s, 2 SDK retries, `max_tokens` 16 000, daily budget 1 M tokens (`AI_DAILY_TOKEN_BUDGET`) checked before every call |
| Abstraction | `ai/provider.py` `AIProvider` protocol; `ai/gateway.py` is the only caller |

Every call writes an append-only `ai_calls` row: task, prompt version, provider, model
requested/used, outcome, stop reason, latency, input/output/cache tokens, request id,
SHA-256 of the prompt and the error **class** (never the prompt, the response text of an
error, or the key).

Outcomes: `ok`, `invalid_output` (not schema-valid JSON), `timeout`, `unavailable`,
`rate_limited`, `refusal`, `not_configured`, `budget_exceeded`, `error`. Anything but `ok`
yields no AI content; callers fall back deterministically.

## P05b — Agreement drafting

`POST /agreements/drafts` (buyer_admin) → `AgreementDraftOut` (`ai/schemas.py`):
service summary, deliverables, criteria (`kind` ∈ the five P03/P04 kinds, `blocking`,
`source_quote`, `expected`, `needs_configuration`), ambiguities, manual-review items,
unsupported requirements.

The schema has **no field** for selectors, URLs, endpoints, code or payment actions —
output containing them fails validation and the whole draft is rejected.

Deterministic review (`review_draft`, pure function) then:
- drops criteria whose `source_quote` is not verbatim in the request (`SOURCE_QUOTE_NOT_IN_REQUEST`);
- drops a Lighthouse threshold the buyer did not write and asks for it instead;
- forces `needs_configuration` for contact forms (field selectors, success text);
- normalises keys, rejects duplicates.

`POST /agreements/drafts/{id}/accept` (buyer_admin) — the **human review step**: the buyer
chooses criteria, supplies configuration (validated against the P03 spec schemas; only
allow-listed keys per kind), may opt into the standard contact-form test fields, and may
change `blocking` per criterion (audited). Result: an agreement in **DRAFT** with criteria
marked `ai_proposed`. Locking still needs `propose` + buyer-approver `approve` (P03).

## P05c/d — Evidence explanations, bound to evidence

`POST /verification/runs/{id}/analysis` (anyone who can read the agreement) on a finished
run. Input: the stored results (outcome, reason code, expected spec, measurements clipped
to 300 chars/string), artifact ids/hashes and the deterministic verdict, wrapped as
untrusted data. Output: `EvidenceAnalysisOut` — per criterion an explanation with
`outcome_reported`, `evidence_refs` (`result_id`, `measurement`, `artifact_id`),
remediation, confidence and `insufficient_evidence`.

Grounding (`ground()`, deterministic) keeps a claim only if: the result id exists and
matches the criterion key; `outcome_reported` equals the **stored** outcome; it has
evidence refs (or declares insufficient evidence); every referenced result, measurement
field and artifact exists in this run. Kept explanations carry the stored outcome, not the
model's copy. A summary claiming success/payment readiness on a non-PASS verdict is
replaced with a deterministic summary. Status: `grounded`, `partially_rejected`,
`rejected`, `insufficient_evidence`, or `ai_unavailable` (deterministic fallback).

Mapping stored per analysis: **criterion → verification result → measurement/artifact →
explanation**. `verification_results` are never written by this code; analyses are a
separate append-only table.

## Prompt-injection defences

1. Untrusted content (buyer text, page-derived strings in measurements, tool output) is
   JSON-encoded inside a tag with `<`/`>` escaped — it cannot close the tag.
2. System prompts state that tagged content is data and that the model has no authority
   over verdicts or payment.
3. **Structural**: even a fully compromised model output can only produce text that is
   (a) schema-validated, (b) grounded against stored ids/outcomes, (c) stored separately.
   The AI package imports nothing from payments, PayPal or the verifier, and nothing in
   those imports the AI package (test-enforced).
4. No AI output is executed: the AI package contains no `exec`/`eval`/subprocess/raw SQL
   calls (test-enforced); criteria become data validated by the P03 spec schemas.

## Evidence classes

| | Where | What it proves |
|---|---|---|
| Mocked | `tests/test_ai.py` (ScriptedProvider) | our validation, grounding, fallbacks, injection containment — not model behaviour |
| Real model | `tests/test_ai_live.py` (`-m ai_live`), `scripts/ai_demo.py` | actual Claude calls; recorded in `ai_calls` and `docs/integration/runs/ai-demo-*.json` |
