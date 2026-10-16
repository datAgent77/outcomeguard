import { NextRequest, NextResponse } from "next/server";
import { apiBase, csrfOk, CSRF_HEADER, SESSION_COOKIE } from "@/lib/bff";

// Revokes the server-side session (API) and clears the cookie.
export async function POST(req: NextRequest) {
  if (!csrfOk("POST", req.headers.get("origin"), req.headers.get("host"), req.headers.get(CSRF_HEADER))) {
    return NextResponse.json({ code: "CSRF_REJECTED", message: "cross-site request refused" }, { status: 403 });
  }
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (token) {
    try {
      await fetch(`${apiBase()}/auth/logout`, {
        method: "POST",
        headers: { authorization: `Bearer ${token}` },
        cache: "no-store",
      });
    } catch {
      // The cookie is still cleared; the API session then simply expires.
    }
  }
  const out = NextResponse.json({ ok: true });
  out.cookies.delete(SESSION_COOKIE);
  return out;
}
