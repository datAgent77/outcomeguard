import type { LedgerRow } from "@/lib/types";
import type { Tone } from "@/lib/status";

/** Who acted, stated only as far as the record supports it. A demo account is never
 *  presented as a human decision; a persisted scripted marker is shown as scripted. */
export function provenance(r: LedgerRow): { label: string; tone: Tone } {
  if (r.provenance === "scripted") return { label: "Scripted demo action", tone: "review" };
  if (r.actor_type === "system") return { label: "System", tone: "neutral" };
  if (r.actor_type === "service_account") return { label: "Service account", tone: "info" };
  if (r.demo_account) return { label: "Demo account", tone: "neutral" };
  return { label: "Human account", tone: "info" };
}

export function actorName(actor: string): string {
  return actor.startsWith("user:") ? actor.slice(5) : actor;
}

const EVENT: Record<string, string> = {
  "agreement.created": "Agreement drafted",
  "agreement.version.proposed": "Version proposed",
  "agreement.version.approved_and_locked": "Agreement approved and locked",
  "agreement.version.withdrawn": "Proposal withdrawn",
  "ai.agreement_draft.proposed": "AI drafted criteria",
  "ai.agreement_draft.accepted": "Buyer accepted reviewed criteria",
  "ai.agreement_draft.failed": "AI drafting failed",
  "evidence.submitted": "Evidence submitted",
  "verification.completed": "Verification completed",
  "manual_review.satisfied": "Manual review: satisfied",
  "manual_review.not_satisfied": "Manual review: not satisfied",
  "capture.approved": "Capture approved by buyer",
  "payment.order.requested": "PayPal order requested",
  "payment.order.created": "PayPal order created",
  "payment.order.approved": "Buyer approved at PayPal",
  "payment.authorization.created": "Payment authorized",
  "payment.capture.requested": "Capture requested",
  "payment.capture.unknown": "Capture outcome unknown",
  "payment.capture.completed": "Payment captured",
  "payment.capture.failed": "Capture rejected",
  "payment.refund.requested": "Refund requested",
  "payment.refund.completed": "Refund completed",
  "payment.refund.pending": "Refund pending at PayPal",
  "payment.refund.unknown": "Refund outcome unknown",
  "payment.refund.failed": "Refund rejected by PayPal",
  "payment.refund.reconciliation_required": "Refund needs operator reconciliation",
  "payment.void.requested": "Authorization cancellation requested",
  "payment.void.completed": "Authorization cancelled (voided)",
  "payment.void.unknown": "Cancellation outcome unknown",
  "payment.void.failed": "Cancellation refused by PayPal",
  "resolution.opened": "Resolution case opened",
  "resolution.message": "Resolution message",
  "resolution.evidence_linked": "Evidence linked to case",
  "resolution.proposal_submitted": "Proposal submitted",
  "resolution.proposal_approved": "Proposal approved",
  "resolution.proposal_rejected": "Proposal rejected",
  "resolution.proposal_withdrawn": "Proposal withdrawn",
  "resolution.refund_requested": "Case: refund sent to PayPal",
  "resolution.refund_completed": "Case: refund completed",
  "resolution.refund_unknown": "Case: refund outcome unknown",
  "resolution.refund_failed": "Case: refund rejected",
  "resolution.void_requested": "Case: cancellation sent",
  "resolution.void_completed": "Case: authorization cancelled",
  "resolution.closed": "Resolution case closed",
};

export function eventLabel(t: string): string {
  if (EVENT[t]) return EVENT[t];
  const m = /^policy\.(\w+)\.(\w+)$/.exec(t);
  if (m) return `Policy (${m[1]}): ${m[2].replaceAll("_", " ")}`;
  if (t.startsWith("ai.evidence_analysis.")) return `AI evidence explanation (${t.split(".").pop()!.replaceAll("_", " ")})`;
  return t;
}

export function eventTone(r: LedgerRow): Tone {
  if (r.event_type.endsWith(".blocked") || r.event_type.endsWith(".failed") || r.event_type === "manual_review.not_satisfied") return "fail";
  if (r.event_type.endsWith(".unknown") || r.event_type.endsWith("_unknown") || r.event_type.endsWith(".needs_review") || r.event_type.endsWith("reconciliation_required") || r.event_type === "resolution.opened") return "review";
  if (r.event_type.endsWith(".rejected") || r.event_type.endsWith("_rejected") || r.event_type === "policy.refund.blocked" || r.event_type === "policy.void.blocked") return "fail";
  if (["payment.refund.completed", "payment.void.completed", "resolution.proposal_approved", "policy.refund.allowed", "policy.void.allowed"].includes(r.event_type)) return "pass";
  if (["payment.capture.completed", "agreement.version.approved_and_locked", "manual_review.satisfied", "capture.approved"].includes(r.event_type))
    return "pass";
  if (r.event_type === "verification.completed") {
    const v = String(r.data.verdict ?? "");
    return v === "pass" ? "pass" : v === "needs_manual_review" ? "review" : v ? "fail" : "neutral";
  }
  return "neutral";
}

/** Event label, marked when the payment object is a rehearsal (simulated) one. */
export function eventTitle(r: LedgerRow): string {
  const base = eventLabel(r.event_type);
  return r.payment_environment === "simulated" ? `${base} (simulated)` : base;
}
