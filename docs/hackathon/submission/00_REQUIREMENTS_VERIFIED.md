# Official requirements — verified

Source: the official pages, read on **2026-10-16** in a browser:
<https://paypalaihackathon.devpost.com/> and <https://paypalaihackathon.devpost.com/rules>
(automated fetching returns HTTP 403; the pages render normally in a browser).
Quotes are short; the rules page is authoritative and may change — re-check before submitting.

## Dates

- Submission period ends **Thursday, Nov 12, 2026, 12:00 pm Pacific Time** (= 20:00 UTC =
  **23:00 GMT+3**; US daylight saving ends Nov 1, so PT is UTC−8).
- Judging Dec 1–15, 2026; winners around Dec 21, 2026.
- Drafts can be saved on Devpost before the deadline; no changes after it.

## Project requirements (and how OutcomeGuard meets them)

| Requirement (rules) | OutcomeGuard | Status |
|---|---|---|
| Integrate the PayPal developer platform using the free **sandbox** | Orders v2 (AUTHORIZE), Payments v2 (capture, refund, void), webhooks; real Sandbox runs documented | ✅ |
| Integrate an AI tool/model; "PayPal integration is central" | Claude (`claude-sonnet-5-5`) drafts criteria and explains evidence; PayPal capture is the product's core | ✅ |
| Newly created or significantly updated during the period (Oct 1 – Nov 12) | Repository created Oct 15, 2026; all work in the period (git history) | ✅ |
| Functional: installs and runs consistently, works as shown in the video | Fresh-clone run verified 2026-10-16 (see below) | ✅ (re-check on final commit) |
| Third-party SDK/API use authorised | PayPal Sandbox, Anthropic API, AG Grid Community (MIT), Lighthouse (Apache-2.0), Playwright (Apache-2.0) | ✅ |

## Submission requirements

| Requirement | Status |
|---|---|
| Text description of features and functionality | Draft: `02_DEVPOST_DRAFT.md` |
| Functional demo: **setup instructions in the repo** *or* a hosted URL ("does not need to be publicly hosted") | README "Run it locally" — verified on a fresh clone without PayPal or Anthropic keys (rehearsal mode) |
| Tools: "identify which tools (model or AI service) were used and explain how" | In the Devpost draft |
| Public repository on GitHub with an **open-source license visible in the About section** | Public; GitHub API detects **MIT**. About description/topics are empty — **needs your action** |
| All source, assets and instructions in the repo | ✅ (secrets only in git-ignored `.env`) |
| Demo video **under 3 minutes**, shows the project working on its device, **public on YouTube**, link in the form | Script/storyboard: `03_VIDEO_PACKAGE.md` — **recording and upload are yours** |
| No third-party trademarks or copyrighted music/material without permission | Video plan avoids PayPal's checkout page and logos and uses no music; **confirm** whether showing the PayPal name/logo is acceptable to you |
| Testing access: provide credentials/keys/sandbox details needed, or instructions to obtain them | README: rehearsal mode needs none; Sandbox needs the judge's own free PayPal developer app; AI needs an Anthropic key. **Decide** whether to also provide a hosted demo or keys |
| English | ✅ |

## Judging (rules, section 6)

Stage one pass/fail: fits the theme and applies the required APIs. Stage two, **equally
weighted**: **Technological Implementation · Design · Potential Impact · Innovation/Idea ·
Presentation**. Ties broken in that order.

## Prizes relevant to this entry

Grand prizes; honorable mentions incl. **Best Use of PayPal + AI**, **Best Demo Delivery**,
Most Impactful, Most Creative; sponsor prize **Best Use of AG Grid** ("All Eligible
Submissions that use any AG Grid tools") — OutcomeGuard's ledger uses AG Grid Community. A
project can win at most one grand/honorable prize plus one sponsor prize. Render prizes
would require a Render deployment (not done; deploying needs your approval).

## Needs manual confirmation

1. The **exact fields of the "Enter a Submission" form** (only visible to a logged-in,
   registered entrant) — e.g. whether "Tools" and "how it was significantly updated" are
   separate fields, and any sponsor-prize opt-ins.
2. Whether you (the entrant) are registered and eligible (age of majority; country not
   excluded — the rules list e.g. Brazil, Quebec, Russia, Crimea, Cuba, Iran, North Korea).
3. Whether to disclose that the code was written with an AI coding assistant (not required
   by the rules as read; entrant must own the work and its ideas).
4. Use of the PayPal name/logo in the video (sponsor's trademark) — the plan avoids logos.
5. Hosted demo or not (optional per rules; Render sponsor prize only with a deployment).
