# AI contract and evidence intelligence (P05)

> AI interprets the promise and explains the evidence. It never decides a verdict, never
> locks an agreement, never counts as human approval, and has no path to payments.

## Model and provider

| | |
|---|---|
| Provider | Anthropic Claude API, official Python SDK `anthropic` 1.14.0 |
| Default model | **`claude-sonnet-5-5`** (Claude Sonnet 5.5) — `AI_MODEL` |
| Comparison model | `claude-opus-5` (set `AI_MODEL=claude-opus-5`); `claude-sonnet-5` also priced |
| Verification of the model | Models API on 2026-10-15: `claude-sonnet-5-5` available, created 2026-09-28, 1M input / 128K output, `structured_outputs` supported; listed in the structured-outputs supported-models list |
| Effort | `high` (`AI_EFFORT`); adaptive thinking is the model default |
| Structured output | `client.messages.parse(output_format=<Pydantic model>)`, then re-validated with `model_validate_json` |
| Server-side fallback | **off** (`AI_SERVER_FALLBACK=false`) so proofs are reproducible; `ai_calls.model_used` records the model ID the API returned. Opt-in path uses `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) |
| Request limits | `max_tokens` 12 000 (`AI_MAX_OUTPUT_TOKENS`), SDK timeout 120 s, `AI_MAX_RETRIES=1` |

## Pricing and spending controls (local estimates, not billing)

Prices (USD / MTok) from <https://platform.claude.com/docs/en/about-claude/pricing>, read
2026-10-15 and stored with that source and date in `ai/pricing.py`
(override/extend with `AI_PRICING_JSON`):

| Model | Input | Output | 5m cache write | Cache hit |
|---|---|---|---|---|
| claude-sonnet-5-5 | 2 | 10 | 2.50 | 0.10 |
| claude-sonnet-5 | 2 | 10 | 2.50 | 0.20 |
| claude-opus-5 | 5 | 25 | 6.25 | 0.50 |

Per call (`ai/gateway.py`):

1. **Unknown price → fail closed** (`pricing_unavailable`, no call).
2. **Reserve the worst case** = (conservative input estimate × input price + `max_tokens`
   × output price) × (1 + retries). The input estimate is 1 token per 2 bytes of prompt +
   schema + 1 500 tokens, deliberately above real tokenisation.
3. The reservation is inserted under a Postgres advisory lock after checking that today's
   settled + outstanding reservations + this reservation ≤ `AI_DAILY_BUDGET_USD` (and the
   token budget). Otherwise → `budget_exceeded`, no call. Concurrent requests serialise on
   the lock, so they cannot jointly exceed the limit (tested with 6 overlapping calls and
   room for 3).
4. After the call the reservation is **settled** from reported usage (input, output, cache
   read/write tokens × price). **No usage reported → the full reservation is kept.** A
   reservation that is never settled (crash) keeps counting — fail closed.
5. `ai_calls` records `reserved_cost_usd`, `estimated_cost_usd` and the price row used.
   Reservations are append-only except the single reserved → settled transition (trigger).

**Limits of local enforcement:** estimates follow the published list price and reported
usage; they are not Anthropic invoices, ignore taxes/discounts, and only cover calls made
through this gateway and database. **Set an account-level spend limit in the Anthropic
Console as the authoritative safeguard.**

## P05b — Agreement drafting

`POST /agreements/drafts` (buyer_admin) → `AgreementDraftOut`. Each criterion has
`origin`: **`stated`** (the buyer asked for it; verbatim `source_quote`) or **`suggested`**
(the model's own recommendation with a `rationale`). The schema has no field for
selectors, URLs, endpoints, code or payment actions; such output fails validation.

Deterministic review (`review_draft`, pure):
- quote not verbatim in the request → rejected (`SOURCE_QUOTE_NOT_IN_REQUEST`);
- a "stated" criterion whose quote does not express its kind → **reclassified as a
  suggestion** (`KIND_NOT_STATED_IN_QUOTE`) — an inference is never shown as a buyer
  instruction;
- a stated requirement phrased as mandatory ("must", "shall", …) stays **blocking**
  (`BLOCKING_RESTORED`) — the model cannot weaken it;
- thresholds the buyer did not write are removed (`THRESHOLD_NOT_IN_REQUEST`) and asked for;
- contact forms always require buyer-supplied fields and success text.

`POST /agreements/drafts/{id}/accept` (buyer_admin) is the human review step: stated
criteria are included (optionally filtered), **suggestions only when named in
`accept_suggestions`**, configuration is validated against the P03 spec schemas, and any
blocking change is explicit (`blocking_overrides`, audited as `weakened_blocking`). The
result is a DRAFT agreement; locking still requires propose + buyer-approver approval.

## P05c/d — Evidence explanations, bound to evidence

Mapping per analysis: **agreement criterion → verification result → measurement /
artifact → AI explanation**. Grounding (`ground()`) keeps a claim only if its result id is
in this run, the criterion key matches, `outcome_reported` equals the stored outcome, and
every referenced result, measured/top-level field and artifact belongs to this run. Kept
explanations display the **stored** outcome.

Deterministic additions, independent of the model:
- `uncertainty` on every result without a positive measurement (manual review, error,
  not run) and an `uncertainties` entry for every criterion without a surviving explanation;
- `pending_human_review`: blocking `manual_review_required` criteria, `decision: null` —
  AI commentary never fills it (P06 adds the authorized decision);
- a summary that asserts success or payment readiness on a non-PASS verdict ("fully
  verified", "ready for payment", …) is replaced; a **negated** phrase in the same clause
  ("should not be considered fully verified") is not a claim.

Verification results are never written by AI code; analyses are a separate append-only
table. AI unavailable/invalid → deterministic summary of the stored results.

## Prompt-injection defences

1. Untrusted content is JSON-encoded inside a tag with `<`/`>` escaped (no tag break-out).
2. System prompts mark tagged content as data; the model has no authority over verdicts
   or payment.
3. Structural: schema-limited output, grounding against stored ids/outcomes, separate
   storage, no import path from the AI package to payments/PayPal/verifier (test), no
   `exec`/`eval`/subprocess/SQL in the AI package (test).

## Evidence classes

| | Where | Proves |
|---|---|---|
| Mocked | `tests/test_ai.py`, `tests/test_ai_controls.py` (ScriptedProvider) | our validation, grounding, budget, fallbacks, injection containment |
| Real model | `tests/test_ai_live.py` (`-m ai_live`), `scripts/ai_demo.py` | actual Claude behaviour; see INTEGRATION_PROOF.md and `docs/integration/runs/README.md` |
