// MOCKED API fixtures for isolated UI tests. Every id is prefixed "mock-"; none of this is
// real backend output. Shapes follow apps/api response models.
import type { BrowserContext, Page, Route } from "@playwright/test";

export const H = (c: string) => c.repeat(64).slice(0, 64);
const ORG_BUYER = "mock-org-buyer";
const ORG_MERCHANT = "mock-org-merchant";
const NOW = "2026-10-16T09:00:00Z";

export const USERS = {
  alice: { user_id: "mock-u-alice", email: "alice@acme.demo", display_name: "Alice (buyer admin)", kind: "human", demo_account: true, organizations: [{ id: ORG_BUYER, name: "Acme Outdoor Co. (demo buyer)", kind: "buyer", roles: ["buyer_admin"] }] },
  bob: { user_id: "mock-u-bob", email: "bob@acme.demo", display_name: "Bob (buyer approver)", kind: "human", demo_account: true, organizations: [{ id: ORG_BUYER, name: "Acme Outdoor Co. (demo buyer)", kind: "buyer", roles: ["buyer_approver"] }] },
  rita: { user_id: "mock-u-rita", email: "rita@acme.demo", display_name: "Rita (accessibility reviewer)", kind: "human", demo_account: true, organizations: [{ id: ORG_BUYER, name: "Acme Outdoor Co. (demo buyer)", kind: "buyer", roles: ["buyer_reviewer"] }] },
  maya: { user_id: "mock-u-maya", email: "maya@northwind.demo", display_name: "Maya (agency owner)", kind: "human", demo_account: true, organizations: [{ id: ORG_MERCHANT, name: "Northwind Studio (demo provider)", kind: "merchant", roles: ["merchant_admin", "provider"] }] },
  bot: { user_id: "mock-u-bot", email: "ai-assistant@system.local", display_name: "AI assistant (service)", kind: "service", demo_account: true, organizations: [{ id: ORG_BUYER, name: "Acme Outdoor Co. (demo buyer)", kind: "buyer", roles: ["buyer_reviewer"] }] },
} as const;

export const MERCHANTS = [{ id: "mock-merchant", display_name: "Northwind Studio", organization_id: ORG_MERCHANT, organization_name: "Northwind Studio (demo provider)", environment: "sandbox" }];

const crit = (id: string, key: string, title: string, kind: string, spec: Record<string, unknown>, manual = false) => ({
  id,
  position: 0,
  key,
  title,
  kind,
  verification_method: manual ? "manual_review_required" : "automated",
  blocking: true,
  source: "ai_proposed",
  spec: { kind, ...spec },
});

export const VERSION = {
  id: "mock-v1",
  version_number: 1,
  status: "locked",
  revision: 2,
  supersedes_version_id: null,
  title: "Landing page for spring campaign",
  description: "Responsive landing page with a working contact form",
  requirements_text: "Build a mobile-friendly landing page for $500. The contact form must work, the website must be accessible and Lighthouse performance must score at least 90.",
  amount: "500.00",
  currency: "USD",
  delivery_deadline: "2026-10-30T23:59:00Z",
  content_hash: H("c"),
  proposed_at: NOW,
  locked_at: NOW,
  criteria: [
    crit("mock-c-form", "contact_form_works", "Contact form can be submitted", "contact_form", { success_text: "Thank you" }),
    crit("mock-c-lh", "lighthouse_performance", "Lighthouse performance ≥ 90", "lighthouse_performance", { min_score: 90 }),
    crit("mock-c-a11y", "website_accessible", "Website is accessible (human review)", "manual_review", { instructions: "Check against WCAG 2.2 AA" }, true),
  ],
  approvals: [],
};

export const AGREEMENT = {
  id: "mock-ag-1",
  status: "active",
  service_category: "landing_page",
  buyer_organization_id: ORG_BUYER,
  merchant_account_id: "mock-merchant",
  current_locked_version_id: "mock-v1",
  created_at: NOW,
  versions: [VERSION],
};

