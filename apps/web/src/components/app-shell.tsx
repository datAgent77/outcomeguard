"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";
import { Logo } from "./logo";
import { ROLE_LABEL, SessionProvider, useSession } from "./session";
import { cx, Skeleton } from "./ui";
import { sessionRequest } from "@/lib/client/api";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <SessionProvider fallback={<ShellSkeleton />}>
      <Chrome>{children}</Chrome>
    </SessionProvider>
  );
}

function ShellSkeleton() {
  return (
    <div className="min-h-dvh" role="status" aria-label="Loading workspace">
      <div className="h-16 border-b border-ink-200 bg-white" />
      <div className="mx-auto max-w-6xl space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    </div>
  );
}

function Chrome({ children }: { children: ReactNode }) {
  const { user, isBuyer, isProvider } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const nav = [
    ...(isBuyer ? [{ href: "/dashboard", label: "Buyer dashboard" }] : []),
    ...(isBuyer && user.organizations.some((o) => o.kind === "buyer" && o.roles.includes("buyer_admin"))
      ? [{ href: "/agreements/new", label: "New agreement" }]
      : []),
    ...(isProvider ? [{ href: "/provider", label: "Provider workspace" }] : []),
    { href: "/ledger", label: "Ledger" },
  ];
  const roles = [...new Set(user.organizations.flatMap((o) => o.roles.map((r) => ROLE_LABEL[r] ?? r)))];
  const logout = async () => {
    setLeaving(true);
    await sessionRequest("logout").catch(() => null);
    router.replace("/login?signed_out=1");
  };
  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2">
        Skip to content
      </a>
      {user.demo_account && (
        <div className="bg-ink-900 px-4 py-1.5 text-center text-xs text-ink-100">
          <strong className="font-semibold text-white">Demo workspace</strong> · PayPal Sandbox only — no real money · signed in with a
          seeded demo account
        </div>
      )}
      <header className="sticky top-0 z-30 border-b border-ink-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-4 px-4 sm:px-6">
          <Link href={isBuyer ? "/dashboard" : "/provider"} className="shrink-0" aria-label="OutcomeGuard home">
            <Logo />
          </Link>
          <nav aria-label="Primary" className="hidden flex-1 items-center gap-1 md:flex">
            {nav.map((n) => (
              <NavLink key={n.href} href={n.href} active={pathname === n.href || (n.href !== "/agreements/new" && pathname.startsWith(n.href + "/"))}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto hidden items-center gap-3 md:flex">
            <div className="text-right leading-tight">
              <div className="text-sm font-medium text-ink-900">{user.display_name}</div>
              <div className="text-xs text-ink-500">
                {roles.join(" · ")}
                {user.kind === "service" && " · service account"}
              </div>
            </div>
            <button
              onClick={logout}
              disabled={leaving}
              className="min-h-9 rounded-lg border border-ink-200 px-3 text-sm font-medium text-ink-700 hover:bg-ink-50"
            >
              {leaving ? "Signing out…" : "Sign out"}
            </button>
          </div>
          <button
            className="ml-auto grid size-11 place-items-center rounded-lg text-ink-700 hover:bg-ink-100 md:hidden"
            aria-expanded={open}
            aria-controls="mobile-nav"
            aria-label="Menu"
            onClick={() => setOpen((v) => !v)}
          >
            <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="2">
              {open ? <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" /> : <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />}
            </svg>
          </button>
        </div>
        {open && (
          <nav id="mobile-nav" aria-label="Primary" className="border-t border-ink-100 bg-white px-4 pb-4 pt-2 md:hidden">
            <div className="mb-3 border-b border-ink-100 pb-3">
              <div className="text-sm font-medium">{user.display_name}</div>
              <div className="text-xs text-ink-500">{roles.join(" · ")}</div>
            </div>
            <div className="flex flex-col">
              {nav.map((n) => (
                <Link
                  key={n.href}
                  href={n.href}
                  onClick={() => setOpen(false)}
                  className={cx("flex min-h-11 items-center rounded-lg px-3 text-sm font-medium", pathname === n.href ? "bg-brand-50 text-brand-700" : "text-ink-800 hover:bg-ink-50")}
                >
                  {n.label}
                </Link>
              ))}
              <button onClick={logout} className="mt-2 flex min-h-11 items-center rounded-lg px-3 text-left text-sm font-medium text-ink-700 hover:bg-ink-50">
                Sign out
              </button>
            </div>
          </nav>
        )}
      </header>
      <main id="main" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 sm:py-8">
        {children}
      </main>
      <footer className="border-t border-ink-200 bg-white px-4 py-4 text-center text-xs text-ink-500">
        PayPal Sandbox only · AI explains evidence and never authorizes payments · Capture requires deterministic policy + human approval
      </footer>
    </div>
  );
}

function NavLink({ href, active, children }: { href: string; active: boolean; children: ReactNode }) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cx("rounded-lg px-3 py-2 text-sm font-medium transition-colors", active ? "bg-ink-100 text-ink-900" : "text-ink-600 hover:bg-ink-50 hover:text-ink-900")}
    >
      {children}
    </Link>
  );
}
