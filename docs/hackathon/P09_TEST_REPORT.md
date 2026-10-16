# P09 test report

Date: 2026-10-16. No real PayPal operation was triggered by any P09 test or rehearsal
(demo stack in **rehearsal** mode; every payment `SIM-…`, labelled SIMULATED).

| Gate | Result |
|---|---|
| Backend `pytest` (incl. 4 real-browser verification tests) | **420 passed** (419 + new `test_delivery_url_may_not_point_at_outcomeguard_itself`) |
| `-m sandbox` / `-m ai_live` (read-only + real model) | 2 / 2 passed (P08 baseline, unchanged code paths) |
| Ruff (src, tests, scripts) · mypy · `alembic check` | clean · clean · no changes |
| Web lint · `tsc --noEmit` · production build | clean · clean · success |
| Mocked browser tests (desktop + mobile, CI) | **47 passed** — incl. axe WCAG 2 A/AA scans, 1440/1280/768/390/375 px layouts, new rehearsal/sandbox banner tests |
| Real-API browser flow, P07 golden path (rehearsal) | **8 / 8** in ~60 s — real Claude draft, real Chromium + Lighthouse (broken FAIL, fixed NEEDS_MANUAL_REVIEW), simulated payment, scripted review, capture, ledger, 390 px; no console errors |
| Real-API browser flow, P08 resolution (rehearsal) | **7 / 7** — case, two-sided $100 refund, void; no console errors |
| Demo scripts | `scripts/demo/start.sh` ~1 s to ready; `prepare_demo.py` ~39 s (broken FAIL 24 s, fixed NEEDS_MANUAL_REVIEW 14 s, scripted review) |
| Secret scan of the P09 diff | no `.env` values (demo password, operator token, PayPal secret, Anthropic key, session/sink secrets) |
| npm audit | unchanged: 5 high, all in `eslint-config-next` dev dependencies (TD-15) |

Defects found and fixed during P09: rehearsal banner claimed "PayPal Sandbox" (misleading
financial state); verifier could be aimed at the API itself in the demo allow-list
(`DELIVERY_TARGETS_PLATFORM`); demo start script passed a wrong sink URL (would have made
the fixed page FAIL on stage); start script pipe hang; stop script missed child processes.

Screenshots: `docs/hackathon/screenshots/` (golden path, landing 1440/390, resolution).
