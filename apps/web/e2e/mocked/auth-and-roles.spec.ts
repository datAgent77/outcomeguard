// MOCKED API — isolated UI behaviour; no backend involved.
import { expect, test } from "@playwright/test";
import { fakeCookie, mockApi, status, summary } from "./fixtures";

test.describe("authentication (mocked API)", () => {
  test("protected pages redirect to login without a session cookie", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login\?next=%2Fdashboard/);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  });

  test("wrong credentials show a generic error and no token is exposed to JS", async ({ page }) => {
    await page.route("**/api/session/login", (r) =>
      r.fulfill({ status: 401, json: { code: "INVALID_CREDENTIALS", message: "Email or password is incorrect, or the account is temporarily locked." } }),
    );
    await page.goto("/login");
    await page.getByLabel("Email").fill("alice@acme.demo");
    await page.getByLabel("Password").fill("wrong-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Email or password is incorrect")).toBeVisible();
    expect(await page.evaluate(() => JSON.stringify(localStorage) + document.cookie)).not.toContain("og_session");
  });

  test("login sends the CSRF header and lands on the role's home", async ({ page, context, baseURL }) => {
    let csrf: string | undefined;
    await page.route("**/api/session/login", async (r) => {
      csrf = r.request().headers()["x-og-csrf"];
      await fakeCookie(context, baseURL!);
      await r.fulfill({ json: { ok: true } });
    });
    await mockApi(page, "maya", { "GET /workspace/agreements": [] });
    await page.goto("/login");
    await page.getByLabel("Email").fill("maya@northwind.demo");
    await page.getByLabel("Password").fill("whatever-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).toHaveURL(/\/provider$/);
    expect(csrf).toBe("1");
  });

  test("an expired session (API 401) sends the user back to login with a notice", async ({ page, context, baseURL }) => {
    await fakeCookie(context, baseURL!);
    await mockApi(page, "alice", { "GET /workspace/agreements": status(401, { code: "SESSION_EXPIRED", message: "expired" }) });
    await page.goto("/dashboard");
    await expect(page).toHaveURL(/\/login\?expired=1/);
    await expect(page.getByText("Your session expired")).toBeVisible();
  });

  test("logout calls the BFF and returns to login", async ({ page, context, baseURL }) => {
    await fakeCookie(context, baseURL!);
    let loggedOut = false;
    await page.route("**/api/session/logout", (r) => {
      loggedOut = true;
      return r.fulfill({ json: { ok: true } });
    });
    await mockApi(page, "alice", { "GET /workspace/agreements": [] });
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login\?signed_out=1/);
    expect(loggedOut).toBe(true);
  });
});

test.describe("role-aware navigation (mocked API)", () => {
  test.beforeEach(async ({ context, baseURL }) => fakeCookie(context, baseURL!));

  test("buyer admin sees buyer dashboard and New agreement", async ({ page }) => {
    await mockApi(page, "alice", { "GET /workspace/agreements": [summary()] });
    await page.goto("/dashboard");
    const nav = page.getByRole("navigation", { name: "Primary" });
    await expect(nav.getByRole("link", { name: "New agreement" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Provider workspace" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Create Agreement" })).toBeVisible();
  });

  test("reviewer has no New agreement action", async ({ page }) => {
    await mockApi(page, "rita", { "GET /workspace/agreements": [summary()] });
    await page.goto("/dashboard");
    await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Buyer dashboard" })).toBeVisible();
    await expect(page.getByRole("link", { name: "New agreement" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Create Agreement" })).toHaveCount(0);
  });

  test("provider sees only the provider workspace", async ({ page }) => {
    await mockApi(page, "maya", { "GET /workspace/agreements": [summary({ perspective: "provider" })] });
    await page.goto("/provider");
    const nav = page.getByRole("navigation", { name: "Primary" });
    await expect(nav.getByRole("link", { name: "Provider workspace" })).toBeVisible();
    await expect(nav.getByRole("link", { name: "Buyer dashboard" })).toHaveCount(0);
    await expect(page.getByText("Landing page for spring campaign").filter({ visible: true })).toBeVisible();
  });
});

test.describe("empty and error states (mocked API)", () => {
  test.beforeEach(async ({ context, baseURL }) => fakeCookie(context, baseURL!));

  test("dashboard shows an honest empty state with zero counts", async ({ page }) => {
    await mockApi(page, "alice", { "GET /workspace/agreements": [] });
    await page.goto("/dashboard");
    await expect(page.getByText("No agreements yet")).toBeVisible();
    await expect(page.getByRole("button", { name: /Completed payments/ })).toContainText("0");
  });

  test("API failure shows an error with retry", async ({ page }) => {
    let n = 0;
    await mockApi(page, "alice", {
      "GET /workspace/agreements": () => (++n === 1 ? status(500, { code: "HTTP_500", message: "Internal error" }) : [summary()]),
    });
    await page.goto("/dashboard");
    await expect(page.getByText("Something went wrong")).toBeVisible();
    await page.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByRole("link", { name: "Landing page for spring campaign" })).toBeVisible();
  });

  test("dashboard tiles count only real records and filter the list", async ({ page }) => {
    await mockApi(page, "alice", {
      "GET /workspace/agreements": [
        summary({ id: "a1", title: "Failed one", latest_run: { id: "r", status: "completed", verdict: "fail", created_at: "", finished_at: "", counts: {}, awaiting_review: 0 }, latest_completed_run: { id: "r", status: "completed", verdict: "fail", created_at: "", finished_at: "", counts: {}, awaiting_review: 0 } }),
        summary({ id: "a2", title: "Proposed one", locked: false, version_status: "proposed" }),
      ],
    });
    await page.goto("/dashboard");
    await expect(page.getByRole("button", { name: /Failed verifications/ })).toContainText("1");
    await page.getByRole("button", { name: /Agreements awaiting approval/ }).click();
    await expect(page.getByRole("link", { name: "Proposed one" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Failed one" })).toHaveCount(0);
  });
});

test("rehearsal mode is announced: new payments are SIMULATED (never shown as Sandbox)", async ({ page, context, baseURL }) => {
  await fakeCookie(context, baseURL!);
  await mockApi(page, "alice", { "GET /ready": { status: "ready", config: { payment_mode: "rehearsal" } }, "GET /workspace/agreements": [] });
  await page.goto("/dashboard");
  const banner = page.getByTestId("mode-banner");
  await expect(banner).toContainText("REHEARSAL MODE");
  await expect(banner).toContainText("SIMULATED");
  await expect(banner).not.toContainText("test money only");
});

test("sandbox mode banner says test money only", async ({ page, context, baseURL }) => {
  await fakeCookie(context, baseURL!);
  await mockApi(page, "alice", { "GET /ready": { status: "ready", config: { payment_mode: "sandbox" } }, "GET /workspace/agreements": [] });
  await page.goto("/dashboard");
  await expect(page.getByTestId("mode-banner")).toContainText("PayPal Sandbox — test money only");
});
