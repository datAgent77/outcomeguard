// Presentation of backend states. Labels and explanations only — no decision logic.
// Agreement status, verification verdict and payment state are separate dimensions and
// are never merged into one badge.

export type Tone = "neutral" | "info" | "pass" | "review" | "fail";

export const VERDICT: Record<string, { label: string; tone: Tone; help: string }> = {
  pass: { label: "Verified", tone: "pass", help: "Every blocking criterion passed its deterministic check." },
  fail: { label: "Failed", tone: "fail", help: "At least one blocking criterion failed." },
  needs_manual_review: {
    label: "Needs human review",
    tone: "review",
    help: "Automated checks are done; a blocking criterion can only be decided by an authorized person.",
  },
  inconclusive: {
    label: "Inconclusive",
    tone: "fail",
    help: "A check could not be performed (tool or network error). Nothing was proven either way.",
  },
};

export const RUN_STATUS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: "Queued", tone: "info" },
  running: { label: "Running", tone: "info" },
  completed: { label: "Completed", tone: "neutral" },
  errored: { label: "Errored", tone: "fail" },
};

export const OUTCOME: Record<string, { label: string; tone: Tone }> = {
  pass: { label: "PASS", tone: "pass" },
  fail: { label: "FAIL", tone: "fail" },
  manual_review_required: { label: "MANUAL REVIEW", tone: "review" },
  error: { label: "INCONCLUSIVE", tone: "fail" },
  not_run: { label: "NOT RUN", tone: "neutral" },
};

export const VERSION_STATUS: Record<string, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "neutral" },
  proposed: { label: "Proposed — awaiting approval", tone: "review" },
  locked: { label: "Locked", tone: "pass" },
  superseded: { label: "Superseded", tone: "neutral" },
  discarded: { label: "Discarded", tone: "neutral" },
};

export const POLICY: Record<string, { label: string; tone: Tone }> = {
  blocked: { label: "Capture blocked", tone: "fail" },
  needs_review: { label: "Waiting for human review", tone: "review" },
  eligible_for_approval: { label: "Eligible for buyer approval", tone: "pass" },
  capture_approved: { label: "Capture approved", tone: "pass" },
  captured: { label: "Captured", tone: "pass" },
};

export const ORDER_STATUS: Record<string, { label: string; tone: Tone }> = {
  created: { label: "Awaiting buyer approval at PayPal", tone: "review" },
  approved: { label: "Approved by buyer at PayPal", tone: "info" },
  authorized: { label: "Authorized", tone: "info" },
  cancelled: { label: "Cancelled at PayPal", tone: "neutral" },
  failed: { label: "Failed", tone: "fail" },
};

export const AUTH_STATUS: Record<string, { label: string; tone: Tone }> = {
  authorized: { label: "Authorized — not captured", tone: "info" },
  pending: { label: "Pending at PayPal", tone: "review" },
  capture_in_progress: { label: "Capture in progress", tone: "review" },
  captured: { label: "Captured", tone: "pass" },
  denied: { label: "Denied", tone: "fail" },
  void_in_progress: { label: "Cancellation in progress", tone: "review" },
  voided: { label: "Cancelled (voided) — no money moved", tone: "neutral" },
  expired: { label: "Expired", tone: "fail" },
};

export const CAPTURE_STATUS: Record<string, { label: string; tone: Tone }> = {
  requested: { label: "Requested", tone: "info" },
  unknown: { label: "Outcome unknown — reconcile", tone: "review" },
  pending: { label: "Pending at PayPal", tone: "review" },
  completed: { label: "Captured", tone: "pass" },
  declined: { label: "Declined by PayPal", tone: "fail" },
  failed: { label: "Rejected by PayPal — no money moved", tone: "fail" },
};

