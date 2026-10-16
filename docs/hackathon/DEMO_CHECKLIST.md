# Demo checklist

## Day before

- [ ] `git pull` on the demo machine; `uv sync` in `apps/api`; `npm ci && npm run build` in `apps/web`
- [ ] `.env` present (git-ignored) with `DATABASE_URL`, `ANTHROPIC_API_KEY`, `SINK_SECRET`,
      `DEMO_PASSWORD`, `VERIFIER_DEV_ALLOW=127.0.0.1:8020,127.0.0.1:8010`, `SINK_BASE_URL=http://127.0.0.1:8010`
- [ ] PostgreSQL running; `uv run alembic upgrade head`
- [ ] Playwright Chromium + Lighthouse installed (`apps/api/verifier-tools`, `uv run playwright install chromium`)
- [ ] Anthropic Console spend limit set (local budget is an estimate, TD-9)
- [ ] Two full **human** rehearsals following GOLDEN_DEMO.md, timed with a stopwatch; record
      them in the table at the end of GOLDEN_DEMO.md (the automated paced rehearsals —
      2:24 and 2:40 — are estimates only)

## 30 minutes before

```bash
scripts/demo/stop.sh; scripts/demo/start.sh          # expect: payment mode rehearsal, web 200, fixtures 200
DEMO_PASSWORD=… uv run --directory apps/api python scripts/prepare_demo.py --ready
# expect: broken → fail, fixed → needs_manual_review, scripted review, SIMULATED order authorized
```

- [ ] Banner shows **REHEARSAL MODE · new payments are SIMULATED** (orange)
- [ ] Five windows parked as in GOLDEN_DEMO.md: L landing · A alice `/agreements/new` ·
      A2 alice prepared agreement on the **broken** run · M maya `/provider` · B bob prepared
      agreement Payment tab showing **Eligible for buyer approval**
- [ ] Do **not** restart the API after `--ready` (the SIMULATED authorization is in memory)
- [ ] Nothing else needs a login on stage (rita's review is pre-recorded, labelled SCRIPTED)
- [ ] Zoom 110–125 % for projection; notifications off; unrelated tabs closed; laptop on power
- [ ] After any rehearsal: run `prepare_demo.py --ready` again (each run consumes the
      prepared capture) and re-park window B on the new agreement

## Accounts (demo only)

| Account | Role | Used for |
|---|---|---|
| alice@acme.demo | buyer admin | live: AI wizard + lock; prepared agreement (by `--ready`): checkout + authorize (SIMULATED) |
| bob@acme.demo | buyer approver | capture approval, capture |
| rita@acme.demo | buyer reviewer | pre-recorded SCRIPTED accessibility decision (`prepare_demo.py`) — not used on stage |
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

1. Run GOLDEN_DEMO.md end to end with a stopwatch, speaking the narration aloud
   (estimate 2:24–2:40; target ≤ 3:00). Do it twice and fill in the table.
2. If a run exceeds 3:00, apply the cuts listed in GOLDEN_DEMO.md, in order.
3. Practise one fallback (Claude slow → window A2).
4. Re-prepare (`--ready`) and re-park window B before the real demo.
