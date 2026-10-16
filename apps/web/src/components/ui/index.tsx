"use client";

import { useEffect, useId, useRef, useState, type ReactNode, type ButtonHTMLAttributes } from "react";
import type { Tone } from "@/lib/status";
import { shortHash } from "@/lib/format";

export function cx(...c: (string | false | null | undefined)[]): string {
  return c.filter(Boolean).join(" ");
}

// ------------------------------------------------------------------ Button

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  busy?: boolean;
};

export function Button({ variant = "primary", size = "md", busy, disabled, className, children, ...rest }: BtnProps) {
  return (
    <button
      {...rest}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-55",
        size === "sm" ? "min-h-9 px-3 text-sm" : "min-h-11 px-4 text-sm",
        variant === "primary" && "bg-brand-600 text-white shadow-sm hover:bg-brand-700",
        variant === "secondary" && "border border-ink-200 bg-white text-ink-900 shadow-sm hover:bg-ink-50",
        variant === "ghost" && "text-ink-700 hover:bg-ink-100",
        variant === "danger" && "bg-fail-700 text-white hover:bg-fail-600",
        className,
      )}
    >
      {busy && <Spinner />}
      {children}
    </button>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cx("size-4 animate-spin", className)} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

// ------------------------------------------------------------------- Badge

const TONE: Record<Tone, string> = {
  neutral: "bg-ink-100 text-ink-700 ring-ink-200",
  info: "bg-brand-50 text-brand-700 ring-brand-100",
  pass: "bg-pass-50 text-pass-700 ring-pass-100",
  review: "bg-review-50 text-review-800 ring-review-100",
  fail: "bg-fail-50 text-fail-700 ring-fail-100",
};
const DOT: Record<Tone, string> = {
  neutral: "bg-ink-400",
  info: "bg-brand-500",
  pass: "bg-pass-600",
  review: "bg-review-700",
  fail: "bg-fail-600",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        "inline-flex max-w-full items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        TONE[tone],
        className,
      )}
    >
      <span className={cx("size-1.5 shrink-0 rounded-full", DOT[tone])} aria-hidden />
      <span className="truncate">{children}</span>
    </span>
  );
}

/** A labelled status: "Verification · Failed". Keeps dimensions visibly separate. */
export function StatusField({ label, tone, value }: { label: string; tone: Tone; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">{label}</div>
      <div className="mt-1">
        <Badge tone={tone}>{value}</Badge>
      </div>
    </div>
  );
}

// -------------------------------------------------------------------- Card

export function Card({ children, className, as: As = "section" }: { children: ReactNode; className?: string; as?: "section" | "div" | "article" }) {
  return <As className={cx("rounded-xl border border-ink-200 bg-white shadow-[0_1px_2px_rgb(15_31_58/0.04)]", className)}>{children}</As>;
}

