# P00 — OutcomeGuard master build prompt

> The source-of-truth specification for this build, as provided by the project owner on
> 2026-10-15. Progress against it is tracked in [IMPLEMENTATION_PROGRESS.md](IMPLEMENTATION_PROGRESS.md).

You are Claude Code acting as a principal software architect, senior full-stack engineer, AI systems engineer, payment security engineer, QA lead, and hackathon product designer.

Your mission is to build **OutcomeGuard — AI-Verifiable Commerce Agreements**, a production-minded, hackathon-ready application for the PayPal AI Hackathon 2026.

**Tagline:** Don't just pay for a promise. Pay for proof.

**Product concept:** OutcomeGuard transforms natural-language service agreements into machine-verifiable acceptance criteria, validates real delivery evidence using AI and deterministic tools, enforces human-controlled payment decisions, and connects those decisions to real PayPal Sandbox transactions.

The application must be a genuine working product, not a static prototype or a scripted simulation disguised as a working integration.

## 0. Operating principles

1. Inspect the existing repository before making changes. If empty, initialize a clean project.
2. Produce a working, deployable vertical slice before adding secondary features.
3. Do not claim an API call succeeded unless the actual API response proves it.
4. Do not invent PayPal transaction IDs, test results, AI verification results, or webhook events.
5. Never hardcode secrets, credentials, access tokens, or merchant identifiers.
6. Do not create an escrow service or imply that OutcomeGuard holds funds.
7. An LLM may interpret evidence, but must never authorize a financial transaction by itself.
8. A failed test, missing approval, expired authorization, mismatched payment amount, or inconsistent transaction state must block capture.
9. Persist all financial state transitions. In-memory state is insufficient.
10. Prefer a smaller fully working product over a larger partially implemented system.
11. When an external integration is unavailable, provide an explicitly labeled simulation mode, never silently fall back.
12. Keep the main demo understandable in under three minutes.
13. Maintain a running implementation checklist and record verified versus unverified integrations.
14. Do not automatically deploy, spend real money, or create production payments.

## 1. Hackathon objectives

Optimize for five equally weighted criteria: Technical Implementation, Design, Potential Impact, Innovation, Presentation.

Required deliverables: real PayPal Sandbox integration; real AI model integration; public open-source-ready repository; comprehensive README and setup instructions; working hosted demo or reproducible local environment; automated tests; three-minute demonstration script; Devpost submission materials.

The core differentiator is not milestone payments alone. OutcomeGuard's innovation is **evidence-bound payment authorization**: every payment decision is linked to an immutable-in-application agreement version, actual verification evidence, a deterministic policy decision, and explicit human approval. Do not claim cryptographic immutability or tamper-proof auditing unless it is implemented and verified.

## 2. Primary target customer

Small digital agencies and freelancers delivering short-duration digital services. First supported service type: **landing page development**.

Example agreement: *"Build a responsive landing page for $500. The contact form must work, mobile layout must pass verification, and Lighthouse performance score must be at least 90."* The application must convert this into explicit, inspectable acceptance criteria.

## 3. Mandatory user journey

**Stage A — Create agreement.** Buyer creates a service agreement in natural language (title, description, merchant, amount and currency, delivery deadline, acceptance requirements). AI converts it into structured acceptance criteria (e.g. HTTP availability, mobile viewport rendering, contact form functional check, Lighthouse performance threshold). The buyer reviews and explicitly approves the criteria before the agreement is locked. After locking, any material change requires a new agreement version and reapproval.

**Stage B — PayPal authorization.** Orders v2 with `intent: AUTHORIZE`: create order → redirect buyer to PayPal approval → verify the buyer's return state → authorize server-side → persist authorization ID, amount, currency, order ID, merchant context, status and expiration timestamps. Order creation is not authorization; a browser redirect is not proof of authorization. Single Sandbox merchant first. Commit & Pay only if it does not compromise the primary journey.