// Plain-English reading of P06 reason codes (domain/policy.py). `detail` from the API is
// shown alongside, e.g. the criterion key.
export const REASON: Record<string, string> = {
  PAYMENT_NOT_BOUND_TO_AGREEMENT: "This payment is not tied to an agreement version.",
  AGREEMENT_NOT_LOCKED: "The agreement version is not locked.",
  AGREEMENT_SUPERSEDED: "The agreement changed: a newer version is now locked.",
  VERSION_MISMATCH: "The payment was created for a different agreement version.",
  AMOUNT_MISMATCH: "The payment amount does not match the agreement.",
  MERCHANT_MISMATCH: "The payee does not match the agreement's provider.",
  AUTH_NOT_CAPTURABLE: "The PayPal authorization can no longer be captured.",
  AUTH_EXPIRED: "The PayPal authorization has expired (or is within the 1-hour safety margin).",
  AUTH_EXPIRY_UNKNOWN: "PayPal did not report when the authorization expires.",
  ALREADY_CAPTURED: "This payment has already been captured.",
  CAPTURE_IN_PROGRESS: "A capture is already in progress — reconcile it before anything else.",
  NO_VERIFICATION_RUN: "No delivery has been verified yet.",
  STALE_EVIDENCE: "New evidence was submitted after the last completed verification.",
  NO_COMPLETED_RUN: "The latest verification has not completed.",
  RUN_VERSION_MISMATCH: "The verification checked a different agreement version.",
  CRITERION_NOT_EVALUATED: "A blocking criterion was not evaluated.",
  BLOCKING_CRITERION_FAILED: "A blocking criterion failed verification.",
  VERIFICATION_INCONCLUSIVE: "A blocking check could not be performed.",
  MANUAL_REVIEW_PENDING: "A blocking criterion is waiting for an authorized human review.",
  MANUAL_REVIEW_REJECTED: "A reviewer recorded that a blocking criterion is not satisfied.",
  NO_BLOCKING_CRITERIA: "Nothing binding was verified (no blocking criteria).",
  PAYPAL_CAPTURE_COMPLETED: "PayPal reported the capture as completed.",
  // P08 refund / void policy
  RESOLUTION_NOT_FOUND: "There is no resolution case for this refund.",
  RESOLUTION_NOT_AGREED: "Both parties have not agreed to this proposal yet.",
  APPROVAL_MISSING: "An approval is missing from one side.",
  APPROVAL_STALE: "An approval no longer matches the proposal (it changed or was not valid).",
  REFUND_AMOUNT_INVALID: "The refund amount or remedy is not valid.",
  REFUND_EXCEEDS_REMAINING: "The refund is larger than what remains refundable.",
  CAPTURE_NOT_COMPLETED: "The payment has not been captured, so there is nothing to refund.",
  CAPTURE_NOT_REFUNDABLE: "This capture cannot be refunded.",
  CURRENCY_MISMATCH: "The currency does not match the captured payment.",
  REFUND_ALREADY_COMPLETED: "This proposal has already been refunded.",
  REFUND_IN_PROGRESS: "A refund for this proposal is already in progress.",
  REFUND_OUTCOME_UNKNOWN: "An earlier refund's outcome is unknown — reconcile it first. Its amount stays reserved.",
  AUTHORIZATION_NOT_VOIDABLE: "The authorization cannot be cancelled in its current state.",
  EVIDENCE_REFERENCE_INVALID: "An evidence reference does not belong to this agreement.",
  AGREEMENT_MISMATCH: "The payment does not belong to this agreement or case.",
  ACTOR_NOT_AUTHORIZED: "Your role does not allow this financial action.",
  VOID_IN_PROGRESS: "A cancellation is already in progress — reconcile it first.",
  VOID_ALREADY_COMPLETED: "The authorization has already been cancelled.",
  PROVIDER_CONSENT_REQUIRED: "The delivery is verified: cancelling now needs the provider's agreement through a resolution case.",
};

export const CASE_STATUS: Record<string, { label: string; tone: Tone; help: string }> = {
  open: { label: "Open", tone: "review", help: "Waiting for the provider to respond." },
  under_review: { label: "Under review", tone: "review", help: "Both parties are discussing the issue." },
  proposed: { label: "Proposal pending", tone: "review", help: "A proposal is waiting for the other party." },
  agreed: { label: "Agreed", tone: "info", help: "Both parties approved a remedy." },
  executing: { label: "Executing", tone: "info", help: "The agreed money operation was sent to PayPal." },
  action_required: { label: "Action required", tone: "fail", help: "The money operation failed or its outcome is unknown. Reconcile or retry." },
  resolved: { label: "Resolved", tone: "pass", help: "The agreed remedy was carried out." },
  closed_no_action: { label: "Closed — no action", tone: "neutral", help: "Closed by the buyer without a remedy." },
  rejected: { label: "Claim rejected", tone: "neutral", help: "The buyer accepted the provider's rejection." },
  cancelled: { label: "Withdrawn", tone: "neutral", help: "The buyer withdrew the case." },
};

export const REMEDY: Record<string, string> = {
  rework: "Correction / rework",
  new_verification: "New verification run",
  partial_refund: "Partial refund",
  full_refund: "Full refund (remaining balance)",
  cancel_authorization: "Cancel the uncaptured authorization",
  other: "Other agreed resolution (no money moved by OutcomeGuard)",
  no_remedy: "No remedy",
};

export const REASON_CATEGORY: Record<string, string> = {
  criterion_not_met: "An acceptance criterion is not met",
  defect_after_acceptance: "A defect appeared after acceptance",
  incomplete_delivery: "Delivery is incomplete",
  late_delivery: "Delivery was late",
  cancel_before_capture: "Cancel before payment is captured",
  other: "Other",
};

