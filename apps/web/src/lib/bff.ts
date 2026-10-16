// Backend-for-frontend rules shared by the session and proxy route handlers. Pure
// functions, unit-tested in tests/unit/bff.test.ts.

export const SESSION_COOKIE = "og_session";
export const SESSION_MAX_AGE = 8 * 3600; // matches the API's server-side session lifetime
export const CSRF_HEADER = "x-og-csrf";

// Only these API paths are reachable through the browser proxy. Operator/developer
// endpoints (/dev/*), the webhook receiver and the form sink are never proxied.
const ALLOWED = [
  /^workspace\/(context|merchants|agreements|ledger)(\/[A-Za-z0-9-]+)?$/,
  /^agreements(\/.*)?$/,
  /^verification\/(runs|artifacts)\/[A-Za-z0-9-]+(\/[a-z-]+)?$/,
  /^payments\/(orders|authorizations|captures)\/[A-Za-z0-9-]+(\/[a-z-]+)?$/,
  /^(me|ready|config\/public)$/,
  // P08 resolution workflow
  /^resolutions\/[A-Za-z0-9-]+(\/(messages|evidence|proposals|policy|refunds|close))?$/,
  /^resolutions\/[A-Za-z0-9-]+\/proposals\/[A-Za-z0-9-]+\/(approve|reject|withdraw)$/,
  /^(refunds|voids)\/[A-Za-z0-9-]+\/reconcile$/,
];

export function isAllowedPath(path: string): boolean {
  if (path.includes("..") || path.includes("//")) return false;
  return ALLOWED.some((re) => re.test(path));
}

const SAFE = new Set(["GET", "HEAD", "OPTIONS"]);

/** CSRF defence for state-changing requests: the session cookie is SameSite=Lax, and every
 *  mutation must come from our own origin (Origin header) and carry a custom header that a
 *  cross-site form cannot set without a CORS preflight. */
export function csrfOk(method: string, origin: string | null, host: string | null, header: string | null): boolean {
  if (SAFE.has(method.toUpperCase())) return true;
  if (header !== "1" || !origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export function apiBase(): string {
  return (process.env.OUTCOMEGUARD_API_URL ?? "http://localhost:8000").replace(/\/$/, "");
}

/** Secure cookies everywhere except plain-http local development. */
export function cookieSecure(protocol: string): boolean {
  return protocol === "https:" || process.env.NODE_ENV === "production";
}
