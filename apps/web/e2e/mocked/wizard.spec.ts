// MOCKED API — agreement wizard behaviour with a canned AI draft (not real model output).
import { expect, test } from "@playwright/test";
import { AGREEMENT, fakeCookie, H, MERCHANTS, mockApi, status, VERSION } from "./fixtures";

const REQ = "Build a mobile-friendly landing page for $500. The contact form must work, the website must be accessible and Lighthouse performance must score at least 90.";

const DRAFT = {
  id: "mock-draft",
  status: "proposed",
  requirements_text: REQ,
  ai_call_id: "mock-call",
  accepted_agreement_id: null,
  created_at: "2026-10-16T09:00:00Z",
  review: {
    service_summary: "Responsive landing page with a working contact form",
    criteria: [
      { key: "contact_form_works", title: "Contact form can be submitted", kind: "contact_form", origin: "stated", blocking: true, source_quote: "The contact form must work", rationale: null, expected: {}, needs_configuration: ["contact form field selectors and test values"], manual_review_instructions: null, review_notes: [] },
      { key: "lighthouse_performance", title: "Lighthouse performance ≥ 90", kind: "lighthouse_performance", origin: "stated", blocking: true, source_quote: "Lighthouse performance must score at least 90", rationale: null, expected: { min_performance_score: 90 }, needs_configuration: [], manual_review_instructions: null, review_notes: [] },
      { key: "website_accessible", title: "Website is accessible", kind: "manual_review", origin: "stated", blocking: true, source_quote: "the website must be accessible", rationale: null, expected: {}, needs_configuration: [], manual_review_instructions: "Check WCAG 2.2 AA", review_notes: [] },
    ],
    suggestions: [
      { key: "site_is_available", title: "Page responds over HTTP", kind: "http_availability", origin: "suggested", blocking: false, source_quote: "Build a mobile-friendly landing page for $500.", rationale: "Other checks depend on the page being live.", expected: {}, needs_configuration: [], manual_review_instructions: null, review_notes: [] },
    ],
    rejected_criteria: [],
    ambiguities: [{ topic: "Accessibility standard", question: "Which standard, e.g. WCAG 2.2 AA?" }],
    unsupported_requirements: [],
  },
};

test.beforeEach(async ({ context, baseURL }) => fakeCookie(context, baseURL!));

