# Integration run transcripts

Raw outputs of real runs. Nothing here is simulated. Transcripts contain no credentials,
tokens or account identifiers. Read each one with the label below.

| File | What it is | Status |
|---|---|---|
| `sandbox-proof-20261015T104415Z.json` | Real PayPal Sandbox lifecycle: $10 authorize → capture → $2 partial refund | **valid evidence** (P02) |
| `verification-demo-20261015T123029Z.json` | Real P04 verification of the broken/fixed fixtures (template agreement, no AI) | **valid evidence** (P04) |
| `container-isolation-20261015T122912Z.json` (+ `.worker.json`) | 17 in-container isolation probes | **valid evidence** (P04) |
| `ai-demo-20261015T131618Z.json` | First P05 demo, `claude-opus-5`, fallback enabled. Both analyses `partially_rejected` because of a **validator bug** (refs to the result's own `measured` field were rejected). Accessibility made non-blocking by the buyer. | **historical — shows the bug**, superseded |
| `ai-demo-20261015T131921Z.json` | P05 demo after the validator fix, `claude-opus-5`. Fixed page **PASS only because the buyer explicitly made accessibility non-blocking** (audited). | **alternative scenario — buyer-approved relaxed criteria**; not the canonical demo |
| `ai-demo-canonical-20261015T134325Z.json` | Canonical demo, `claude-sonnet-5-5`, fallback off. Correct verdicts (FAIL / NEEDS_MANUAL_REVIEW), but the fixed-page summary was replaced because the **success-claim check ignored negation** ("should not be considered fully verified"). | **historical — shows the second bug**, superseded |
| `ai-demo-canonical-20261015T134532Z.json` | **Canonical P05 demo.** `claude-sonnet-5-5`, fallback off. Accessibility stays a blocking manual review: broken → FAIL, fixed → NEEDS_MANUAL_REVIEW, both analyses grounded with 0 rejected claims, pending human review shown, no approval fabricated, no payment action. | **canonical evidence** (P05) |
