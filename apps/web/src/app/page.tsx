import Link from "next/link";
import { Logo } from "@/components/logo";

const STAGES = [
  {
    n: "01",
    title: "Define the promise",
    body: "The buyer describes the work in plain language — price, deliverable and what success means.",
  },
  {
    n: "02",
    title: "Agree on acceptance criteria",
    body: "AI proposes measurable criteria. The buyer reviews every one, opts in to suggestions, and locks a versioned agreement with a content hash.",
  },
  {
    n: "03",
    title: "Verify delivery with evidence",
    body: "The provider submits the delivery. A real browser, Lighthouse and a controlled form endpoint measure it. Evidence is stored by content hash.",
  },
  {
    n: "04",
    title: "Resolve outstanding reviews",
    body: "What no tool can decide — like accessibility — goes to an authorized human reviewer, who attests to the exact evidence they saw.",
  },
  {
    n: "05",
    title: "Approve payment through PayPal",
    body: "A deterministic policy re-checks every condition at the moment of capture. Only then can the buyer's approval capture the PayPal authorization.",
  },
];

export default function Landing() {
  return (
    <div className="min-h-dvh bg-white">
      <header className="border-b border-ink-100">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
          <Logo />
          <nav aria-label="Site" className="flex items-center gap-2">
            <a href="#how" className="hidden rounded-lg px-3 py-2 text-sm font-medium text-ink-600 hover:text-ink-900 sm:inline-block">
              How it works
            </a>
            <Link href="/login" className="rounded-lg px-3 py-2 text-sm font-medium text-ink-700 hover:bg-ink-50">
              Sign in
            </Link>
          </nav>
        </div>
      </header>

      <main>
        <section className="mx-auto grid max-w-6xl gap-12 px-4 pb-16 pt-12 sm:px-6 lg:grid-cols-[1.1fr_1fr] lg:items-center lg:pt-20">
          <div>
            <p className="inline-flex items-center gap-2 rounded-full border border-ink-200 bg-ink-50 px-3 py-1 text-xs font-medium text-ink-600">
              <span className="size-1.5 rounded-full bg-review-700" aria-hidden />
              PayPal Sandbox build · no real money
            </p>
            <h1 className="mt-5 text-4xl font-semibold tracking-tight text-ink-950 sm:text-5xl">
              Don&apos;t just pay for a promise. <span className="text-brand-600">Pay for proof.</span>
            </h1>
            <p className="mt-5 max-w-xl text-lg leading-relaxed text-ink-600">
              Define what success looks like. Verify the delivery. Approve payment with confidence.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                href="/login?next=/agreements/new"
                className="inline-flex min-h-12 items-center rounded-lg bg-brand-600 px-5 text-sm font-semibold text-white shadow-sm hover:bg-brand-700"
              >
                Start an Agreement
              </Link>
              <a
                href="#how"
                className="inline-flex min-h-12 items-center rounded-lg border border-ink-200 px-5 text-sm font-semibold text-ink-800 hover:bg-ink-50"
              >
                See How It Works
              </a>
            </div>
          </div>
          <DecisionIllustration />
        </section>

        <section className="border-t border-ink-100 py-14" aria-labelledby="proof-h">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <h2 id="proof-h" className="text-2xl font-semibold tracking-tight text-ink-950">
              Proven end-to-end on PayPal Sandbox
            </h2>
            <p className="mt-2 max-w-3xl text-ink-600">
              Recorded runs, not a mock-up: every ID below was read back from PayPal&apos;s Sandbox API (test money). Full audit trails are in the
              repository&apos;s integration proof.
            </p>
            <div className="mt-8 grid gap-4 md:grid-cols-3">
              <ProofCard
                title="Proof-governed capture"
                date="Oct 16"
                lines={[
                  ["Authorization", "7HU39463K8073954H"],
                  ["Capture · $500.00", "70F43933LT884351X"],
                ]}
                note="Captured only after verification, a recorded review decision, a fresh policy check and a separate buyer approval."
              />
              <ProofCard
                title="Two-party refund"
                date="Oct 16"
                lines={[
                  ["Capture · $500.00", "95Y56941P3956690M"],
                  ["Refund · $100.00", "2DF34389JE394200F"],
                ]}
                note="Refunded only after buyer and provider approved the same proposal; $400.00 remained refundable."
              />
              <ProofCard
                title="Cancelled before capture"
                date="Oct 16"
                lines={[
                  ["Authorization · $500.00", "32284282MB448210R"],
                  ["PayPal status", "VOIDED"],
                ]}
                note="A void is not a refund: the buyer was never charged and the provider received nothing."
              />
            </div>
            <p className="mt-4 text-xs text-ink-500">
              Source:{" "}
              <a className="underline" href="https://github.com/datAgent77/outcomeguard/blob/main/docs/INTEGRATION_PROOF.md">
                docs/INTEGRATION_PROOF.md
              </a>
              . Sandbox only — production PayPal is not used.
            </p>
          </div>
        </section>

        <section id="how" className="border-t border-ink-100 bg-ink-50 py-16">
          <div className="mx-auto max-w-6xl px-4 sm:px-6">
            <h2 className="text-2xl font-semibold tracking-tight text-ink-950">How it works</h2>
            <p className="mt-2 max-w-2xl text-ink-600">Five stages. Each one leaves a record you can inspect later.</p>
            <ol className="mt-10 grid gap-4 md:grid-cols-5">
              {STAGES.map((s) => (
                <li key={s.n} className="rounded-xl border border-ink-200 bg-white p-5">
                  <div className="font-mono text-xs font-semibold text-brand-600">{s.n}</div>
                  <h3 className="mt-2 font-semibold text-ink-900">{s.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-ink-600">{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="mx-auto grid max-w-6xl gap-6 px-4 py-16 sm:px-6 md:grid-cols-3">
          <Principle title="AI explains. It never authorizes.">
            AI drafts criteria for the buyer to review and explains measured evidence in plain language. It cannot lock an
            agreement, decide a review, approve a capture or move money. Its explanations are labeled as interpretations of
            the deterministic results.
          </Principle>
          <Principle title="Authorization and capture are separate events.">
            When the buyer approves at PayPal, the payment is <em>authorized</em>, not captured, and an authorization can
            expire. Capture happens later, only after verification, review and a fresh policy check. OutcomeGuard is not an
            escrow service and makes no guarantee about funds.
          </Principle>
          <Principle title="Every decision has a trail.">
            Agreement versions are hashed, reviews are bound to the evidence snapshot they saw, and capture approvals are bound
            to the exact policy inputs. Events go to a hash-chained audit log that makes out-of-band edits detectable.
          </Principle>
        </section>
      </main>

      <footer className="border-t border-ink-100 py-8 text-center text-xs text-ink-500">
        OutcomeGuard · PayPal AI Hackathon build · Sandbox only — production PayPal is not available.
      </footer>
    </div>
  );
}

function ProofCard({ title, date, lines, note }: { title: string; date: string; lines: [string, string][]; note: string }) {
  return (
    <div className="rounded-xl border border-ink-200 p-5">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold text-ink-900">{title}</h3>
        <span className="shrink-0 whitespace-nowrap rounded-full bg-pass-50 px-2 py-0.5 text-[11px] font-medium text-pass-700 ring-1 ring-pass-100">Sandbox · {date}</span>
      </div>
      <dl className="mt-3 space-y-1.5 text-sm">
        {lines.map(([k, v]) => (
          <div key={k} className="flex flex-wrap justify-between gap-2">
            <dt className="text-ink-600">{k}</dt>
            <dd className="font-mono text-xs text-ink-900">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-sm text-ink-600">{note}</p>
    </div>
  );
}

function Principle({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-ink-200 p-6">
      <h3 className="font-semibold text-ink-900">{title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-ink-600">{children}</p>
    </div>
  );
}

/** Schematic illustration of a report — labeled as such; not live data. */
function DecisionIllustration() {
  const rows: [string, string, string][] = [
    ["Page reachable", "PASS", "text-pass-700 bg-pass-50"],
    ["Fits a 390 px phone screen", "PASS", "text-pass-700 bg-pass-50"],
    ["Contact form submits", "FAIL", "text-fail-700 bg-fail-50"],
    ["Lighthouse performance ≥ 90", "PASS", "text-pass-700 bg-pass-50"],
    ["Accessible (human review)", "REVIEW", "text-review-800 bg-review-50"],
  ];
  return (
    <figure
      className="rounded-2xl border border-ink-200 bg-white p-5 shadow-[0_20px_50px_-20px_rgb(15_31_58/0.25)]"
      aria-label="Illustration of a verification report"
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">Verification report</span>
        <span className="rounded-full bg-ink-100 px-2 py-0.5 text-[11px] font-medium text-ink-500">Illustration</span>
      </div>
      <ul className="mt-4 divide-y divide-ink-100 rounded-lg border border-ink-100">
        {rows.map(([label, out, cls]) => (
          <li key={label} className="flex items-center justify-between gap-3 px-3 py-2.5 text-sm">
            <span className="text-ink-700">{label}</span>
            <span className={`rounded px-2 py-0.5 font-mono text-[11px] font-semibold ${cls}`}>{out}</span>
          </li>
        ))}
      </ul>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-fail-100 bg-fail-50 p-3">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-fail-700">Payment</div>
          <div className="mt-1 text-sm font-semibold text-ink-900">Capture blocked</div>
          <div className="mt-0.5 text-xs text-ink-600">Blocking criterion failed · review pending</div>
        </div>
        <div className="rounded-lg border border-ink-200 p-3">
          <div className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">PayPal</div>
          <div className="mt-1 text-sm font-semibold text-ink-900">Authorized, not captured</div>
          <div className="mt-0.5 text-xs text-ink-600">Authorization can expire</div>
        </div>
      </div>
      <figcaption className="mt-3 text-xs text-ink-500">Schematic example. Real reports show measured values and stored evidence.</figcaption>
    </figure>
  );
}