test("AI criteria: stated vs suggested, opt-in, blocking protection, configuration, payload", async ({ page }) => {
  let accepted: Record<string, unknown> | undefined;
  await mockApi(page, "alice", {
    "GET /workspace/merchants": MERCHANTS,
    "POST /agreements/drafts": DRAFT,
    "GET /agreements/drafts/mock-draft": DRAFT,
    "POST /agreements/drafts/mock-draft/accept": (_r, _u, body) => {
      accepted = body as Record<string, unknown>;
      return { agreement_id: "mock-ag-1", status: "draft", version: 1 };
    },
    "GET /agreements/mock-ag-1": { ...AGREEMENT, current_locked_version_id: null, status: "draft", versions: [{ ...VERSION, status: "draft", content_hash: null, locked_at: null }] },
  });
  await page.goto("/agreements/new");
  await page.getByRole("button", { name: "Use the example request" }).click();
  await page.getByRole("button", { name: "Generate Acceptance Criteria" }).click();

  // Step 2: stated criteria included and blocking; suggestion visibly separate and off.
  const stated = page.getByTestId("criterion-contact_form_works");
  await expect(stated.getByText("Buyer-stated")).toBeVisible();
  await expect(stated.getByText("Blocking", { exact: true })).toBeVisible();
  await expect(stated.getByRole("checkbox").first()).toBeChecked();
  const sugg = page.getByTestId("criterion-site_is_available");
  await expect(sugg.getByText("AI-suggested")).toBeVisible();
  await expect(sugg.getByRole("checkbox")).not.toBeChecked();
  await expect(page.getByText("Which standard, e.g. WCAG 2.2 AA?")).toBeVisible();
  await sugg.getByRole("checkbox").check();
  // Weakening a mandatory requirement is an explicit, labelled buyer decision.
  await page.getByTestId("criterion-lighthouse_performance").getByText("Make non-blocking").click();
  await expect(page.getByTestId("criterion-lighthouse_performance").getByText("Non-blocking", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Continue to configuration" }).click();

  // Step 3: nothing invented — contact form selectors stay empty until the buyer acts.
  await expect(page.getByText("Still needed before the agreement can be created")).toBeVisible();
  await expect(page.getByRole("button", { name: "Create draft agreement" })).toBeDisabled();
  await expect(page.getByLabel("Form selector")).toHaveValue("");
  await expect(page.getByLabel("Minimum performance score (0–100)")).toHaveValue("90");
  await expect(page.getByLabel("Price (USD)")).toHaveValue("500");
  await page.getByRole("button", { name: "Use the standard field set" }).click();
  await page.getByLabel("Delivery deadline").fill("2030-01-15");
  await page.getByRole("button", { name: "Create draft agreement" }).click();

  await expect(page.getByText("Draft agreement created")).toBeVisible();
  expect(accepted).toMatchObject({
    merchant_account_id: "mock-merchant",
    amount: "500",
    accept_suggestions: ["site_is_available"],
    include_keys: ["contact_form_works", "lighthouse_performance", "website_accessible"],
    blocking_overrides: { lighthouse_performance: false },
  });
  const cfg = (accepted!.configuration as Record<string, Record<string, unknown>>).contact_form_works;
  expect(cfg.form_selector).toBe("form");
  expect((accepted!.configuration as Record<string, Record<string, unknown>>).lighthouse_performance.min_score).toBe(90);
  // Step 4: locking is a separate explicit action.
  await expect(page.getByRole("button", { name: "Propose this version" })).toBeVisible();
});

test("AI failure is reported honestly with retry", async ({ page }) => {
  await mockApi(page, "alice", {
    "GET /workspace/merchants": MERCHANTS,
    "POST /agreements/drafts": { ...DRAFT, status: "failed", review: { error: "budget_exceeded", fallback: "AI unavailable — nothing was generated." } },
  });
  await page.goto("/agreements/new");
  await page.getByLabel("Request").fill(REQ);
  await page.getByRole("button", { name: "Generate Acceptance Criteria" }).click();
  await expect(page.getByText("AI could not draft criteria")).toBeVisible();
  await expect(page.getByText("The AI spending budget is exhausted")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
});

test("provider API errors surface without losing the request", async ({ page }) => {
  await mockApi(page, "alice", { "GET /workspace/merchants": MERCHANTS, "POST /agreements/drafts": status(403, { code: "FORBIDDEN", message: "drafting an agreement requires role buyer_admin" }) });
  await page.goto("/agreements/new");
  await page.getByLabel("Request").fill(REQ);
  await page.getByRole("button", { name: "Generate Acceptance Criteria" }).click();
  await expect(page.getByText("Your role does not allow this action.")).toBeVisible();
  await expect(page.getByLabel("Request")).toHaveValue(REQ);
});

test("approve-and-lock quotes the content hash and needs confirmation", async ({ page }) => {
  let approveBody: unknown;
  const proposed = { ...AGREEMENT, current_locked_version_id: null, status: "draft", versions: [{ ...VERSION, status: "proposed", locked_at: null }] };
  await mockApi(page, "alice", {
    "GET /workspace/merchants": MERCHANTS,
    "GET /agreements/mock-ag-1": proposed,
    "GET /workspace/agreements/mock-ag-1": { id: "mock-ag-1", title: VERSION.title, status: "draft", perspective: "buyer", buyer_organization_name: "Acme", merchant_display_name: "Northwind Studio", amount: "500.00", currency: "USD", version_number: 1, version_status: "proposed", content_hash: H("c"), locked: false, created_at: "", latest_run: null, latest_completed_run: null, payment: null },
    "GET /agreements/mock-ag-1/verification-runs": [],
    "POST /agreements/mock-ag-1/versions/1/approve": (_r, _u, body) => {
      approveBody = body;
      return AGREEMENT;
    },
  });
  await page.goto("/agreements/mock-ag-1");
  await page.getByRole("button", { name: "Approve and lock…" }).click();
  const dialog = page.getByRole("dialog", { name: "Approve and lock this agreement?" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "Approve and lock" }).click();
  await expect(page.getByText("Locked", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve and lock…" })).toHaveCount(0);
  expect(approveBody).toEqual({ content_hash: H("c") });
});
