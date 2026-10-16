// Response shapes of the OutcomeGuard API (apps/api). Money is a decimal string + currency.

export type OrgOut = { id: string; name: string; kind: "buyer" | "merchant"; roles: string[] };

export type WorkspaceContext = {
  user_id: string;
  email: string;
  display_name: string;
  kind: "human" | "service";
  demo_account: boolean;
  organizations: OrgOut[];
};

export type Merchant = {
  id: string;
  display_name: string;
  organization_id: string;
  organization_name: string;
  environment: string;
};

export type Criterion = {
  id: string;
  position: number;
  key: string;
  title: string;
  kind: CriterionKind;
  verification_method: "automated" | "manual_review_required";
  blocking: boolean;
  source: string;
  spec: Record<string, unknown>;
};

export type CriterionKind =
  | "http_availability"
  | "mobile_viewport"
  | "contact_form"
  | "lighthouse_performance"
  | "manual_review";

export type Version = {
  id: string;
  version_number: number;
  status: "draft" | "proposed" | "locked" | "superseded" | "discarded";
  revision: number;
  supersedes_version_id: string | null;
  title: string;
  description: string;
  requirements_text: string;
  amount: string;
  currency: string;
  delivery_deadline: string;
  content_hash: string | null;
  proposed_at: string | null;
  locked_at: string | null;
  criteria: Criterion[];
  approvals: {
    id: string;
    decision: string;
    approver_user_id: string;
    approver_role: string;
    content_hash: string;
    decided_at: string;
    note: string | null;
  }[];
};

export type Agreement = {
  id: string;
  status: string;
  service_category: string;
  buyer_organization_id: string;
  merchant_account_id: string;
  current_locked_version_id: string | null;
  created_at: string;
  versions: Version[];
};

export type RunSummary = {
  id: string;
  status: string;
  verdict: Verdict | null;
  created_at: string;
  finished_at: string | null;
  counts: Record<string, number>;
  awaiting_review: number;
};

export type PaymentSummary = {
  order_id: string;
  environment: string;
  order_status: string;
  paypal_order_status: string | null;
  authorization_id: string | null;
  authorization_status: string | null;
  authorization_expires_at: string | null;
  capture_status: string | null;
  last_policy_decision: PolicyDecision | null;
  last_policy_reasons: string[];
  last_policy_evaluated_at: string | null;
  void_status: string | null;
  refunded: string | null;
};

export type AgreementSummary = {
  id: string;
  title: string;
  status: string;
  perspective: "buyer" | "provider";
  buyer_organization_name: string;
  merchant_display_name: string;
  amount: string;
  currency: string;
  version_number: number;
  version_status: string;
  content_hash: string | null;
  locked: boolean;
  created_at: string;
  latest_run: RunSummary | null;
  latest_completed_run: RunSummary | null;
  payment: PaymentSummary | null;
  open_resolution: { id: string; status: string } | null;
};

export type Verdict = "pass" | "fail" | "needs_manual_review" | "inconclusive";
export type Outcome = "pass" | "fail" | "manual_review_required" | "error" | "not_run";

export type RunResult = {
  criterion_id: string;
  criterion_key: string;
  kind: CriterionKind;
  blocking: boolean;
  outcome: Outcome;
  reason_code: string;
  measured: Record<string, unknown>;
  expected: Record<string, unknown>;
  artifact_ids: string[];
  duration_ms: number | null;
};

export type Artifact = {
  id: string;
  kind: "screenshot" | "lighthouse_json" | "http_trace" | "network_log";
  label: string;
  sha256: string;
  size_bytes: number;
  content_type: string;
};

export type FormReceipt = {
  id: string;
  sink_ref: string;
  field_names: string[];
  values_sha256: string;
  content_length: number;
  received_at: string;
};

export type Run = {
  id: string;
  status: "queued" | "running" | "completed" | "errored";
  verdict: Verdict | null;
  verified_pass: boolean;
  evidence_snapshot_sha256: string | null;
  agreement_version_id: string;
  agreement_content_hash: string;
  delivery_url: string;
  submission_hash: string;
  tool_versions: Record<string, unknown> | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  results: RunResult[];
  artifacts: Artifact[];
  form_receipts: FormReceipt[];
};

