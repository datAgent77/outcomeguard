# P07 — Product UI verification

Date: 2026-10-16. Scope: the web application in `apps/web` (Next.js 16, TypeScript strict,
Tailwind 4, AG Grid Community 36) on top of the P01–P06 API. Every screenshot below was
taken by Playwright from the running application against the real local API — none is a
mockup.

## Screens and routes

| Route | Screen | Who |
|---|---|---|
| `/` | Landing page: hero, five stages, schematic (labelled "Illustration") verification/payment card, AI-never-authorizes and authorization-≠-capture statements | public |
| `/login` | Password login; expired-session and signed-out notices | public |
| `/dashboard` | Buyer dashboard: 7 state tiles (counts from real records only) that filter the agreement list; empty/error states | buyer org members |
| `/provider` | Provider workspace: assigned agreements, delivery/verification/payment state | merchant org members |
| `/agreements/new` | Wizard: describe → review AI criteria (stated vs suggested, opt-in) → configure verification → propose + approve & lock | buyer admin |
| `/agreements/[id]` | Workspace with tabs **Overview · Acceptance Criteria · Delivery & Verification · Evidence · Payment · Activity**; header keeps agreement, verification and payment as three separate statuses | buyer + provider members |
| `/ledger` | Payment & Evidence Ledger (AG Grid Community) with server search/filters, client sort/filter/pagination, detail drawer, timeline view (automatic below 768 px) | everyone with a session |

## Architecture (BFF)

- The browser never holds an API token. `POST /api/session/login` exchanges credentials
  with the API and stores the opaque session token in an **HttpOnly, SameSite=Lax** cookie
  (`Secure` on HTTPS / production), max-age = the API's 8 h session.
- `/api/og/*` is a same-origin proxy that attaches the token. It only forwards an
  allow-list of product paths (`/dev/*`, webhooks, the form sink and rehearsal pages are
  not reachable); a 401 from the API clears the cookie and the UI redirects to
  `/login?expired=1`.
- CSRF: every mutation must carry the `x-og-csrf: 1` header **and** an `Origin` equal to
  the host; otherwise 403. Unit-tested in `e2e/mocked/bff.spec.ts`.
- `src/proxy.ts` only redirects cookie-less requests for app routes to `/login`
  (optimistic); the API authorizes every call. Hidden buttons are never the boundary.
- All eligibility, review and capture decisions come from the API (P06). The UI renders
  policy responses and error codes; it contains no financial logic.

## Backend additions (all read-only except two small, tested write-path fixes)

| Endpoint / change | Purpose |
|---|---|
| `GET /workspace/context` | user, kind (human/service), demo-account flag, organizations with roles |
| `GET /workspace/merchants` | merchant accounts of the active payment environment (no PayPal ids) |
| `GET /workspace/agreements[/{id}]` | per-agreement roll-up: version, latest/latest-completed run with outcome counts and un-reviewed manual items, latest order/authorization/capture and the **last persisted** policy decision (no new evaluation) |
| `GET /agreements/{id}/payments` | stored orders for an agreement (PayPal is re-read by the existing `GET /payments/orders/{id}`) |
| `GET /payments/authorizations/{id}/capture-approvals` | approvals with approver, bound inputs hash, consumed flag |
| `GET /workspace/ledger` | organization-scoped, paginated view of the hash-chained audit log, enriched with agreement/version/run/criterion/policy/payment/amount/evidence/transaction references and honest provenance (human account / demo account / service account / system; `scripted` only when the persisted `SCRIPTED DEMO DECISION` marker is present); policy previews hidden unless requested; `payment_environment` marks simulated payments |
| `RunOut` + `criterion_id`, `form_receipts`; `ReviewOut` + `reviewer_email` | evidence viewer and manual-review panel |
| `POST /agreements/drafts/{id}/accept` accepts `accept_suggestions` | the service supported suggestion opt-in since P05, but the route did not pass it — fixed |
| `POST /agreements/{id}/payments` honours `Idempotency-Key` | a double click or retry returns the same PayPal order instead of creating a second one (`idempotency_records`) |
| `scripts/seed_demo.py` | sets the operator-supplied demo password from `DEMO_PASSWORD` (never printed) |

Tests: `apps/api/tests/test_workspace_api.py` (3 tests: scoping/404s, idempotent order
creation, ledger provenance and filters, suggestion opt-in over HTTP).

## Browser tests

### Mocked API (`e2e/mocked`, runs in CI) — 36 passed

Every `/api/og/*` call is answered by fixtures in `e2e/mocked/fixtures.ts` (ids prefixed
`mock-`). Covered: login/CSRF header/no token in JS storage, expired session, logout,
role-aware navigation (buyer admin, reviewer, provider), wizard (stated vs suggested,
default-off suggestions, explicit opt-in, blocking-weakening as an explicit decision,
nothing invented in configuration, exact accept payload), AI failure and API error
states, approve-and-lock confirmation quoting the content hash, evidence report
(measurements, reason codes, artifact viewer only shows API data, AI text labelled as
interpretation, AI outage does not block the report), manual review (rationale +
attestation + confirmation, stale-evidence rejection, no action for provider or service
account, scripted decisions labelled), payment (blocked reasons in plain English,
approval bound to inputs hash, capture as a separate step, double-click sends one
request, UNKNOWN never shown as failed, guarded reconcile, idempotency key on checkout,
approval URL taken from the API), ledger grid (server filters, provenance, drawer),
empty/error states, BFF path allow-list and CSRF rules, axe-core WCAG 2 A/AA scan of
landing, login, dashboard, evidence and payment screens, and layouts at 1440/1280/768/
390/375 px (no horizontal scroll, ≥ 44 px touch targets, timeline instead of grid on
mobile).

