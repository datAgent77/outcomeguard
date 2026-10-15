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
| TD-6 | Real-model evaluation set for agreement drafting / evidence explanations | Live tests cover two scenarios; no broader eval of drafting quality or injection resistance | 2026-10-15 (P05) | P09 | **open** |
| TD-7 | Interim authentication (operator-issued demo tokens) | No real login yet | 2026-10-15 (P03) | P06 | **open** |
| TD-8 | Rate limiting on sensitive and AI endpoints | AI endpoints are budget-capped per day but not rate-limited per user | 2026-10-15 (P05) | P09 | **open** |