export type Explanation = {
  result_id?: string;
  criterion_key: string;
  outcome: string;
  blocking?: boolean;
  confidence?: string;
  explanation: string;
  remediation: string | null;
  reason_code?: string;
  insufficient_evidence?: boolean;
};

export type Analysis = {
  id: string;
  run_id: string;
  status: string;
  run_verdict: string | null;
  ai_call_id: string | null;
  grounded: {
    summary?: string;
    verdict?: string;
    limitations?: string[];
    explanations?: Explanation[];
    pending_human_review?: { criterion_key: string; note?: string }[];
    [k: string]: unknown;
  };
  created_at: string;
};

export type ManualReview = {
  id: string;
  criterion_id: string;
  verification_run_id: string;
  decision: "satisfied" | "not_satisfied";
  rationale: string;
  reviewer_user_id: string;
  reviewer_email: string | null;
  reviewer_role: string;
  evidence_snapshot_sha256: string;
  decided_at: string;
};

export type PolicyDecision =
  | "blocked"
  | "needs_review"
  | "eligible_for_approval"
  | "capture_approved"
  | "captured"
  | string;

export type Policy = {
  policy_decision_id: string;
  decision: PolicyDecision;
  reasons: { code: string; detail?: string }[];
  inputs_sha256: string;
  verification_run_id: string | null;
  evidence_snapshot_sha256: string | null;
  evaluated_at: string;
};

export type Settlement = {
  currency: string;
  gross_captured: string;
  paypal_fee: string | null;
  net_at_capture: string | null;
  refunded_gross: string;
  net_after_refunds_derived: string | null;
};

export type Capture = {
  id: string;
  environment: string;
  paypal_capture_id: string | null;
  amount: string;
  currency: string;
  status: "requested" | "unknown" | "pending" | "completed" | "declined" | "failed";
  paypal_status: string | null;
  settlement: Settlement;
  error: Record<string, unknown> | null;
  created_at: string;
};

export type Authorization = {
  id: string;
  paypal_authorization_id: string;
  amount: string;
  currency: string;
  status: string;
  paypal_status: string;
  payee_merchant_id: string | null;
  expires_at: string | null;
  honor_period_ends_at: string | null;
};

export type Order = {
  id: string;
  environment: string;
  simulated: boolean;
  reference: string;
  paypal_order_id: string | null;
  amount: string;
  currency: string;
  status: string;
  paypal_status: string | null;
  approval_url: string | null;
  authorization: Authorization | null;
  capture: Capture | null;
  created_at: string;
};

export type CaptureApproval = {
  id: string;
  inputs_sha256: string;
  policy_decision_id: string;
  approver_user_id: string;
  approver_email: string;
  approver_role: string;
  approved_at: string;
  consumed_by_capture_id: string | null;
};

export type DraftCriterion = {
  key: string;
  title: string;
  kind: CriterionKind;
  origin: "stated" | "suggested";
  blocking: boolean;
  source_quote: string;
  rationale: string | null;
  expected: Record<string, unknown>;
  needs_configuration: string[];
  manual_review_instructions: string | null;
  review_notes: string[];
  reasons?: string[];
};

export type Draft = {
  id: string;
  status: "proposed" | "failed" | "accepted" | "discarded";
  requirements_text: string;
  ai_call_id: string | null;
  review: {
    criteria?: DraftCriterion[];
    suggestions?: DraftCriterion[];
    rejected_criteria?: DraftCriterion[];
    ambiguities?: { topic: string; question: string }[];
    manual_review_items?: { topic?: string; reason?: string; [k: string]: unknown }[];
    unsupported_requirements?: { requirement?: string; reason?: string; [k: string]: unknown }[];
    service_summary?: string;
    deliverables?: string[];
    error?: string;
    fallback?: string;
  };
  accepted_agreement_id: string | null;
  created_at: string;
};

