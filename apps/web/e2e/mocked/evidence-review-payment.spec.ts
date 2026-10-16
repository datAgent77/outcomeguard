// MOCKED API — evidence viewer, manual-review permissions and payment governance UI.
import { expect, test, type Page } from "@playwright/test";
import { AGREEMENT, fakeCookie, H, MERCHANTS, mockApi, order, POLICY_ELIGIBLE, run, status, summary, USERS, type Handler } from "./fixtures";

test.beforeEach(async ({ context, baseURL }) => fakeCookie(context, baseURL!));

function base(extra: Record<string, Handler | object> = {}, runs = [run("needs_manual_review"), run("fail")]) {
  return {
    "GET /workspace/merchants": MERCHANTS,
    "GET /agreements/mock-ag-1": AGREEMENT,
    "GET /workspace/agreements/mock-ag-1": summary({
      latest_run: { id: runs[0].id, status: "completed", verdict: runs[0].verdict, created_at: "", finished_at: "", counts: {}, awaiting_review: 1 },
      latest_completed_run: { id: runs[0].id, status: "completed", verdict: runs[0].verdict, created_at: "", finished_at: "", counts: {}, awaiting_review: 1 },
    }),
    "GET /agreements/mock-ag-1/verification-runs": runs,
    "GET /verification/runs/mock-run-fixed/analyses": [],
    "GET /verification/runs/mock-run-broken/analyses": [],
    "GET /verification/runs/mock-run-fixed/manual-reviews": [],
    "GET /verification/runs/mock-run-broken/manual-reviews": [],
    ...extra,
  };
}

async function openEvidence(page: Page, runId = "mock-run-fixed") {
  await page.goto(`/agreements/mock-ag-1?tab=evidence&run=${runId}`);
  await expect(page.getByText("Verification Report")).toBeVisible();
}

test("evidence report shows verdict, measurements, separate statuses and labelled AI text", async ({ page }) => {
  await mockApi(
    page,
    "alice",
    base({
      "GET /verification/runs/mock-run-broken/analyses": [
        {
          id: "mock-an",
          run_id: "mock-run-broken",
          status: "grounded",
          run_verdict: "fail",
          ai_call_id: "x",
          created_at: "",
          grounded: {
            summary: "The contact form did not show the success text.",
            limitations: ["Single mobile viewport."],
            explanations: [{ criterion_key: "contact_form_works", outcome: "fail", confidence: "high", explanation: "Success text was not seen after submit.", remediation: "Make the form show “Thank you” after a successful submit." }],
          },
        },
      ],
    }),
  );
  await openEvidence(page, "mock-run-broken");
  await expect(page.getByRole("heading", { name: "Failed" })).toBeVisible();
  const form = page.getByTestId("result-contact_form_works");
  await expect(form.getByText("FAIL")).toBeVisible();
  await expect(form.getByText("SUCCESS_TEXT_MISSING")).toBeVisible();
  await expect(form.getByText("The submission reached the test endpoint, but “Thank you” never appeared.").or(form.getByText(/no submission reached the test endpoint/))).toBeVisible();
  await form.getByText("Technical measurements (expected vs actual)").click();
  await expect(form.getByText("Success text seen")).toBeVisible();
  await expect(form.getByText("AI interpretation")).toBeVisible();
  await expect(form.getByText("Suggested remediation:")).toBeVisible();
  await expect(page.getByText("An interpretation of the deterministic results above — not the verdict.")).toBeVisible();
  // agreement / verification / payment are three separate status fields
  for (const label of ["Agreement", "Verification", "Payment"]) await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
  // Artifact viewer shows only what is fetched from the API.
  let artifactCalls = 0;
  await page.route("**/api/og/verification/artifacts/mock-art-shot", (r) => {
    artifactCalls++;
    return r.fulfill({ status: 404, json: { code: "NOT_FOUND" } });
  });
  await form.getByRole("button", { name: /Screenshot/ }).click();
  await expect(page.getByText("Could not load this artifact")).toBeVisible();
  expect(artifactCalls).toBe(1);
});

test("AI unavailable does not block the deterministic report", async ({ page }) => {
  await mockApi(page, "alice", base({ "POST /verification/runs/mock-run-fixed/analysis": status(503, { code: "AI_UNAVAILABLE", message: "provider unavailable" }) }));
  await openEvidence(page);
  await page.getByRole("button", { name: "Explain with AI" }).click();
  await expect(page.getByText("The deterministic report above is unaffected.")).toBeVisible();
  await expect(page.getByTestId("result-lighthouse_performance").getByText("PASS")).toBeVisible();
});

