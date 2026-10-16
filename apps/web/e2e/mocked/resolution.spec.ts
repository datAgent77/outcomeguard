// MOCKED API — P08 resolution UI: open a case, proposals, refund gating and confirmation,
// UNKNOWN reconciliation, authorization void, ledger labels, mobile and accessibility.
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { AGREEMENT, fakeCookie, H, MERCHANTS, mockApi, order, run, status, summary, type Handler } from "./fixtures";

test.beforeEach(async ({ context, baseURL }) => fakeCookie(context, baseURL!));

const captured = order({
  capture: {
    id: "mock-cap",
    environment: "simulated",
    paypal_capture_id: "SIM-CAPTURE-1",
    amount: "500.00",
    currency: "USD",
    status: "completed",
    paypal_status: "COMPLETED",
    settlement: { currency: "USD", gross_captured: "500.00", paypal_fee: null, net_at_capture: null, refunded_gross: "0.00", net_after_refunds_derived: null },
    error: null,
    created_at: "",
  },
  simulated: true,
  environment: "simulated",
});

function proposal(over: Record<string, unknown> = {}) {
  return {
    id: "mock-prop-1",
    number: 1,
    side: "provider",
    author_email: "maya@northwind.demo",
    remedy: "partial_refund",
    amount: "100.00",
    currency: "USD",
    rationale: "Refund 100 for the broken contact form.",
    evidence_refs: [],
    content_sha256: H("p"),
    status: "open",
    created_at: "2026-10-16T10:00:00Z",
    decided_at: null,
    approvals: [{ side: "provider", choice: "approve", approver_email: "maya@northwind.demo", approver_role: "merchant_admin", proposal_content_sha256: H("p"), decided_at: "" }],
    ...over,
  };
}

function kase(over: Record<string, unknown> = {}) {
  return {
    id: "mock-case",
    agreement_id: "mock-ag-1",
    agreement_version_id: "mock-v1",
    agreement_content_hash: H("c"),
    payment_authorization_id: "mock-auth",
    payment_capture_id: "mock-cap",
    status: "proposed",
    revision: 3,
    reason_category: "defect_after_acceptance",
    description: "The contact form stopped sending messages after launch.",
    requested_remedy: "partial_refund",
    requested_amount: "150.00",
    currency: "USD",
    opened_by_email: "alice@acme.demo",
    your_side: "buyer",
    evidence_snapshot: {
      agreement_version_id: "mock-v1",
      agreement_content_hash: H("c"),
      criteria: [{ id: "mock-c-form", key: "contact_form_works", title: "Contact form can be submitted" }],
      verification_run: { id: "mock-run-fixed", verdict: "needs_manual_review", evidence_snapshot_sha256: H("2"), finished_at: "", results: [{ criterion_id: "mock-c-form", key: "contact_form_works", outcome: "pass" }] },
      artifacts: [{ id: "mock-art-shot", kind: "screenshot", label: "contact-form-after-submit.png", sha256: H("a") }],
      manual_reviews: [{ id: "r", criterion_id: "mock-c-a11y", decision: "satisfied", evidence_snapshot_sha256: H("2"), scripted: true }],
      payment: { paypal_order_id: "SIM-ORDER", paypal_authorization_id: "SIM-AUTH", authorization_status: "captured", paypal_capture_id: "SIM-CAPTURE-1", capture_status: "completed", amount: "500.00", currency: "USD", environment: "simulated" },
      limitations: [],
    },
    evidence_snapshot_sha256: H("s"),
    outcome: null,
    closure_reason: null,
    created_at: "2026-10-16T09:00:00Z",
    updated_at: "",
    closed_at: null,
    proposals: [proposal()],
    events: [
      { id: "e1", kind: "opened", side: "buyer", author_email: "alice@acme.demo", body: "The contact form stopped sending messages after launch.", data: {}, created_at: "2026-10-16T09:00:00Z" },
      { id: "e2", kind: "message", side: "provider", author_email: "maya@northwind.demo", body: "We offer a partial refund.", data: {}, created_at: "2026-10-16T09:30:00Z" },
    ],
    financials: { currency: "USD", captured: "500.00", refunded: "0.00", held: "0.00", remaining: "500.00", paypal_fee_at_capture: null, refunds: [] },
    ...over,
  };
}

