import { NextRequest, NextResponse } from "next/server";
import { apiBase, csrfOk, CSRF_HEADER, isAllowedPath, SESSION_COOKIE } from "@/lib/bff";

// Same-origin proxy to the OutcomeGuard API. Attaches the bearer token from the HttpOnly
// cookie; the browser never sees it. Authorization is enforced by the API — this layer
// only restricts which paths are reachable and blocks cross-site mutations.
const FORWARD_REQUEST = ["content-type", "idempotency-key", "accept"];
const FORWARD_RESPONSE = ["content-type", "content-disposition", "x-content-sha256", "x-content-type-options"];

async function handle(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const joined = path.join("/");
  if (!isAllowedPath(joined)) {
    return NextResponse.json({ code: "NOT_FOUND", message: "not found" }, { status: 404 });
  }
  if (!csrfOk(req.method, req.headers.get("origin"), req.headers.get("host"), req.headers.get(CSRF_HEADER))) {
    return NextResponse.json({ code: "CSRF_REJECTED", message: "cross-site request refused" }, { status: 403 });
  }
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!token) {
    return NextResponse.json({ code: "SESSION_EXPIRED", message: "Please sign in." }, { status: 401 });
  }
  const headers = new Headers({ authorization: `Bearer ${token}` });
  for (const h of FORWARD_REQUEST) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const hasBody = !["GET", "HEAD"].includes(req.method);
  let res: Response;
  try {
    res = await fetch(`${apiBase()}/${joined}${req.nextUrl.search}`, {
      method: req.method,
      headers,
      body: hasBody ? await req.arrayBuffer() : undefined,
      cache: "no-store",
      signal: req.signal,
    });
  } catch {
    return NextResponse.json({ code: "API_UNREACHABLE", message: "OutcomeGuard API is unreachable" }, { status: 502 });
  }
  if (res.status === 401) {
    const out = NextResponse.json({ code: "SESSION_EXPIRED", message: "Your session has expired. Please sign in again." }, { status: 401 });
    out.cookies.delete(SESSION_COOKIE);
    return out;
  }
  const outHeaders = new Headers({ "cache-control": "no-store" });
  for (const h of FORWARD_RESPONSE) {
    const v = res.headers.get(h);
    if (v) outHeaders.set(h, v);
  }
  return new NextResponse(res.body, { status: res.status, headers: outHeaders });
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
