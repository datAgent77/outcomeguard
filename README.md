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

> **Status: early build (P02 of P10).** The PayPal payment core (authorize → capture →
> partial refund, idempotency, reconciliation, webhooks, audit log) is implemented and
> unit-tested with mocked PayPal. Live Sandbox verification, agreements, AI and the
> verification worker are in progress. See
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
cd apps/web && npm install && npm run dev   # http://localhost:3000
```

Or with Docker: `docker compose up --build`.

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
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Security & threat model](docs/SECURITY.md)
- [PayPal integration](docs/PAYPAL_INTEGRATION.md)
- [Integration proof](docs/INTEGRATION_PROOF.md)
- [Known limitations](docs/KNOWN_LIMITATIONS.md)
- [Build specification](docs/BUILD_PROMPT_P00.md)

## License

[MIT](LICENSE)