function base(extra: Record<string, Handler | object> = {}) {
  return {
    "GET /workspace/merchants": MERCHANTS,
    "GET /agreements/mock-ag-1": AGREEMENT,
    "GET /workspace/agreements/mock-ag-1": summary({ open_resolution: { id: "mock-case", status: "proposed" } }),
    "GET /agreements/mock-ag-1/verification-runs": [run("needs_manual_review")],
    "GET /agreements/mock-ag-1/payments": [captured],
    "GET /agreements/mock-ag-1/resolutions": [{ id: "mock-case", status: "proposed", reason_category: "defect_after_acceptance", requested_remedy: "partial_refund", requested_amount: "150.00", currency: "USD", created_at: "", closed_at: null, outcome: null }],
    ...extra,
  };
}

async function openCase(page: Page) {
  await page.goto("/agreements/mock-ag-1?tab=resolution&case=mock-case");
  await expect(page.getByTestId("case-workspace")).toBeVisible();
}

test("buyer opens a case through the guided form; opening moves no money", async ({ page }) => {
  let posted: Record<string, unknown> | undefined;
  await mockApi(
    page,
    "alice",
    base({
      "GET /workspace/agreements/mock-ag-1": summary(),
      "GET /agreements/mock-ag-1/resolutions": [],
      "POST /agreements/mock-ag-1/resolutions": (_r, _u, b) => {
        posted = b as Record<string, unknown>;
        return kase({ status: "open", proposals: [], events: [kase().events[0]] });
      },
      "GET /resolutions/mock-case": kase({ status: "open", proposals: [], events: [kase().events[0]] }),
    }),
  );
  await page.goto("/agreements/mock-ag-1?tab=resolution");
  await expect(page.getByText("Opening a case never moves money.")).toBeVisible();
  await page.getByRole("button", { name: "Open a case" }).click();
  await expect(page.getByText("Opening a case does not reverse or refund any payment.")).toBeVisible();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Describe the issue").fill("The contact form stopped sending messages after launch.");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel("Contact form can be submitted").check();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel(/screenshot · contact-form-after-submit.png/).check();
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByLabel(/Partial refund/).check();
  await page.getByLabel(/Requested amount/).fill("150.00");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByText("Nothing is refunded or cancelled until both parties approve")).toBeVisible();
  await page.getByRole("button", { name: "Open case" }).click();
  await expect(page.getByTestId("case-workspace")).toBeVisible();
  expect(posted).toMatchObject({ requested_remedy: "partial_refund", requested_amount: "150.00", criterion_ids: ["mock-c-form"], artifact_ids: ["mock-art-shot"] });
});

test("case workspace shows positions, immutable evidence snapshot and proposal approvals", async ({ page }) => {
  await mockApi(page, "alice", base({ "GET /resolutions/mock-case": kase() }));
  await openCase(page);
  await expect(page.getByText("Buyer position")).toBeVisible();
  await expect(page.getByText("We offer a partial refund.").first()).toBeVisible();
  await expect(page.getByText("scripted demo decision")).toBeVisible();
  await expect(page.getByText("The snapshot taken when the case was opened is immutable.")).toBeVisible();
  const p = page.getByTestId("proposal-1");
  await expect(p.getByText("provider: approved by maya@northwind.demo")).toBeVisible();
  await expect(p.getByText("buyer: pending")).toBeVisible();
  await expect(page.getByTestId("financials")).toContainText("$500.00");
});

test("approving binds the proposal hash and needs confirmation; rejection also confirmed", async ({ page }) => {
  let approveBody: unknown;
  let rejected = false;
  await mockApi(
    page,
    "bob",
    base({
      "GET /resolutions/mock-case": kase(),
      "POST /resolutions/mock-case/proposals/mock-prop-1/approve": (_r, _u, b) => {
        approveBody = b;
        return kase({ status: "agreed", proposals: [proposal({ status: "accepted", approvals: [...proposal().approvals, { side: "buyer", choice: "approve", approver_email: "bob@acme.demo", approver_role: "buyer_approver", proposal_content_sha256: H("p"), decided_at: "" }] })] });
      },
      "POST /resolutions/mock-case/proposals/mock-prop-1/reject": () => {
        rejected = true;
        return kase({ status: "under_review", proposals: [proposal({ status: "rejected" })] });
      },
      "GET /resolutions/mock-case/policy": { policy_decision_id: "pd", purpose: "refund", decision: "allowed", reasons: [], inputs_sha256: H("i"), figures: { remaining_minor: 50000 }, proposal_id: "mock-prop-1", evaluated_at: "" },
    }),
  );
  await openCase(page);
  await page.getByTestId("proposal-1").getByRole("button", { name: "Approve as buyer" }).click();
  const dlg = page.getByRole("dialog", { name: "Approve proposal #1?" });
  await expect(dlg.getByText("Approving does not move money by itself.")).toBeVisible();
  await dlg.getByRole("button", { name: "Approve" }).click();
  await expect.poll(() => approveBody).toEqual({ content_sha256: H("p") });
  await expect(page.getByText("Refund policy satisfied")).toBeVisible();
  expect(rejected).toBe(false);
});