**Stage C — Submit evidence.** Provider submits delivery URL, optional screenshots, optional GitHub repo/commit, notes. Persist metadata, references, hashes, timestamps. All of it is untrusted input.

**Stage D — Real verification.** Isolated worker running deterministic checks: HTTP status/redirects, Playwright mobile viewport, contact-form interaction against an explicitly configured safe test endpoint, Lighthouse with a documented configuration. AI translates requirements into proposed test specs, summarizes real results, identifies missing evidence, explains discrepancies, recommends next steps — never fabricates results or flips a failure to a pass. Typed structured outputs with schema validation. Never execute arbitrary AI-generated commands. SSRF protections, allowlisting/safe targets, resource limits, timeouts, browser sandboxing; no internal network or cloud metadata access. Unverifiable requirements → `MANUAL_REVIEW_REQUIRED`.

**Stage E — Deterministic payment policy.** Policy engine independent of the LLM. Decisions: `BLOCKED`, `NEEDS_REVIEW`, `ELIGIBLE_FOR_APPROVAL`, `CAPTURE_APPROVED`, `CAPTURED`. Capture requires: locked agreement version; correct merchant; correct authorized amount and currency; valid authorization state; required evidence verified; no unresolved blocking criteria; explicit buyer acceptance; explicit authorized human approval; no prior successful capture; no active capture request; valid payment state transition. Structured reasons for every decision.

**Stage F — Human approval and capture.** Approver reviews agreement, criteria, evidence, test results, AI explanation, policy decision, PayPal authorization details. Backend then performs a real Sandbox capture with PayPal's idempotency mechanism, persists the response, and handles pending, failed, uncertain, duplicate and completed states. Never mark success because a request was sent.

**Stage G — Post-payment resolution.** Narrow refund workflow: buyer submits an issue with evidence; AI summarizes and proposes a resolution; deterministic refund policy validates capture eligibility, merchant authority, remaining refundable balance, no duplicate, merchant approval, recorded issue/resolution. Verified merchant approver authorizes partial/full refund; real Sandbox Refund API; persist refund IDs, status, amounts, responses. Do not claim OutcomeGuard can refund arbitrary third-party merchants' payments, or that AI can force a refund.

## 4. Technical architecture

Monorepo. Frontend: Next.js App Router, TypeScript strict, Tailwind, accessible components, responsive, AG Grid Community for the evidence and payment ledger. Backend: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL, HTTPX for PayPal REST, explicit dependency injection. AI: provider abstraction, at least one real provider, Pydantic-validated structured outputs, deterministic fallback when AI is unavailable, clear separation between AI observations and financial authority. Verification: isolated worker, Playwright, Lighthouse, durable background job queue. Infra: Docker Compose, Render-compatible config, env-based configuration, health/readiness endpoints, structured JSON logging.

Suggested structure: `apps/web`, `apps/api`, `workers/verifier`, `packages/shared-schemas`, `docs/{architecture,security,demo,integration}`, `scripts`, `.github/workflows`, `docker-compose.yml`, `.env.example`, `README.md`, `LICENSE`. Deviations must be documented.

## 5. Domain model

User, Organization, OrganizationMembership, MerchantAccount, Agreement, AgreementVersion, AcceptanceCriterion, EvidenceSubmission, VerificationRun, VerificationResult, PolicyDecision, Approval, PaymentOrder, PaymentAuthorization, PaymentCapture, RefundCase, PaymentRefund, WebhookEvent, AuditEvent, IdempotencyRecord. Explicit enums and state machines; integer minor units or exact decimals (never floats); DB transactions and concurrency controls for financial state; unique constraints on PayPal references and idempotency keys.

## 6. Payment security requirements

Sandbox only by default; server-side OAuth token management; secret redaction; RBAC; secure sessions; buyer/merchant ownership checks; webhook signature verification; replay protection; idempotency; capture/refund concurrency protection; reconciliation; audit events; rate limiting for sensitive operations; clear sandbox-vs-production indicators. Never expose the client secret to the frontend. Never accept amount, merchant identity, authorization ID or capture ID from the browser without server-side verification. A browser approval callback is not a trusted payment event; webhooks and API responses must be validated and reconciled.