export const REFUND_STATUS: Record<string, { label: string; tone: Tone }> = {
  requested: { label: "Sent to PayPal", tone: "info" },
  pending: { label: "Pending at PayPal", tone: "review" },
  completed: { label: "Refunded", tone: "pass" },
  failed: { label: "Rejected by PayPal — no money moved", tone: "fail" },
  unknown: { label: "Outcome unknown — reconcile", tone: "review" },
  reconciliation_required: { label: "Operator reconciliation required", tone: "fail" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export const VOID_STATUS: Record<string, { label: string; tone: Tone }> = {
  requested: { label: "Cancellation sent", tone: "info" },
  unknown: { label: "Cancellation outcome unknown — reconcile", tone: "review" },
  completed: { label: "Authorization cancelled", tone: "neutral" },
  failed: { label: "PayPal refused the cancellation", tone: "fail" },
};

// Errors returned by mutation endpoints (P06 governance + P03 agreements).
export const ERROR_HELP: Record<string, string> = {
  POLICY_NOT_SATISFIED: "The deterministic policy does not allow this right now.",
  APPROVAL_STALE: "The facts changed since this capture approval was given. Review the current state and approve again.",
  APPROVAL_ALREADY_USED: "This capture approval has already been used.",
  STALE_EVIDENCE: "The evidence changed while you were reviewing. Reload and review the current evidence.",
  CONTENT_CHANGED: "The agreement changed since you opened it. Reload and review again.",
  CAPTURE_IN_PROGRESS: "A capture is already in progress for this authorization.",
  ALREADY_CAPTURED: "This payment has already been captured.",
  OUTCOME_UNKNOWN: "PayPal's answer was lost. The capture may or may not have happened — do not retry; reconcile.",
  FORBIDDEN: "Your role does not allow this action.",
  SESSION_EXPIRED: "Your session has expired.",
  POLICY_INPUTS_CHANGED: "The facts changed since you loaded the policy. Refresh and review again.",
  STALE_EVIDENCE_VIEW: "The evidence changed while this review was open. Refresh and review the current evidence.",
  ALREADY_DECIDED: "This criterion already has a recorded decision for this verification run.",
  RUN_NOT_COMPLETED: "The verification run has not completed yet.",
  APPROVER_NO_LONGER_AUTHORIZED: "The approver no longer holds a capture role. A new approval is required.",
  REQUEST_IN_PROGRESS: "The same request is still being processed.",
  AGREEMENT_NOT_LOCKED: "Lock the agreement before starting a payment.",
  PAYPAL_REJECTED: "PayPal rejected the request.",
  PAYPAL_NOT_CONFIGURED: "PayPal Sandbox credentials are not configured on the server.",
  API_UNREACHABLE: "The OutcomeGuard API is unreachable.",
  CASE_ALREADY_OPEN: "This payment already has an open resolution case.",
  CASE_CHANGED: "The case changed since you loaded it. Reload and try again.",
  CASE_CLOSED: "This case is closed.",
  INVALID_TRANSITION: "That action is not possible in the case's current state.",
  IDEMPOTENCY_KEY_REUSED: "This request key was already used for a different operation.",
};

// AI gateway outcomes (P05) shown when drafting fails.
export const AI_OUTCOME: Record<string, string> = {
  budget_exceeded: "The AI spending budget is exhausted. Nothing was generated and nothing was charged beyond the budget.",
  pricing_unavailable: "The configured model has no known price, so the call was refused (fail-closed).",
  invalid_output: "The model returned output that failed validation. Nothing was used.",
  refusal: "The model declined to answer.",
  timeout: "The AI provider timed out.",
  unavailable: "The AI provider is temporarily unavailable.",
  rate_limited: "The AI provider is rate-limiting requests. Try again shortly.",
  not_configured: "No AI provider is configured on the server.",
  error: "The AI provider returned an error.",
};

export function reasonText(code: string): string {
  return REASON[code] ?? code;
}

export const KIND_LABEL: Record<string, string> = {
  http_availability: "HTTP availability",
  mobile_viewport: "Mobile viewport",
  contact_form: "Contact form",
  lighthouse_performance: "Lighthouse performance",
  manual_review: "Manual review",
};

export const KIND_PLAIN: Record<string, string> = {
  http_availability: "We request the delivered page and check it answers with an expected HTTP status.",
  mobile_viewport: "A real Chromium browser opens the page at a phone-sized viewport and checks nothing overflows sideways.",
  contact_form:
    "A real browser fills in the contact form with test values and submits it to an OutcomeGuard test endpoint; we check the request arrived and the success message appeared.",
  lighthouse_performance:
    "Google Lighthouse measures page performance (simulated mobile throttling). Close to the threshold, it re-runs and uses the median.",
  manual_review:
    "No automated tool can decide this. An authorized person on the buyer's side reviews the delivery and records a decision.",
};
