import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/bff";

// Optimistic check only: no cookie → login page. The API validates every request.
export function proxy(req: NextRequest) {
  if (!req.cookies.get(SESSION_COOKIE)) {
    const url = new URL("/login", req.url);
    url.searchParams.set("next", req.nextUrl.pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/dashboard/:path*", "/provider/:path*", "/agreements/:path*", "/ledger/:path*"],
};
