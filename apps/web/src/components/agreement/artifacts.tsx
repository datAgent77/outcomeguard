"use client";

import { useEffect, useState } from "react";
import { fetchBlob } from "@/lib/client/api";
import type { Artifact } from "@/lib/types";
import { bytes } from "@/lib/format";
import { Button, Dialog, Hash, LoadingBlock, Notice } from "../ui";

const KIND_LABEL: Record<Artifact["kind"], string> = {
  screenshot: "Screenshot",
  lighthouse_json: "Lighthouse report",
  http_trace: "HTTP trace",
  network_log: "Network egress decisions",
};

export function ArtifactButton({ artifact, onOpen }: { artifact: Artifact; onOpen: (a: Artifact) => void }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(artifact)}
      className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-ink-200 bg-white px-3 text-xs font-medium text-ink-700 hover:border-brand-500 hover:text-brand-700"
    >
      <ArtifactIcon kind={artifact.kind} />
      {KIND_LABEL[artifact.kind]}
      <span className="font-normal text-ink-500">{artifact.label}</span>
    </button>
  );
}

function ArtifactIcon({ kind }: { kind: Artifact["kind"] }) {
  const d =
    kind === "screenshot"
      ? "M4 6h16v12H4zM8 14l3-3 3 3 2-2 4 4"
      : kind === "lighthouse_json"
        ? "M4 18h16M7 15V9M12 15V6M17 15v-3"
        : "M5 7h14M5 12h14M5 17h9";
  return (
    <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
      <path d={d} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

type Loaded = { blob: Blob; url: string; json?: unknown; text?: string };

async function decode(a: Artifact, blob: Blob): Promise<Loaded> {
  const url = URL.createObjectURL(blob);
  if (a.content_type === "application/gzip") {
    const stream = blob.stream().pipeThrough(new DecompressionStream("gzip"));
    const text = await new Response(stream).text();
    return { blob, url, text, json: JSON.parse(text) };
  }
  if (a.content_type.includes("json")) {
    const text = await blob.text();
    return { blob, url, text, json: JSON.parse(text) };
  }
  return { blob, url };
}

/** Shows exactly the stored artifact. Nothing is synthesized: if it cannot be loaded or
 *  parsed, the viewer says so and offers the raw download. */
export function ArtifactViewer({ artifact, onClose }: { artifact: Artifact | null; onClose: () => void }) {
  const [raw, setState] = useState<{ id: string; loaded?: Loaded; error?: string } | null>(null);
  const state = raw && artifact && raw.id === artifact.id ? raw : { loaded: undefined, error: undefined };
  useEffect(() => {
    if (!artifact) return;
    const ctl = new AbortController();
    let url: string | undefined;
    fetchBlob(`/verification/artifacts/${artifact.id}`, ctl.signal)
      .then((b) => decode(artifact, b))
      .then((l) => {
        url = l.url;
        setState({ id: artifact.id, loaded: l });
      })
      .catch((e) => {
        if ((e as Error).name !== "AbortError") setState({ id: artifact.id, error: (e as Error).message });
      });
    return () => {
      ctl.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [artifact]);
  if (!artifact) return null;
  const l = state.loaded;
  return (
    <Dialog
      open
      wide
      onClose={onClose}
      title={`${KIND_LABEL[artifact.kind]} · ${artifact.label}`}
      footer={
        <>
          {l && (
            <a href={l.url} download={artifact.label} className="inline-flex min-h-9 items-center rounded-lg border border-ink-200 px-3 text-sm font-medium hover:bg-ink-50">
              Download original
            </a>
          )}
          <Button size="sm" onClick={onClose}>
            Close
          </Button>
        </>
      }
    >
      <p className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-500">
        <span>
          SHA-256 <Hash value={artifact.sha256} n={16} />
        </span>
        <span>{bytes(artifact.size_bytes)}</span>
        <span>{artifact.content_type}</span>
        <span>Integrity is re-checked by the API on every download.</span>
      </p>
      {state.error && <Notice tone="fail" title="Could not load this artifact">{state.error}</Notice>}
      {!l && !state.error && <LoadingBlock label="Loading artifact" />}
      {l && artifact.kind === "screenshot" && (
        // eslint-disable-next-line @next/next/no-img-element -- blob URL of stored evidence
        <img src={l.url} alt={`Stored screenshot: ${artifact.label}`} className="mx-auto max-h-[60vh] rounded border border-ink-200" />
      )}
      {l && artifact.kind === "lighthouse_json" && <LighthouseSummary report={l.json} />}
      {l && (artifact.kind === "http_trace" || artifact.kind === "network_log") && (
        <pre className="max-h-[55vh] overflow-auto rounded-lg bg-ink-950 p-3 text-xs leading-relaxed text-ink-100">{JSON.stringify(l.json, null, 2)}</pre>
      )}
    </Dialog>
  );
}

type LHR = {
  lighthouseVersion?: string;
  fetchTime?: string;
  finalDisplayedUrl?: string;
  finalUrl?: string;
  configSettings?: { formFactor?: string; throttlingMethod?: string };
  categories?: Record<string, { title: string; score: number | null }>;
  audits?: Record<string, { title: string; displayValue?: string; score: number | null }>;
  runWarnings?: string[];
};

const METRICS = ["first-contentful-paint", "largest-contentful-paint", "total-blocking-time", "cumulative-layout-shift", "speed-index"];

function LighthouseSummary({ report }: { report: unknown }) {
  const r = report as LHR;
  if (!r || typeof r !== "object" || !r.categories) {
    return <Notice tone="review">This file does not look like a Lighthouse result. Download it to inspect.</Notice>;
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        {Object.entries(r.categories).map(([k, c]) => (
          <div key={k} className="min-w-28 rounded-lg border border-ink-200 px-3 py-2">
            <div className="text-xs text-ink-500">{c.title}</div>
            <div className="text-xl font-semibold tabular-nums">{c.score == null ? "n/a" : Math.round(c.score * 100)}</div>
          </div>
        ))}
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-ink-100">
          {METRICS.filter((m) => r.audits?.[m]).map((m) => (
            <tr key={m}>
              <td className="py-1.5 text-ink-600">{r.audits![m].title}</td>
              <td className="py-1.5 text-right font-medium tabular-nums">{r.audits![m].displayValue ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-ink-500">
        Lighthouse {r.lighthouseVersion} · {r.configSettings?.formFactor} · throttling {r.configSettings?.throttlingMethod} · {r.finalDisplayedUrl ?? r.finalUrl} ·{" "}
        {r.fetchTime}
      </p>
      {r.runWarnings?.length ? <Notice tone="review" title="Lighthouse warnings">{r.runWarnings.join(" · ")}</Notice> : null}
    </div>
  );
}
