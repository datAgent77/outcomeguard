import type { Metadata } from "next";
import { Suspense } from "react";
import Link from "next/link";
import { Logo } from "@/components/logo";
import { LoginForm } from "./login-form";

export const metadata: Metadata = { title: "Sign in" };

export default function LoginPage() {
  return (
    <div className="flex min-h-dvh flex-col items-center justify-center px-4 py-10">
      <Link href="/" className="mb-8" aria-label="OutcomeGuard home">
        <Logo />
      </Link>
      <div className="w-full max-w-sm rounded-2xl border border-ink-200 bg-white p-6 shadow-sm sm:p-8">
        <h1 className="text-xl font-semibold tracking-tight">Sign in</h1>
        <p className="mt-1 text-sm text-ink-500">Use the account your organization gave you.</p>
        <Suspense fallback={<div className="mt-6 h-48" />}>
          <LoginForm />
        </Suspense>
      </div>
      <p className="mt-6 max-w-sm text-center text-xs text-ink-500">
        PayPal Sandbox build — no real money moves. Demo accounts are created by an operator; there is no self sign-up.
      </p>
    </div>
  );
}
