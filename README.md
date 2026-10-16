# OutcomeGuard

**AI-verifiable commerce agreements on PayPal.**
*Don't just pay for a promise. Pay for proof.*

OutcomeGuard turns a natural-language service agreement ("build a responsive landing
page for $500; the contact form must work; Lighthouse performance ≥ 90") into
machine-verifiable acceptance criteria, checks the delivered work with real tools, and
only lets a human release the buyer's PayPal-authorized payment when the evidence
supports it.

```
AI interprets the promise → Tools verify the evidence → Policy determines eligibility
→ Humans authorize the money → PayPal executes the transaction
```

Built for the [PayPal AI Hackathon](https://paypalaihackathon.devpost.com/) (2026).
PayPal **Sandbox only** — no real money moves.

> **Status: hackathon demo ready (P09 of P10).** Implemented and tested: the PayPal payment
> core (authorize → capture → partial refund **run against the live PayPal Sandbox**;
> live webhook delivery not verified yet), the agreement domain (versioned agreements,
> typed acceptance criteria, buyer approval and locking, RBAC), and real evidence
> verification (Chromium, Lighthouse, controlled contact-form sink, SSRF-guarded egress,
> container network isolation), and the AI layer (Claude Sonnet 5.5 drafts acceptance
> criteria from plain language and explains real verification evidence — grounded, budget-
> capped, no financial authority). A fixed page with an unresolved blocking manual review
> is reported as NEEDS_MANUAL_REVIEW, never as verified. Payment governance (P06): a
> deterministic policy, evidence-bound human reviews and a separate buyer capture approval,
> re-checked at the moment of capture. Product UI (P07): role-aware buyer/provider
> workspaces, an AI agreement wizard, an evidence-first verification report, manual-review
> and payment workspaces and an AG Grid ledger, served through a same-origin BFF with
> HttpOnly session cookies. Resolution (P08): evidence-bound cases between buyer and
> provider; refunds only after both approve the same proposal and a fresh policy check;
> governed authorization voids. See
> [docs/IMPLEMENTATION_PROGRESS.md](docs/IMPLEMENTATION_PROGRESS.md) and
> [docs/INTEGRATION_PROOF.md](docs/INTEGRATION_PROOF.md) for what is verified.

## How the money works

OutcomeGuard is **not an escrow and never holds funds**. When the buyer approves, PayPal
places an **authorization** (a hold) on the buyer's PayPal account payable to the
merchant. OutcomeGuard decides whether the merchant's server may ask PayPal to
**capture** it — only after verification passes and a human approves. Every capture and
refund is idempotent, reconciled against PayPal and recorded in a hash-chained audit log.
Details: [docs/PAYPAL_INTEGRATION.md](docs/PAYPAL_INTEGRATION.md).

## Repository

```
apps/api     FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · PostgreSQL · HTTPX
apps/web     Next.js 16 (App Router) · TypeScript strict · Tailwind CSS
docs/        architecture, security, PayPal integration, proof, progress
```

## Run locally

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 22, PostgreSQL 15+.

```bash
cp .env.example .env            # fill in Sandbox credentials, or set PAYMENT_MODE=rehearsal
createdb outcomeguard
cd apps/api && uv sync && uv run alembic upgrade head
uv run uvicorn outcomeguard.main:app --reload --port 8000
```

```bash
cd apps/web && npm install
OUTCOMEGUARD_API_URL=http://localhost:8000 npm run dev   # http://localhost:3000
```

The web app talks to the API only from the server (BFF); the browser never sees an API
token. Demo users: `DEMO_PASSWORD=… uv run python scripts/seed_demo.py` (operator-chosen
password). Full local demo and test instructions:
[docs/P07_UI_VERIFICATION.md](docs/P07_UI_VERIFICATION.md).

Or with Docker: `docker compose up --build`.

### Demo in one command

```bash
scripts/demo/start.sh                     # rehearsal mode (simulated payments), web on :3010
DEMO_PASSWORD=… uv run --directory apps/api python scripts/prepare_demo.py
scripts/demo/stop.sh
```

Walkthrough: [docs/hackathon/GOLDEN_DEMO.md](docs/hackathon/GOLDEN_DEMO.md) ·
checklist: [docs/hackathon/DEMO_CHECKLIST.md](docs/hackathon/DEMO_CHECKLIST.md).

### No PayPal credentials? Rehearsal mode

Set `PAYMENT_MODE=rehearsal`. No PayPal calls are made; every id starts with `SIM-`,
every record is stored as `simulated`, and the approval step is a clearly labeled
"SIMULATED" page. It is never used as a silent fallback.

## Tests

```bash
cd apps/api
createdb outcomeguard_test      # once
uv run pytest                   # unit tests, mocked PayPal
uv run pytest -m sandbox        # opt-in, real PayPal Sandbox (needs credentials)

cd apps/web
npm run lint && npm run typecheck && npm run build
npm run test:e2e                # browser tests, MOCKED API (runs in CI)
E2E_BASE_URL=… DEMO_PASSWORD=… npm run test:e2e:integration   # real local stack
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Security & threat model](docs/SECURITY.md)
- [PayPal integration](docs/PAYPAL_INTEGRATION.md)
- [Integration proof](docs/INTEGRATION_PROOF.md)
- [Evidence verification (P04)](docs/VERIFICATION.md)
- [AI contract & evidence intelligence (P05)](docs/AI_VERIFICATION.md)
- [Payment governance (P06)](docs/GOVERNANCE.md)
- [Product UI verification (P07)](docs/P07_UI_VERIFICATION.md)
- [Resolution, void and refund governance (P08)](docs/RESOLUTION_AND_REFUNDS.md)
- [Hackathon demo readiness (P09)](docs/hackathon/DEMO_READINESS_REPORT.md)
- [Technical debt register](docs/TECH_DEBT.md)
- [Webhooks: go-live procedure](docs/WEBHOOKS.md)
- [P04 verification worker plan](docs/architecture/P04_VERIFICATION_PLAN.md)
- [Known limitations](docs/KNOWN_LIMITATIONS.md)
- [Build specification](docs/BUILD_PROMPT_P00.md)

## License

[MIT](LICENSE)
