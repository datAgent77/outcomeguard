export function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-semibold tracking-tight text-ink-900 ${className}`}>
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
        <rect width="32" height="32" rx="8" fill="#0f1f3a" />
        <path d="M16 6.5 8.5 9.6v5.6c0 4.7 3.1 8.6 7.5 10.3 4.4-1.7 7.5-5.6 7.5-10.3V9.6L16 6.5Z" fill="none" stroke="#9db4f7" strokeWidth="1.7" />
        <path d="m12.4 16 2.6 2.6 4.8-5.2" fill="none" stroke="#ffffff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span>OutcomeGuard</span>
    </span>
  );
}
