import { Suspense } from "react";
import { connection } from "next/server";
import { getReadiness } from "@/lib/api";

const STAGES = [
  "Agreement",
  "Criteria",
  "Evidence",
  "Verification",
  "Human approval",
  "PayPal settlement",
];

async function SystemStatus() {
  await connection();
  const r = await getReadiness();
  if (!r.ok) {
    return (
      <p role="status" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-800 dark:bg-red-950 dark:text-red-200">
        Backend not ready: {r.error}
      </p>
    );
  }
  const c = r.config;
  const rows: [string, string][] = [
    ["Payment mode", c.payment_mode === "rehearsal" ? "REHEARSAL — simulated, not PayPal" : "PayPal Sandbox"],
    ["PayPal credentials", c.paypal_credentials_configured ? "configured" : "missing"],
    ["Webhook verification", c.paypal_webhook_configured ? "configured" : "not configured"],
  ];
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-zinc-500">{k}</dt>
          <dd className="font-medium">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-4xl px-4 py-12 sm:px-6">
      <p className="text-xs font-semibold uppercase tracking-wider text-amber-700 dark:text-amber-400">
        PayPal Sandbox · no real money
      </p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">OutcomeGuard</h1>
      <p className="mt-2 text-lg text-zinc-600 dark:text-zinc-400">
        Don&apos;t just pay for a promise. Pay for proof.
      </p>

      <ol className="mt-8 flex flex-wrap gap-2 text-sm" aria-label="Settlement pipeline">
        {STAGES.map((s, i) => (
          <li key={s} className="rounded-full border border-zinc-300 px-3 py-1 dark:border-zinc-700">
            <span className="text-zinc-400">{i + 1}.</span> {s}
          </li>
        ))}
      </ol>

      <section className="mt-10 rounded-lg border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
        <h2 className="mb-4 text-sm font-semibold">System status</h2>
        <Suspense fallback={<p className="text-sm text-zinc-500">Checking backend…</p>}>
          <SystemStatus />
        </Suspense>
      </section>
    </main>
  );
}
