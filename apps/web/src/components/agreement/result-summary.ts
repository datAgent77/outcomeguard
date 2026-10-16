import type { RunResult } from "@/lib/types";

const num = (v: unknown) => (typeof v === "number" ? v : undefined);

/** One plain-English sentence per result, built only from the stored measurement. */
export function summarize(r: RunResult): string {
  const m = r.measured as Record<string, unknown>;
  const e = r.expected as Record<string, unknown>;
  if (r.outcome === "manual_review_required") return "No tool can decide this. An authorized person must review the delivery.";
  if (r.outcome === "error" || r.outcome === "not_run") return `The check could not be performed (${r.reason_code}). Nothing was proven either way.`;
  switch (r.kind) {
    case "http_availability": {
      const chain = (m.chain as { status?: number }[] | undefined) ?? [];
      const last = chain.at(-1)?.status;
      const ms = num(m.elapsed_ms);
      return last ? `The page answered HTTP ${last}${ms != null ? ` in ${ms} ms` : ""} (expected ${((e.expected_status as number[]) ?? [200]).join(" or ")}).` : `No HTTP answer (${r.reason_code}).`;
    }
    case "mobile_viewport": {
      const sw = num(m.scroll_width);
      const vw = num(m.viewport_width);
      if (sw != null && vw != null) return sw <= vw ? `On a ${vw}px-wide phone screen the page is ${sw}px wide — no sideways scrolling.` : `On a ${vw}px-wide phone screen the page is ${sw}px wide — it scrolls sideways.`;
      return `Mobile layout: ${r.reason_code}.`;
    }
    case "contact_form": {
      const receipts = num(m.sink_receipts_in_db) ?? 0;
      const seen = m.success_text_seen === true;
      const text = String(e.success_text ?? "the success message");
      if (m.form_found === false) return "The contact form was not found on the page.";
      if (r.outcome === "pass") return `A real browser filled in and submitted the form; the test endpoint received ${receipts} submission${receipts === 1 ? "" : "s"} and “${text}” appeared.`;
      if (!receipts) return `The form was submitted, but no submission reached the test endpoint${seen ? "" : ` and “${text}” never appeared`}.`;
      return `The submission reached the test endpoint, but “${text}” never appeared.`;
    }
    case "lighthouse_performance": {
      const scores = (m.scores as number[] | undefined) ?? [];
      const min = num(e.min_score);
      const median = scores.length ? [...scores].sort((a, b) => a - b)[Math.floor(scores.length / 2)] : undefined;
      return median != null ? `Lighthouse performance score ${median}${scores.length > 1 ? ` (median of ${scores.length} runs)` : ""} — minimum required ${min ?? "?"}.` : `Lighthouse: ${r.reason_code}.`;
    }
    default:
      return r.reason_code;
  }
}

export const TOOL: Record<string, string> = {
  http_availability: "HTTP request",
  mobile_viewport: "Real Chromium browser",
  contact_form: "Real Chromium browser + test endpoint",
  lighthouse_performance: "Google Lighthouse",
  manual_review: "Human reviewer",
};
