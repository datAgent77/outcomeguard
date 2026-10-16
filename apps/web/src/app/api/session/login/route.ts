import { NextRequest, NextResponse } from "next/server";
import { apiBase, cookieSecure, csrfOk, CSRF_HEADER, SESSION_COOKIE, SESSION_MAX_AGE } from "@/lib/bff";

// Exchanges email + password for an API session and keeps the bearer token in an
// HttpOnly cookie. The token never reaches browser JavaScript or localStorage.
export async function POST(req: NextRequest) {
  if (!csrfOk("POST", req.headers.get("origin"), req.headers.get("host"), req.headers.get(CSRF_HEADER))) {
    return NextResponse.json({ code: "CSRF_REJECTED", message: "cross-site request refused" }, { status: 403 });
  }
  let body: { email?: unknown; password?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ code: "BAD_REQUEST", message: "invalid body" }, { status: 400 });
  }
  if (typeof body.email !== "string" || typeof body.password !== "string") {
    return NextResponse.json({ code: "BAD_REQUEST", message: "email and password required" }, { status: 400 });
  }
  let res: Response;
  try {
    res = await fetch(`${apiBase()}/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: body.email, password: body.password }),
      cache: "no-store",
    });
  } catch {
    return NextResponse.json({ code: "API_UNREACHABLE", message: "OutcomeGuard API is unreachable" }, { status: 502 });
  }
  if (!res.ok) {
    // Same message for unknown user, wrong password and lockout (no account probing).
    const status = res.status === 401 ? 401 : 502;
    return NextResponse.json(
      { code: status === 401 ? "INVALID_CREDENTIALS" : "API_ERROR", message: status === 401 ? "Email or password is incorrect, or the account is temporarily locked." : `API answered ${res.status}` },
      { status },
    );
  }
  const { token, expires_in } = (await res.json()) as { token: string; expires_in?: number };
  const out = NextResponse.json({ ok: true });
  out.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: cookieSecure(req.nextUrl.protocol),
    sameSite: "lax",
    path: "/",
    maxAge: Math.min(expires_in ?? SESSION_MAX_AGE, SESSION_MAX_AGE),
  });
  return out;
}