export type LedgerRow = {
  seq: number;
  occurred_at: string;
  event_type: string;
  category: string;
  entity_type: string;
  entity_id: string;
  agreement_id: string | null;
  agreement_title: string | null;
  agreement_version: number | null;
  actor: string;
  actor_type: "human_account" | "service_account" | "system";
  demo_account: boolean;
  provenance: "scripted" | null;
  resolution_case_id: string | null;
  verification_run_id: string | null;
  criterion_key: string | null;
  policy_decision: string | null;
  policy_reasons: string[];
  payment_status: string | null;
  payment_environment: "sandbox" | "simulated" | null;
  amount: string | null;
  currency: string | null;
  evidence_ref: string | null;
  transaction_ref: string | null;
  data: Record<string, unknown>;
  hash: string;
  prev_hash: string;
};

export type LedgerPage = { total: number; offset: number; limit: number; rows: LedgerRow[]; chain_note: string };

// ------------------------------------------------------------------ P08

export type Refund = {
  id: string;
  environment: string;
  paypal_refund_id: string | null;
  amount: string;
  currency: string;
  status: "requested" | "unknown" | "pending" | "completed" | "failed" | "cancelled" | "reconciliation_required";
  paypal_status: string | null;
  paypal_fee_returned: string | null;
  merchant_debit: string | null;
  error: Record<string, unknown> | null;
  created_at: string;
};

export type CaseEvent = {
  id: string;
  kind: string;
  side: string | null;
  author_email: string | null;
  body: string | null;
  data: Record<string, unknown>;
  created_at: string;
};

export type Proposal = {
  id: string;
  number: number;
  side: "buyer" | "provider";
  author_email: string | null;
  remedy: string;
  amount: string | null;
  currency: string;
  rationale: string;
  evidence_refs: unknown[];
  content_sha256: string;
  status: "open" | "accepted" | "rejected" | "superseded" | "withdrawn" | "executed";
  created_at: string;
  decided_at: string | null;
  approvals: { side: string; choice: string; approver_email: string | null; approver_role: string; proposal_content_sha256: string; decided_at: string }[];
};

export type EvidenceSnapshot = {
  agreement_version_id: string;
  agreement_content_hash: string | null;
  criteria: { id: string; key: string; title: string }[];
  verification_run: {
    id: string;
    verdict: string | null;
    evidence_snapshot_sha256: string;
    finished_at: string | null;
    results: { criterion_id: string; key: string; outcome: string }[];
  } | null;
  artifacts: { id: string; kind: string; label: string; sha256: string }[];
  manual_reviews: { id: string; criterion_id: string; decision: string; evidence_snapshot_sha256: string; scripted: boolean }[];
  payment: {
    paypal_order_id: string | null;
    paypal_authorization_id: string | null;
    authorization_status: string | null;
    paypal_capture_id: string | null;
    capture_status: string | null;
    amount: string | null;
    currency: string;
    environment: string | null;
  };
  limitations: string[];
};

export type ResolutionCase = {
  id: string;
  agreement_id: string;
  agreement_version_id: string;
  agreement_content_hash: string;
  payment_authorization_id: string | null;
  payment_capture_id: string | null;
  status: string;
  revision: number;
  reason_category: string;
  description: string;
  requested_remedy: string;
  requested_amount: string | null;
  currency: string;
  opened_by_email: string | null;
  your_side: "buyer" | "provider" | null;
  evidence_snapshot: EvidenceSnapshot;
  evidence_snapshot_sha256: string;
  outcome: string | null;
  closure_reason: string | null;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  proposals: Proposal[];
  events: CaseEvent[];
  financials: {
    currency: string;
    captured: string | null;
    refunded: string;
    held: string;
    remaining: string | null;
    paypal_fee_at_capture: string | null;
    refunds: Refund[];
  } | null;
};

export type CaseSummary = {
  id: string;
  status: string;
  reason_category: string;
  requested_remedy: string;
  requested_amount: string | null;
  currency: string;
  created_at: string;
  closed_at: string | null;
  outcome: string | null;
};

export type FinPolicy = {
  policy_decision_id: string;
  purpose: "refund" | "void";
  decision: "allowed" | "blocked";
  reasons: { code: string; detail?: string }[];
  inputs_sha256: string;
  figures: Record<string, number>;
  proposal_id: string | null;
  evaluated_at: string;
};

export type VoidOp = {
  id: string;
  payment_authorization_id: string;
  status: "requested" | "unknown" | "completed" | "failed";
  reason: string;
  requested_by: string;
  paypal_status: string | null;
  case_id: string | null;
  error: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};