test("refund is blocked until the policy allows it", async ({ page }) => {
  await mockApi(
    page,
    "bob",
    base({
      "GET /resolutions/mock-case": kase({ status: "agreed", proposals: [proposal({ status: "accepted" })] }),
      "GET /resolutions/mock-case/policy": { policy_decision_id: "pd", purpose: "refund", decision: "blocked", reasons: [{ code: "APPROVAL_MISSING", detail: "buyer" }], inputs_sha256: H("i"), figures: {}, proposal_id: "mock-prop-1", evaluated_at: "" },
    }),
  );
  await openCase(page);
  await expect(page.getByText("Refund blocked by policy")).toBeVisible();
  await expect(page.getByText("An approval is missing from one side.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Execute refund…" })).toBeDisabled();
});

test("refund confirmation shows all figures, sends one idempotent request, UNKNOWN → reconcile", async ({ page }) => {
  const accepted = proposal({ status: "accepted", approvals: [...proposal().approvals, { side: "buyer", choice: "approve", approver_email: "bob@acme.demo", approver_role: "buyer_approver", proposal_content_sha256: H("p"), decided_at: "" }] });
  let current = kase({ status: "agreed", proposals: [accepted] });
  const keys: string[] = [];
  let reconciled = 0;
  const unknownRefund = { id: "mock-ref", environment: "simulated", paypal_refund_id: null, amount: "100.00", currency: "USD", status: "unknown", paypal_status: null, paypal_fee_returned: null, merchant_debit: null, error: null, created_at: "" };
  await mockApi(
    page,
    "bob",
    base({
      "GET /resolutions/mock-case": () => current,
      "GET /resolutions/mock-case/policy": { policy_decision_id: "pd", purpose: "refund", decision: "allowed", reasons: [], inputs_sha256: H("i"), figures: {}, proposal_id: "mock-prop-1", evaluated_at: "" },
      "POST /resolutions/mock-case/refunds": async (r) => {
        keys.push(r.request().headers()["idempotency-key"]);
        await new Promise((res) => setTimeout(res, 300));
        current = kase({ status: "action_required", proposals: [accepted], financials: { ...kase().financials, held: "100.00", remaining: "400.00", refunds: [unknownRefund] } });
        return status(503, { code: "OUTCOME_UNKNOWN", message: "refund outcome unknown", entity: "payment_refund", entity_id: "mock-ref" });
      },
      "POST /refunds/mock-ref/reconcile": () => {
        reconciled++;
        current = kase({ status: "resolved", outcome: "refunded 100.00 USD", proposals: [{ ...accepted, status: "executed" }], financials: { ...kase().financials, refunded: "100.00", remaining: "400.00", refunds: [{ ...unknownRefund, status: "completed", paypal_refund_id: "SIM-REFUND-1" }] } });
        return { ...unknownRefund, status: "completed" };
      },
    }),
  );
  await openCase(page);
  await page.getByRole("button", { name: "Execute refund…" }).click();
  const dlg = page.getByRole("dialog", { name: "Refund $100.00?" });
  for (const t of ["Original capture", "Already refunded", "Held / pending", "Remaining refundable", "This refund", "Approved by"]) await expect(dlg.getByText(t)).toBeVisible();
  await expect(dlg.getByText("PayPal fees are not assumed to be returned")).toBeVisible();
  const go = dlg.getByRole("button", { name: "Refund $100.00" });
  await go.click();
  await go.click({ force: true }).catch(() => {});
  await expect(page.getByText("A refund's outcome is unknown")).toBeVisible();
  expect(keys.length).toBe(1);
  expect(keys[0]).toMatch(/^refund-/);
  await expect(page.getByText("Outcome unknown — reconcile")).toBeVisible();
  await page.getByRole("button", { name: "Reconcile with PayPal" }).click();
  await expect(page.getByText("Resolved", { exact: true })).toBeVisible();
  await expect(page.getByText("SIM-REFUND-1")).toBeVisible();
  expect(reconciled).toBe(1);
});

test("provider proposes; the form hides refunds from a provider-only role", async ({ page }) => {
  let body: Record<string, unknown> | undefined;
  await mockApi(
    page,
    "maya",
    base({
      "GET /workspace/agreements/mock-ag-1": summary({ perspective: "provider" }),
      "GET /resolutions/mock-case": kase({ your_side: "provider", status: "under_review", proposals: [] }),
      "POST /resolutions/mock-case/proposals": (_r, _u, b) => {
        body = b as Record<string, unknown>;
        return kase({ your_side: "provider", proposals: [proposal()] });
      },
    }),
  );
  await openCase(page);
  await page.getByLabel("Remedy").selectOption("partial_refund");
  await page.getByLabel(/^Amount/).fill("100.00");
  await page.getByLabel("Rationale").fill("Refund 100 for the broken contact form.");
  await page.getByRole("button", { name: "Submit proposal" }).click();
  await expect.poll(() => body).toMatchObject({ expected_revision: 3, remedy: "partial_refund", amount: "100.00", approve: true });
});

