// Unit tests of the BFF rules (pure functions; no browser, no API).
import { expect, test } from "@playwright/test";
import { csrfOk, isAllowedPath } from "../../src/lib/bff";

test("only product API paths are proxied", () => {
  for (const ok of ["workspace/context", "workspace/ledger", "workspace/agreements/abc-1", "agreements", "agreements/x/versions/1/approve", "verification/runs/r1/manual-reviews", "verification/artifacts/a1", "payments/authorizations/a/captures", "payments/captures/c/reconcile"])
    expect(isAllowedPath(ok), ok).toBe(true);
  for (const bad of ["dev/seed", "dev/payments/x/capture", "webhooks/paypal", "sinks/default", "rehearsal/approve/x", "agreements/../dev/seed", "workspace//context", "auth/login"])
    expect(isAllowedPath(bad), bad).toBe(false);
});

test("mutations need same-origin Origin and the custom header", () => {
  expect(csrfOk("GET", null, "app.test", null)).toBe(true);
  expect(csrfOk("POST", "https://app.test", "app.test", "1")).toBe(true);
  expect(csrfOk("POST", "https://evil.test", "app.test", "1")).toBe(false);
  expect(csrfOk("POST", "https://app.test", "app.test", null)).toBe(false);
  expect(csrfOk("POST", null, "app.test", "1")).toBe(false);
  expect(csrfOk("PATCH", "not a url", "app.test", "1")).toBe(false);
});
