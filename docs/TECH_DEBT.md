# Technical debt register

Open items are tracked here until they are resolved **and verified**. An item is closed
only with a link to the commit and the evidence that proves it.

| # | Item | Why it matters | Opened | Target | Status |
|---|---|---|---|---|---|
| TD-1 | Dedicated least-privilege PostgreSQL role for the verification worker | Worker currently uses the API's DB user; a compromised worker could touch payment tables | 2026-10-15 (P04) | P09 | **open** |
| TD-2 | Job heartbeat and lease renewal | A run longer than the lease (10 min) can be re-claimed; first worker then fails with LEASE_LOST | 2026-10-15 (P04) | P09 | **open** |
| TD-3 | Container sandbox limitations | Chromium's own sandbox is off in containers and CI (no user namespaces); isolation relies on the container + internal network | 2026-10-15 (P04) | P09 (evaluate seccomp profile / userns-enabled runtime) | **open** |
| TD-4 | Full Docker Compose end-to-end verification | Only the isolation topology has been run in containers, not API + worker + runner + DB together | 2026-10-15 (P04) | P09/P10 | **open** |
| TD-5 | Live PayPal webhook verification | Signature verification and delivery only tested with mocks; needs a public HTTPS URL + `PAYPAL_WEBHOOK_ID` | 2026-10-15 (P02) | P10 (with hosted deploy, needs approval) | **open** |
| TD-6 | Comprehensive real-model evaluation dataset | Live tests + demos cover the canonical scenario and one injection; no broad eval of drafting quality, grounding or injection resistance across models | 2026-10-15 (P05) | P09 | **open** |
| TD-7 | Real authentication and authorization | P06 replaced the interim tokens with scrypt passwords + server-side revocable sessions + lockout and enforces org/role checks on review/capture. P07 added the web BFF: HttpOnly SameSite=Lax session cookie, Origin + custom-header CSRF check, path allow-list. Still missing: MFA, password reset flow, audit of logins, session refresh, external IdP | 2026-10-15 (P03) | P09 | **partially addressed** (P06 + P07, see GOVERNANCE.md, P07_UI_VERIFICATION.md) — not resolved |
| TD-8 | Rate limiting | AI endpoints are budget-capped per day but not rate-limited per user; no rate limits on payment or sink endpoints | 2026-10-15 (P05) | P09 | **open** |
| TD-9 | Local AI budget is an estimate | Reservations use list prices and reported usage, not Anthropic billing; covers only calls through this gateway | 2026-10-15 (P05) | P10 (configure Console spend limit before any shared deployment) | **open** |
| TD-11 | Authorization reauthorization not implemented | Authorizations near expiry are blocked (`AUTH_EXPIRED`, 1 h margin) but cannot be renewed; a new order is needed | 2026-10-15 (P06) | P08/P09 | **open** |
| TD-12 | Demo users and scripted actors | Demo identities share one operator-supplied password; demo scripts act as the human actors | 2026-10-15 (P06) | P10 | **open** |
| TD-13 | Real PayPal Sandbox flow through the UI not yet run | The browser flow is automated end-to-end with SIMULATED payments; the Sandbox path (checkout → human PayPal approval → authorize → approve → capture) through the UI still needs a person | 2026-10-16 (P07) | P07 acceptance | **open** |
| TD-14 | Ledger query does not scale | `/workspace/ledger` builds an `IN` list of all the organization's entity ids per request; needs an indexed agreement/org column on audit events or a projection table | 2026-10-16 (P07) | P09 | **open** |
| TD-15 | Dev-only npm audit findings | `eslint-config-next` pulls `@next/eslint-plugin-next → fast-glob → micromatch → braces` (5 high, ReDoS/stack exhaustion); dev/lint-time only, not shipped to the browser; fix needs a Next ESLint plugin update | 2026-10-16 (P07) | P09 | **open** |
| TD-10 | No browser console capture in verification | AI cannot see JavaScript errors, so root causes like the broken fixture's TypeError are reported as "not visible in the evidence" | 2026-10-15 (P05) | P09 | **open** |

**Before any publicly accessible deployment** TD-1 (worker DB permissions), TD-3/TD-4
(network and container isolation in the real topology), TD-7 (authentication) and TD-8
(rate limiting) must be resolved and verified.