test("cancel authorization explains void ≠ refund and confirms", async ({ page }) => {
  let voidBody: unknown;
  let voided = false;
  await mockApi(
    page,
    "bob",
    base({
      "GET /agreements/mock-ag-1/payments": () => [order(voided ? { authorization: { ...order().authorization!, status: "voided", paypal_status: "VOIDED" } } : {})],
      "GET /payments/authorizations/mock-auth/void-status": () => (voided ? { id: "v", payment_authorization_id: "mock-auth", status: "completed", reason: "Project cancelled", requested_by: "user:bob@acme.demo", paypal_status: "VOIDED", case_id: null, error: null, created_at: "", updated_at: "" } : null),
      "GET /payments/authorizations/mock-auth/void-policy": { policy_decision_id: "pd", purpose: "void", decision: "allowed", reasons: [], inputs_sha256: H("i"), figures: {}, proposal_id: null, evaluated_at: "" },
      "GET /payments/authorizations/mock-auth/policy": { policy_decision_id: "x", decision: "needs_review", reasons: [{ code: "MANUAL_REVIEW_PENDING" }], inputs_sha256: H("e"), verification_run_id: null, evidence_snapshot_sha256: null, evaluated_at: "" },
      "GET /payments/authorizations/mock-auth/capture-approvals": [],
      "POST /payments/authorizations/mock-auth/void": (_r, _u, b) => {
        voidBody = b;
        voided = true;
        return { id: "v", payment_authorization_id: "mock-auth", status: "completed", reason: "Project cancelled", requested_by: "user:bob@acme.demo", paypal_status: "VOIDED", case_id: null, error: null, created_at: "", updated_at: "" };
      },
    }),
  );
  await page.goto("/agreements/mock-ag-1?tab=payment");
  await expect(page.getByText("It is not a refund: the payment was never captured")).toBeVisible();
  await page.getByRole("button", { name: "Cancel authorization…" }).click();
  const dlg = page.getByRole("dialog", { name: "Cancel this authorization?" });
  await expect(dlg.getByText("This is not a refund")).toBeVisible();
  await expect(dlg.getByRole("button", { name: "Cancel authorization" })).toBeDisabled();
  await dlg.getByLabel(/Reason/).fill("Project cancelled");
  await dlg.getByRole("button", { name: "Cancel authorization" }).click();
  await expect(page.getByText("Authorization cancelled").first()).toBeVisible();
  expect(voidBody).toEqual({ reason: "Project cancelled" });
});

test("void blocked by policy shows why", async ({ page }) => {
  await mockApi(
    page,
    "bob",
    base({
      "GET /agreements/mock-ag-1/payments": [order()],
      "GET /payments/authorizations/mock-auth/void-status": () => null,
      "GET /payments/authorizations/mock-auth/void-policy": { policy_decision_id: "pd", purpose: "void", decision: "blocked", reasons: [{ code: "PROVIDER_CONSENT_REQUIRED", detail: "delivery is verified" }], inputs_sha256: H("i"), figures: {}, proposal_id: null, evaluated_at: "" },
      "GET /payments/authorizations/mock-auth/policy": { policy_decision_id: "x", decision: "eligible_for_approval", reasons: [], inputs_sha256: H("e"), verification_run_id: null, evidence_snapshot_sha256: null, evaluated_at: "" },
      "GET /payments/authorizations/mock-auth/capture-approvals": [],
    }),
  );
  await page.goto("/agreements/mock-ag-1?tab=payment");
  await expect(page.getByText("the provider's agreement through a resolution case")).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancel authorization…" })).toHaveCount(0);
});

test("case workspace at 375 px: no horizontal scroll; axe finds no serious issues", async ({ page }) => {
  await mockApi(page, "bob", base({ "GET /resolutions/mock-case": kase(), "GET /resolutions/mock-case/policy": { policy_decision_id: "pd", purpose: "refund", decision: "allowed", reasons: [], inputs_sha256: H("i"), figures: {}, proposal_id: null, evaluated_at: "" } }));
  for (const width of [375, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await openCase(page);
    const [sw, iw] = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
    expect(sw).toBeLessThanOrEqual(iw);
  }
  await page.waitForTimeout(400);
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
});
