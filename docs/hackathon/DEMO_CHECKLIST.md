# Demo checklist

## Day before

- [ ] `git pull` on the demo machine; `uv sync` in `apps/api`; `npm ci && npm run build` in `apps/web`
- [ ] `.env` present (git-ignored) with `DATABASE_URL`, `ANTHROPIC_API_KEY`, `SINK_SECRET`,
      `DEMO_PASSWORD`, `VERIFIER_DEV_ALLOW=127.0.0.1:8020,127.0.0.1:8010`, `SINK_BASE_URL=http://127.0.0.1:8010`
- [ ] PostgreSQL running; `uv run alembic upgrade head`
- [ ] Playwright Chromium + Lighthouse installed (`apps/api/verifier-tools`, `uv run playwright install chromium`)
- [ ] Anthropic Console spend limit set (local budget is an estimate, TD-9)
- [ ] Two full rehearsals following GOLDEN_DEMO.md, timed

## 30 minutes before

```bash
scripts/demo/stop.sh; scripts/demo/start.sh          # expect: payment mode rehearsal, web 200, fixtures 200
DEMO_PASSWORD=… uv run --directory apps/api python scripts/prepare_demo.py   # expect: fail → needs_manual_review
```

- [ ] Banner shows **REHEARSAL MODE · new payments are SIMULATED**
- [ ] Backup agreement URL from the script opens on the Evidence tab
- [ ] One test AI draft succeeds (≈12 s)
- [ ] Browser profiles signed in: alice, maya, rita, bob; zoom 110–125 % for projection
- [ ] Notifications off; close unrelated tabs; laptop on power

## Accounts (demo only)

| Account | Role | Used for |
|---|---|---|
| alice@acme.demo | buyer admin | wizard, lock, checkout, authorize |
| bob@acme.demo | buyer approver | capture approval, capture |
| rita@acme.demo | buyer reviewer | scripted accessibility decision |
| maya@northwind.demo | provider admin | submit delivery |
| eve@globex.demo | other buyer | (isolation check: sees nothing) |

Password: the operator-chosen `DEMO_PASSWORD` from `.env` — never shown on screen or committed.

## Screenshots available as fallback

`docs/hackathon/screenshots/` (rehearsal, simulated payments), `docs/p08/sandbox/` (real
Sandbox refund), `docs/p07/screenshots/`.

## PayPal Sandbox precautions

- The demo stack runs in **rehearsal** mode: no PayPal call is made, every new payment is
  labelled SIMULATED.
- Real Sandbox runs are already recorded (INTEGRATION_PROOF.md). Do **not** start a new Sandbox
  order on stage unless explicitly decided; if you do: `PAYMENT_MODE=sandbox scripts/demo/start.sh`,
  approve with a **Sandbox buyer account** (never a real PayPal login), and expect ~30 s for
  the PayPal approval.
- Never present a SIMULATED capture as a PayPal transaction.

## Final rehearsal steps

1. Run GOLDEN_DEMO.md end to end with a timer (target ≤ 3:00).
2. Practise one fallback (switch to the backup agreement mid-demo).
3. `scripts/demo/stop.sh` after rehearsal; restart fresh before the real demo.