export function run(verdict: "fail" | "needs_manual_review", overrides: Record<string, unknown> = {}) {
  const formPass = verdict !== "fail";
  return {
    id: verdict === "fail" ? "mock-run-broken" : "mock-run-fixed",
    status: "completed",
    verdict,
    verified_pass: false,
    evidence_snapshot_sha256: verdict === "fail" ? H("1") : H("2"),
    agreement_version_id: "mock-v1",
    agreement_content_hash: H("c"),
    delivery_url: `http://127.0.0.1:8020/landing-page-${verdict === "fail" ? "broken" : "fixed"}/`,
    submission_hash: H("s"),
    tool_versions: { chromium: "mock", lighthouse: "mock" },
    started_at: NOW,
    finished_at: NOW,
    error: null,
    results: [
      {
        criterion_id: "mock-c-form",
        criterion_key: "contact_form_works",
        kind: "contact_form",
        blocking: true,
        outcome: formPass ? "pass" : "fail",
        reason_code: formPass ? "OK" : "SUCCESS_TEXT_MISSING",
        measured: { form_found: true, success_text_seen: formPass, sink_receipts_in_db: formPass ? 1 : 0 },
        expected: { success_text: "Thank you" },
        artifact_ids: ["mock-art-shot"],
        duration_ms: 2100,
      },
      {
        criterion_id: "mock-c-lh",
        criterion_key: "lighthouse_performance",
        kind: "lighthouse_performance",
        blocking: true,
        outcome: "pass",
        reason_code: "OK",
        measured: { scores: [97], band: 5 },
        expected: { min_score: 90 },
        artifact_ids: [],
        duration_ms: 9000,
      },
      {
        criterion_id: "mock-c-a11y",
        criterion_key: "website_accessible",
        kind: "manual_review",
        blocking: true,
        outcome: "manual_review_required",
        reason_code: "MANUAL_CRITERION",
        measured: {},
        expected: { instructions: "Check against WCAG 2.2 AA" },
        artifact_ids: [],
        duration_ms: null,
      },
    ],
    artifacts: [{ id: "mock-art-shot", kind: "screenshot", label: "contact-form-after-submit.png", sha256: H("a"), size_bytes: 1234, content_type: "image/png" }],
    form_receipts: formPass ? [{ id: "mock-rcpt", sink_ref: "default", field_names: ["email", "message", "name"], values_sha256: H("v"), content_length: 80, received_at: NOW }] : [],
    ...overrides,
  };
}

export function summary(overrides: Record<string, unknown> = {}) {
  return {
    id: "mock-ag-1",
    title: VERSION.title,
    status: "active",
    perspective: "buyer",
    buyer_organization_name: "Acme Outdoor Co. (demo buyer)",
    merchant_display_name: "Northwind Studio",
    amount: "500.00",
    currency: "USD",
    version_number: 1,
    version_status: "locked",
    content_hash: H("c"),
    locked: true,
    created_at: NOW,
    latest_run: null,
    latest_completed_run: null,
    payment: null,
    ...overrides,
  };
}

export function order(over: Record<string, unknown> = {}) {
  return {
    id: "mock-order",
    environment: "sandbox",
    simulated: false,
    reference: "og_mock",
    paypal_order_id: "MOCKORDER123",
    amount: "500.00",
    currency: "USD",
    status: "authorized",
    paypal_status: "COMPLETED",
    approval_url: "https://www.sandbox.paypal.com/checkoutnow?token=MOCKORDER123",
    authorization: {
      id: "mock-auth",
      paypal_authorization_id: "MOCKAUTH456",
      amount: "500.00",
      currency: "USD",
      status: "authorized",
      paypal_status: "CREATED",
      payee_merchant_id: "MOCKPAYEE",
      expires_at: "2026-11-14T07:00:00Z",
      honor_period_ends_at: null,
    },
    capture: null,
    created_at: NOW,
    ...over,
  };
}

export const POLICY_ELIGIBLE = {
  policy_decision_id: "mock-pd-1",
  decision: "eligible_for_approval",
  reasons: [],
  inputs_sha256: H("e"),
  verification_run_id: "mock-run-fixed",
  evidence_snapshot_sha256: H("2"),
  evaluated_at: NOW,
};

export type Handler = (route: Route, url: URL, body: unknown) => unknown | Promise<unknown>;

/** Intercepts the BFF. `routes` maps "METHOD /path" (no /api/og prefix, no query) to a
 *  handler returning {status, json} or a plain JSON body. Unmatched calls fail loudly. */
export async function mockApi(page: Page, user: keyof typeof USERS | null, routes: Record<string, Handler | object> = {}) {
  const calls: { method: string; path: string; body: unknown; headers: Record<string, string> }[] = [];
  await page.route("**/api/og/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^\/api\/og/, "");
    const body = req.postData() ? JSON.parse(req.postData()!) : undefined;
    calls.push({ method: req.method(), path: path + url.search, body, headers: req.headers() });
    if (path === "/workspace/context") {
      return user ? route.fulfill({ json: USERS[user] }) : route.fulfill({ status: 401, json: { code: "SESSION_EXPIRED", message: "Please sign in." } });
    }
    const key = `${req.method()} ${path}`;
    const h = routes[key];
    if (h === undefined) {
      return route.fulfill({ status: 599, json: { code: "UNMOCKED", message: `unmocked ${key}` } });
    }
    const out = typeof h === "function" ? await (h as Handler)(route, url, body) : h;
    if (out && typeof out === "object" && "__status" in (out as Record<string, unknown>)) {
      const o = out as { __status: number; json: unknown };
      return route.fulfill({ status: o.__status, json: o.json });
    }
    return route.fulfill({ json: out });
  });
  return calls;
}

export const status = (s: number, json: unknown) => ({ __status: s, json });

/** The proxy only checks that a session cookie exists; the API (mocked here) decides. */
export async function fakeCookie(context: BrowserContext, baseURL: string) {
  await context.addCookies([{ name: "og_session", value: "mock-session", url: baseURL }]);
}
