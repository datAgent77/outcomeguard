# Implementation progress

Deadline: **2026-11-12 23:00 GMT+3** (PayPal AI Hackathon). Spec: [BUILD_PROMPT_P00.md](BUILD_PROMPT_P00.md).

| Phase | Scope | Status |
|---|---|---|
| P01 | Repository foundation | ✅ done (Docker images not built locally — daemon was off) |
| P02 | PayPal Sandbox proof | 🟡 code + mocked tests done; **live gate waiting for Sandbox credentials + buyer approval** |
| P03 | Agreement domain | ⏳ |
| P04 | Verification engine (Playwright, Lighthouse, SSRF guard) | ⏳ |
| P05 | AI contract and evidence intelligence | ⏳ |
| P06 | Deterministic policy, human approval, auth/RBAC | ⏳ |
| P07 | Product UI + AG Grid ledger | ⏳ |
| P08 | Resolution and refunds | ⏳ (payment-level refund done in P02) |
| P09 | Security and integration hardening | ⏳ |
| P10 | Hackathon delivery | ⏳ |

## P01 — Repository foundation (2026-10-15)

- [x] Monorepo: `apps/api` (FastAPI, uv, Python 3.12), `apps/web` (Next.js 16, TS strict, Tailwind 4)
- [x] Config from env, Sandbox-only guard, `postgres://` → `postgresql+psycopg://` normalisation
- [x] JSON logging with secret redaction
- [x] SQLAlchemy 2 models + first Alembic migration (12 tables)
- [x] `/health`, `/ready` (DB ping + non-secret config), `/config/public`
- [x] Dockerfiles, `docker-compose.yml`, `render.yaml` (not deployed), GitHub Actions CI
- [x] `.env.example`, `.gitignore`, docs skeleton
- [ ] `docker compose up` verified (Docker daemon not running on dev machine)
- [ ] CI run on GitHub (not pushed yet)

## P02 — PayPal Sandbox proof (2026-10-15)

- [x] HTTPX gateway: OAuth caching, PayPal-Request-Id, definite vs uncertain errors
- [x] Orders v2 create/get/authorize; Payments v2 get/capture/void/refund; webhook verify
- [x] Reserve → call → settle; UNKNOWN + reconcile; payee pinning; amount checks
- [x] Webhook intake: verify, replay-safe store, re-read + reconcile, no regression
- [x] Hash-chained audit log
- [x] Explicit rehearsal mode
- [x] Operator-gated `/dev/payments` driver + `scripts/paypal_sandbox_proof.py`
- [x] 50 unit tests (mocked PayPal), including concurrency
- [x] HTTP smoke test in rehearsal mode (order → simulated approval → authorize → capture → duplicate blocked → $50 refund → audit chain intact)
- [ ] **Live Sandbox run** — needs `PAYPAL_CLIENT_ID`/`SECRET` in `.env` and a buyer approval
- [ ] Live webhook delivery — needs a public HTTPS URL (Render deploy) and `PAYPAL_WEBHOOK_ID`

## Verified vs unverified

See [INTEGRATION_PROOF.md](INTEGRATION_PROOF.md).
