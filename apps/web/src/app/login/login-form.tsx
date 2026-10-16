"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Button, Field, inputCls, Notice } from "@/components/ui";
import { sessionRequest } from "@/lib/client/api";

function safeNext(next: string | null): string | null {
  // Only same-site relative paths; never an open redirect.
  return next && next.startsWith("/") && !next.startsWith("//") ? next : null;
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const expired = params.get("expired") === "1";
  const signedOut = params.get("signed_out") === "1";

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (busy) return;
    const fd = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const res = await sessionRequest("login", { email: String(fd.get("email") ?? ""), password: String(fd.get("password") ?? "") });
      const body = (await res.json().catch(() => ({}))) as { message?: string };
      if (!res.ok) {
        setError(body.message ?? "Sign-in failed.");
        return;
      }
      const ctx = await fetch("/api/og/workspace/context", { credentials: "same-origin" }).then((r) => r.json());
      const isBuyer = (ctx.organizations ?? []).some((o: { kind: string }) => o.kind === "buyer");
      router.replace(safeNext(params.get("next")) ?? (isBuyer ? "/dashboard" : "/provider"));
    } catch {
      setError("Could not reach OutcomeGuard. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="mt-6 space-y-4" noValidate>
      {expired && <Notice tone="review">Your session expired. Please sign in again.</Notice>}
      {signedOut && <Notice tone="neutral">You have signed out.</Notice>}
      {error && <Notice tone="fail">{error}</Notice>}
      <Field label="Email" htmlFor="email">
        <input id="email" name="email" type="email" autoComplete="username" required className={inputCls} />
      </Field>
      <Field label="Password" htmlFor="password">
        <input id="password" name="password" type="password" autoComplete="current-password" required className={inputCls} />
      </Field>
      <Button type="submit" busy={busy} className="w-full">
        Sign in
      </Button>
    </form>
  );
}