test("authorized reviewer: rationale, attestation and confirmation are all required", async ({ page }) => {
  let posted: Record<string, unknown> | undefined;
  await mockApi(
    page,
    "rita",
    base({
      "GET /verification/runs/mock-run-fixed": run("needs_manual_review"),
      "POST /verification/runs/mock-run-fixed/manual-reviews": (_r, _u, b) => {
        posted = b as Record<string, unknown>;
        return { id: "mock-rev", criterion_id: "mock-c-a11y", verification_run_id: "mock-run-fixed", decision: "satisfied", rationale: posted.rationale, reviewer_user_id: "mock-u-rita", reviewer_email: "rita@acme.demo", reviewer_role: "buyer_reviewer", evidence_snapshot_sha256: H("2"), decided_at: "" };
      },
    }),
  );
  await openEvidence(page);
  const panel = page.getByTestId("manual-review-panel");
  await expect(panel.getByText("Human Review Required")).toBeVisible();
  const submit = panel.getByRole("button", { name: "Record decision…" });
  await expect(submit).toBeDisabled();
  await panel.getByLabel("Satisfied", { exact: true }).check();
  await panel.getByLabel(/Rationale/).fill("Keyboard, contrast and labels checked.");
  await expect(submit).toBeDisabled();
  await panel.getByLabel(/I personally reviewed/).check();
  await submit.click();
  const dialog = page.getByRole("dialog", { name: "Record your review decision?" });
  await expect(dialog.getByText("personal attestation")).toBeVisible();
  await dialog.getByRole("button", { name: /Record “Satisfied”/ }).click();
  await expect.poll(() => posted).toMatchObject({ criterion_id: "mock-c-a11y", decision: "satisfied", evidence_snapshot_sha256: H("2") });
});

test("stale evidence while the review is open is rejected and asks for a refresh", async ({ page }) => {
  let posted = false;
  await mockApi(
    page,
    "rita",
    base({
      "GET /verification/runs/mock-run-fixed": run("needs_manual_review", { evidence_snapshot_sha256: H("9") }),
      "POST /verification/runs/mock-run-fixed/manual-reviews": () => {
        posted = true;
        return {};
      },
    }),
  );
  await openEvidence(page);
  const panel = page.getByTestId("manual-review-panel");
  await panel.getByLabel("Not satisfied", { exact: true }).check();
  await panel.getByLabel(/Rationale/).fill("Contrast fails on the hero button.");
  await panel.getByLabel(/I personally reviewed/).check();
  await panel.getByRole("button", { name: "Record decision…" }).click();
  await page.getByRole("dialog").getByRole("button", { name: /Record/ }).click();
  await expect(panel.getByText("Evidence changed")).toBeVisible();
  expect(posted).toBe(false);
});

for (const who of ["maya", "bot"] as const) {
  test(`${USERS[who].kind === "service" ? "service account" : "provider"} gets no review action`, async ({ page }) => {
    await mockApi(page, who, base());
    await openEvidence(page);
    const panel = page.getByTestId("manual-review-panel");
    await expect(panel.getByText("Human Review Required")).toBeVisible();
    await expect(panel.getByRole("button", { name: "Record decision…" })).toHaveCount(0);
    await expect(panel.getByText(who === "bot" ? "Service accounts (including AI integrations) cannot decide" : "Providers cannot review their own delivery")).toBeVisible();
  });
}

test("scripted demo reviews are labelled as such", async ({ page }) => {
  await mockApi(
    page,
    "alice",
    base({
      "GET /verification/runs/mock-run-fixed/manual-reviews": [
        { id: "r", criterion_id: "mock-c-a11y", verification_run_id: "mock-run-fixed", decision: "satisfied", rationale: "SCRIPTED DEMO DECISION: no human review.", reviewer_user_id: "u", reviewer_email: "rita@acme.demo", reviewer_role: "buyer_reviewer", evidence_snapshot_sha256: H("2"), decided_at: "" },
      ],
    }),
  );
  await openEvidence(page);
  await expect(page.getByText("Scripted demo decision — not a human review")).toBeVisible();
});

// ------------------------------------------------------------------ payment

function payment(extra: Record<string, Handler | object>) {
  return base({ "GET /agreements/mock-ag-1/payments": [order()], "GET /payments/authorizations/mock-auth/capture-approvals": [], ...extra });
}