## 7. Frontend experience

Professional enterprise fintech SaaS: clean typography, clear hierarchy, accessible contrast, minimal noise, subtle motion, responsive, no fake metrics, no decorative AI chat as primary UX. Screens: Overview, **Agreement Workspace (hero)**, Evidence Verification, Payment Approval, Resolution Center, Promise Ledger. The workspace visually connects Agreement → Criteria → Evidence → Verification → Human Approval → PayPal Settlement with actual status. AG Grid Community ledger with sorting, filtering, column management, record links; no premium-only features without a license.

## 8. Demo scenario

Fixtures `landing-page-broken` (contact form fails) and `landing-page-fixed` (works) — real pages the worker actually tests. Canonical demo: $500 agreement → AI criteria → buyer approves → Sandbox authorization → broken page submitted → real verification fails → policy blocks capture → fixed page submitted → verification passes → buyer approves → real Sandbox capture → optional $50 partial refund → Promise Ledger. Refund optional for the first slice, required for feature-complete if feasible. Rehearsal mode without live credentials, every simulated result labeled.

## 9. Testing requirements

Agreement version locking; AI output schema validation; verification pass/fail; policy authorization; human approval enforcement; unauthorized capture; duplicate capture; concurrent capture; expired authorization; merchant mismatch; currency mismatch; payment failure and recovery; partial refund; refund overpayment; duplicate refund; webhook signature verification; webhook replay and out-of-order events; RBAC; audit integrity; SSRF defenses. Mocked PayPal for unit tests; separate opt-in real Sandbox suite; never label mocked tests as live proof.

## 10. Required documentation

README.md, docs/ARCHITECTURE.md, docs/PRODUCT_SPEC.md, docs/SECURITY.md, docs/PAYPAL_INTEGRATION.md, docs/AI_VERIFICATION.md, docs/DEMO_SCRIPT_3MIN.md, docs/DEVPOST_SUBMISSION.md, docs/INTEGRATION_PROOF.md (Implemented / Unit-tested with mocks / Tested against PayPal Sandbox / Tested against real AI provider / Not yet verified), docs/KNOWN_LIMITATIONS.md, docs/IMPLEMENTATION_PROGRESS.md. No credentials or sensitive transaction data committed.

## 11. Development phases

P01 Repository foundation · **P02 PayPal Sandbox proof** (OAuth → Create Order → Buyer Approval → Authorize → Capture → Partial Refund, before AI) · P03 Agreement domain · P04 Verification engine · P05 AI contract and evidence intelligence · P06 Deterministic payment governance · P07 Product UI · P08 Resolution and refunds · P09 Security and integration hardening · P10 Hackathon delivery. Do not advance while a critical blocker in the previous phase remains.

## 12. Execution instructions

Inspect repo and tooling; check current PayPal docs; create architecture, threat model and plan; initialize; build the smallest working Sandbox slice; run tests and report actual results. Stop at checkpoints needing credentials or human browser approval with precise instructions. Never ask for PayPal secrets in chat. Never claim completion after scaffolding. Per phase report: files changed, functionality, tests and results, real integrations verified, blockers, next phase.

## 13. Definition of done

Buyer creates and approves a real agreement; Sandbox order approved and authorized; real verification fails then passes; failed verification blocks capture; human approval mandatory; real Sandbox capture succeeds; real partial refund by an authorized merchant; real transaction references persisted and displayed; duplicate financial operations prevented or reconciled; UI reflects backend state; automated tests pass; reproducible deployment; accurate README; reliable three-minute demo.

> **AI interprets the promise. Tools verify the evidence. Policy determines eligibility.
> Humans authorize the money. PayPal executes the transaction.**