### Real API (`e2e/integration/full-flow.spec.ts`) — 8 passed (≈ 1 min)

Stack: web (`next start`) → BFF → API (`PAYMENT_MODE=rehearsal`) → PostgreSQL; the
verification worker with real Chromium 156 + Lighthouse 13.5 against the local fixture
pages; the real AI provider (`claude-sonnet-5-5`) for drafting. Payments are
**SIMULATED** in this run and labelled so in the UI and ledger; no PayPal call is made.

1. Buyer (alice) drafts criteria from the canonical request with the real model, reviews,
   configures (standard form fields opted in, HTTPS off for the local http fixtures),
   proposes, approves and locks.
2. Provider (maya) submits the broken page → **FAIL**, contact form FAIL; the provider has
   no review action.
3. Provider submits the fixed page → **NEEDS_MANUAL_REVIEW**; a stored screenshot opens
   from the evidence store.
4. Buyer starts checkout (simulated), approves on the rehearsal page, authorizes; the
   policy shows "Waiting for human review" and no approval action.
5. Reviewer account (rita) records the accessibility decision. **This is a scripted test
   decision**: its rationale starts with `SCRIPTED DEMO DECISION`, and the UI shows
   "Scripted demo decision — not a human review".
6. Buyer approver (bob) approves capture, then captures (simulated); the fresh policy then
   reports `ALREADY_CAPTURED` and no capture action remains.
7. Ledger shows the persisted trail with provenance; detail drawer; activity timeline.
8. Same data at 390 px.

Each step fails on any browser console error or uncaught page error.

### Real PayPal Sandbox through the UI

`e2e/integration/sandbox-prep.spec.ts` (opt-in) prepared, against the API in
`PAYMENT_MODE=sandbox`, a locked agreement with the fixed page verified and the scripted
review recorded — and stops before any payment action. A person then performed the
checkout, the PayPal Sandbox buyer approval, authorization, capture approval and capture in
the UI (2026-10-16): order `3PE754440H8280847`, authorization `7HU39463K8073954H`,
capture `70F43933LT884351X` ($500.00, COMPLETED, confirmed by re-reading PayPal). The
accessibility review in that run was the scripted preparation decision, labelled as such.
Full record: INTEGRATION_PROOF.md, "Real PayPal Sandbox through the UI".

## Accessibility

- Semantic landmarks, skip link, labelled form controls, keyboard-operable tabs (arrow
  keys), native `<dialog>` (focus trap, Escape), visible focus ring, `prefers-reduced-motion`.
- axe-core scans found real contrast defects (secondary text tokens); fixed by darkening
  `ink-500` and removing `ink-400` text. Scans now report no serious/critical violations.
- Automated scans are not a WCAG conformance claim; no manual screen-reader audit was done.

## Screenshots (`docs/p07/screenshots`)

Desktop 1440 px: `01-buyer-dashboard`, `02-wizard-ai-criteria`, `03-wizard-configure`,
`04-lock-confirmation`, `05-provider-workspace`, `06-evidence-fail`,
`07-artifact-screenshot`, `08-evidence-needs-review`, `09-payment-blocked-review`,
`10-manual-review-form`, `11-capture-approval-dialog`, `12-payment-captured` (simulated),
`13-ledger-grid`, `14-ledger-detail`, `15-activity-timeline`.
Mobile 390 px: `m01-dashboard-390`, `m02-evidence-390`, `m03-payment-390`,
`m04-ledger-timeline-390`.

## Local demo

```bash
# 1. API (sandbox or rehearsal), worker, fixtures, seed
cd apps/api
uv run uvicorn outcomeguard.main:app --port 8010          # PUBLIC_API_URL=http://localhost:8010
env -u ANTHROPIC_BASE_URL uv run python -m outcomeguard.verification.worker   # no PAYPAL_*/ANTHROPIC_* in its env
python ../../fixtures/serve.py --port 8020 --sink-url http://127.0.0.1:8010/
DEMO_PASSWORD=… uv run python scripts/seed_demo.py         # operator-chosen password

# 2. Web
cd ../web && npm run build
OUTCOMEGUARD_API_URL=http://localhost:8010 npx next start -p 3010

# 3. Tests
npm run test:e2e                                            # mocked, no backend
E2E_BASE_URL=http://localhost:3012 DEMO_PASSWORD=… npm run test:e2e:integration   # rehearsal stack
```

Demo narrative: alice (buyer admin) → maya (provider) → rita (buyer reviewer) → bob (buyer
approver). The demo accounts share one operator-supplied password (TD-12); every demo
decision recorded by a script is labelled as scripted.

## Remaining limitations

See KNOWN_LIMITATIONS.md ("Product UI") and TECH_DEBT.md (TD-13 – TD-15).