export function CardHeader({ title, subtitle, action }: { title: ReactNode; subtitle?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-ink-100 px-4 py-3 sm:px-5">
      <div className="min-w-0">
        <h2 className="text-sm font-semibold text-ink-900">{title}</h2>
        {subtitle && <p className="mt-0.5 text-sm text-ink-500">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

// ------------------------------------------------------------------- Notice

export function Notice({ tone = "info", title, children, className }: { tone?: Tone; title?: ReactNode; children?: ReactNode; className?: string }) {
  const border: Record<Tone, string> = {
    neutral: "border-ink-200 bg-ink-50",
    info: "border-brand-100 bg-brand-50",
    pass: "border-pass-100 bg-pass-50",
    review: "border-review-100 bg-review-50",
    fail: "border-fail-100 bg-fail-50",
  };
  return (
    <div role={tone === "fail" ? "alert" : "status"} className={cx("rounded-lg border px-4 py-3 text-sm", border[tone], className)}>
      {title && <div className="font-semibold text-ink-900">{title}</div>}
      {children && <div className={cx("text-ink-700", title ? "mt-1" : null)}>{children}</div>}
    </div>
  );
}

// ---------------------------------------------------------------- Skeleton

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton rounded-md", className)} aria-hidden />;
}

export function LoadingBlock({ label = "Loading…", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" className="space-y-3 p-4">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className={cx("h-4", i % 2 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-10 text-center">
      <div className="mb-3 grid size-10 place-items-center rounded-full bg-ink-100 text-ink-500" aria-hidden>
        <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M4 7h16M4 12h10M4 17h7" strokeLinecap="round" />
        </svg>
      </div>
      <p className="text-sm font-semibold text-ink-900">{title}</p>
      {children && <p className="mt-1 max-w-md text-sm text-ink-500">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: { message: string; code?: string } | null; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <Notice tone="fail" title="Something went wrong">
      <p>{error.message}</p>
      {error.code && <p className="mt-1 font-mono text-xs text-ink-500">{error.code}</p>}
      {onRetry && (
        <Button size="sm" variant="secondary" className="mt-3" onClick={onRetry}>
          Retry
        </Button>
      )}
    </Notice>
  );
}

// -------------------------------------------------------------------- Hash

export function Hash({ value, label, n = 10 }: { value: string | null | undefined; label?: string; n?: number }) {
  const [copied, setCopied] = useState(false);
  if (!value) return <span className="text-ink-500">—</span>;
  return (
    <button
      type="button"
      title={value}
      aria-label={`${label ?? "Value"} ${value}. Copy to clipboard`}
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        });
      }}
      className="inline-flex items-center gap-1 rounded font-mono text-xs text-ink-700 hover:text-brand-700"
    >
      {shortHash(value, n)}
      <span className="text-[10px] text-ink-500">{copied ? "copied" : "copy"}</span>
    </button>
  );
}

// -------------------------------------------------------------------- Tabs

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: { id: T; label: string; badge?: ReactNode }[];
  value: T;
  onChange: (id: T) => void;
  label: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  return (
    <div role="tablist" aria-label={label} className="-mb-px flex gap-1 overflow-x-auto border-b border-ink-200">
      {tabs.map((t, i) => {
        const active = t.id === value;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={active}
            aria-controls={`panel-${t.id}`}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(t.id)}
            onKeyDown={(e) => {
              if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
              e.preventDefault();
              const next = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
              refs.current[next]?.focus();
              onChange(tabs[next].id);
            }}
            className={cx(
              "inline-flex min-h-11 shrink-0 items-center gap-2 border-b-2 px-3 text-sm font-medium whitespace-nowrap transition-colors",
              active ? "border-brand-600 text-ink-900" : "border-transparent text-ink-500 hover:text-ink-800",
            )}
          >
            {t.label}
            {t.badge}
          </button>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ Dialog

/** Native <dialog>: focus trap, Escape and backdrop come from the platform. */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={id}
      onClose={onClose}
      onCancel={onClose}
      className={cx(
        "m-auto w-[calc(100%-2rem)] rounded-2xl border border-ink-200 bg-white p-0 text-ink-900 shadow-2xl",
        wide ? "max-w-3xl" : "max-w-lg",
      )}
    >
      {open && (
        <div className="flex max-h-[85vh] flex-col">
          <div className="flex items-start justify-between gap-4 border-b border-ink-100 px-5 py-4">
            <h2 id={id} className="text-base font-semibold">
              {title}
            </h2>
            <button type="button" onClick={onClose} className="-m-1 rounded p-1 text-ink-500 hover:text-ink-900" aria-label="Close dialog">
              <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          <div className="overflow-y-auto px-5 py-4 text-sm">{children}</div>
          {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-ink-100 px-5 py-3">{footer}</div>}
        </div>
      )}
    </dialog>
  );
}

// -------------------------------------------------------------- DefList

export function KV({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-ink-500">{k}</dt>
          <dd className="min-w-0 break-words font-medium text-ink-900">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Field({ label, hint, children, htmlFor }: { label: string; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return (
    <div>
      <label htmlFor={htmlFor} className="block text-sm font-medium text-ink-800">
        {label}
      </label>
      {hint && <p className="mt-0.5 text-xs text-ink-500">{hint}</p>}
      <div className="mt-1.5">{children}</div>
    </div>
  );
}

export const inputCls =
  "block w-full rounded-lg border border-ink-300 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm placeholder:text-ink-500 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100 disabled:bg-ink-50";
