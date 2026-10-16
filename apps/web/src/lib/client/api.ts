"use client";

// Browser → same-origin BFF (/api/og/*) → OutcomeGuard API. No tokens in JavaScript.
import { CSRF_HEADER } from "@/lib/bff";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public reasons: { code: string; detail?: string }[] = [],
    public body: unknown = null,
  ) {
    super(message);
  }
  /** The API could not tell whether PayPal executed the request (lost response, 5xx). */
  get outcomeUnknown(): boolean {
    return this.code === "OUTCOME_UNKNOWN";
  }
}

export const SESSION_EXPIRED_EVENT = "og:session-expired";

type Opts = {
  method?: "GET" | "POST" | "PUT" | "PATCH";
  body?: unknown;
  signal?: AbortSignal;
  idempotencyKey?: string;
};

function detailMessage(body: Record<string, unknown>): string | null {
  const d = body.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d.length && typeof d[0] === "object" && d[0]) {
    const first = d[0] as { loc?: unknown[]; msg?: string };
    return `${(first.loc ?? []).slice(1).join(".")}: ${first.msg ?? "invalid"}`;
  }
  return null;
}

export async function api<T>(path: string, opts: Opts = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { accept: "application/json" };
  if (method !== "GET") headers[CSRF_HEADER] = "1";
  if (opts.body !== undefined) headers["content-type"] = "application/json";
  if (opts.idempotencyKey) headers["idempotency-key"] = opts.idempotencyKey;
  let res: Response;
  try {
    res = await fetch(`/api/og${path}`, {
      method,
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
      credentials: "same-origin",
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "NETWORK_ERROR", "Network error — check your connection and try again.");
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const b = (body && typeof body === "object" ? body : {}) as Record<string, unknown>;
    if (res.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
    }
    const code = typeof b.code === "string" ? b.code : res.status === 422 ? "VALIDATION_ERROR" : `HTTP_${res.status}`;
    const message =
      (typeof b.message === "string" && b.message) || detailMessage(b) || `Request failed (${res.status})`;
    throw new ApiError(res.status, code, message, Array.isArray(b.reasons) ? (b.reasons as ApiError["reasons"]) : [], body);
  }
  return body as T;
}

/** Fetches a binary artifact (screenshot, Lighthouse report) through the BFF. */
export async function fetchBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const res = await fetch(`/api/og${path}`, { signal, credentials: "same-origin" });
  if (res.status === 401) window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  if (!res.ok) throw new ApiError(res.status, `HTTP_${res.status}`, `Could not load artifact (${res.status})`);
  return res.blob();
}

export function newIdempotencyKey(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`.slice(0, 64);
}

export async function sessionRequest(path: "login" | "logout", body?: unknown): Promise<Response> {
  return fetch(`/api/session/${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", [CSRF_HEADER]: "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
}