test("blocked policy shows plain-English reasons and no capture action", async ({ page }) => {
  await mockApi(
    page,
    "bob",
    payment({
      "GET /payments/authorizations/mock-auth/policy": { ...POLICY_ELIGIBLE, decision: "blocked", reasons: [{ code: "BLOCKING_CRITERION_FAILED", detail: "contact_form_works" }, { code: "MANUAL_REVIEW_PENDING", detail: "website_accessible" }] },
    }),
  );
  await page.goto("/agreements/mock-ag-1?tab=payment");
  await expect(page.getByText("Payment is authorized, not captured. Authorization can expire.")).toBeVisible();
  await expect(page.getByText("Capture blocked")).toBeVisible();
  await expect(page.getByText("A blocking criterion failed verification.")).toBeVisible();
  await expect(page.getByText("A blocking criterion is waiting for an authorized human review.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Approve capture/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Capture \$500/ })).toHaveCount(0);
});

test("eligible: approval needs confirmation and binds the inputs hash; capture is a separate step", async ({ page }) => {
  let approvals: unknown[] = [];
  let approveBody: unknown;
  let captureCalls = 0;
  await mockApi(
    page,
    "bob",
    payment({
      "GET /payments/authorizations/mock-auth/policy": POLICY_ELIGIBLE,
      "GET /payments/authorizations/mock-auth/capture-approvals": () => approvals,
      "POST /payments/authorizations/mock-auth/capture-approvals": (_r, _u, b) => {
        approveBody = b;
        approvals = [{ id: "mock-appr", inputs_sha256: H("e"), policy_decision_id: "mock-pd-1", approver_user_id: "mock-u-bob", approver_email: "bob@acme.demo", approver_role: "buyer_approver", approved_at: "2026-10-16T09:01:00Z", consumed_by_capture_id: null }];
        return { approval_id: "mock-appr" };
      },
      "POST /payments/authorizations/mock-auth/captures": async () => {
        captureCalls++;
        await new Promise((r) => setTimeout(r, 400));
        return status(503, { code: "OUTCOME_UNKNOWN", message: "capture outcome unknown", entity: "capture", entity_id: "mock-cap" });
      },
    }),
  );
  await page.goto("/agreements/mock-ag-1?tab=payment");
  await page.getByRole("button", { name: "Approve capture…" }).click();
  const dlg = page.getByRole("dialog", { name: "Approve this capture?" });
  await expect(dlg.getByText("Approving does not capture")).toBeVisible();
  await dlg.getByRole("button", { name: "I approve the capture" }).click();
  await expect.poll(() => approveBody).toEqual({ inputs_sha256: H("e") });
  await expect(page.getByText("Capture approved by the buyer")).toBeVisible();

  await page.getByRole("button", { name: /Capture \$500\.00 via PayPal/ }).click();
  const cap = page.getByRole("dialog", { name: /Capture \$500\.00\?/ });
  const go = cap.getByRole("button", { name: "Capture payment" });
  await go.click();
  await go.click({ force: true }).catch(() => {}); // a second click while in flight must not send twice
  await expect(page.getByText("PayPal outcome unknown")).toBeVisible();
  await expect(page.getByText(/do not retry; reconcile/)).toBeVisible();
  expect(captureCalls).toBe(1);
});

test("UNKNOWN capture: no retry button, guarded reconcile, never shown as failed", async ({ page }) => {
  let reconciled = 0;
  const cap = { id: "mock-cap", environment: "sandbox", paypal_capture_id: null, amount: "500.00", currency: "USD", status: "unknown", paypal_status: null, settlement: { currency: "USD", gross_captured: "0.00", paypal_fee: null, net_at_capture: null, refunded_gross: "0.00", net_after_refunds_derived: null }, error: null, created_at: "" };
  const unknown = order({ capture: cap });
  await mockApi(
    page,
    "bob",
    payment({
      "GET /agreements/mock-ag-1/payments": [unknown],
      "GET /payments/authorizations/mock-auth/policy": { ...POLICY_ELIGIBLE, decision: "blocked", reasons: [{ code: "CAPTURE_IN_PROGRESS", detail: "reconcile the existing capture first" }] },
      "POST /payments/captures/mock-cap/reconcile": () => {
        reconciled++;
        return { ...cap, status: "completed", paypal_capture_id: "MOCKCAP789", paypal_status: "COMPLETED", settlement: { currency: "USD", gross_captured: "500.00", paypal_fee: "17.94", net_at_capture: "482.06", refunded_gross: "0.00", net_after_refunds_derived: "482.06" } };
      },
    }),
  );
  await page.goto("/agreements/mock-ag-1?tab=payment");
  await expect(page.getByText("Outcome unknown — do not retry")).toBeVisible();
  await expect(page.getByText("Capture rejected by PayPal")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Capture \$500/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Reconcile with PayPal…" }).click();
  const dlg = page.getByRole("dialog", { name: "Reconcile with PayPal?" });
  await expect(dlg.getByText("A second, independent capture is never created.")).toBeVisible();
  await dlg.getByRole("button", { name: "Reconcile" }).click();
  await expect(page.getByText("Payment captured")).toBeVisible();
  await expect(page.getByText("MOCKCAP789")).toBeVisible();
  expect(reconciled).toBe(1);
});

test("starting checkout sends an idempotency key and shows the PayPal approval link from the API", async ({ page }) => {
  let key: string | undefined;
  let orders: unknown[] = [];
  await mockApi(
    page,
    "alice",
    base({
      "GET /agreements/mock-ag-1/payments": () => orders,
      "POST /agreements/mock-ag-1/payments": (r) => {
        key = r.request().headers()["idempotency-key"];
        orders = [order({ status: "created", paypal_status: "PAYER_ACTION_REQUIRED", authorization: null })];
        return orders[0];
      },
    }),
  );
  await page.goto("/agreements/mock-ag-1?tab=payment");
  await page.getByRole("button", { name: "Start PayPal Sandbox checkout" }).click();
  const link = page.getByRole("link", { name: /Open PayPal approval/ });
  await expect(link).toHaveAttribute("href", "https://www.sandbox.paypal.com/checkoutnow?token=MOCKORDER123");
  await expect(link).toHaveAttribute("target", "_blank");
  expect(key).toMatch(/^pay-[0-9a-f-]{20,}$/);
});
