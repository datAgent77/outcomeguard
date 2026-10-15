// Server-side API access. The browser never talks to PayPal and never sees secrets;
// everything goes through the OutcomeGuard API.
export const API_URL = process.env.OUTCOMEGUARD_API_URL ?? "http://localhost:8000";

export type PublicConfig = {
  app_env: string;
  payment_mode: "sandbox" | "rehearsal";
  paypal_environment: "sandbox" | "none (simulated)";
  paypal_credentials_configured: boolean;
  paypal_webhook_configured: boolean;
  sandbox_proof_enabled: boolean;
};

export type Readiness =
  | { ok: true; config: PublicConfig }
  | { ok: false; error: string };

export async function getReadiness(): Promise<Readiness> {
  try {
    const res = await fetch(`${API_URL}/ready`, { cache: "no-store" });
    if (!res.ok) return { ok: false, error: `API answered ${res.status}` };
    const body = (await res.json()) as { config: PublicConfig };
    return { ok: true, config: body.config };
  } catch {
    return { ok: false, error: "API unreachable" };
  }
}
