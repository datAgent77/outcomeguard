"use client";

import type { Version } from "@/lib/types";
import { KIND_LABEL, KIND_PLAIN } from "@/lib/status";
import { Badge, Card, CardHeader, Notice } from "../ui";

function specSummary(spec: Record<string, unknown>): [string, string][] {
  return Object.entries(spec)
    .filter(([k]) => k !== "kind")
    .map(([k, v]) => [k.replaceAll("_", " "), typeof v === "object" ? JSON.stringify(v) : String(v)]);
}

export function CriteriaPanel({ version }: { version: Version }) {
  const locked = version.status === "locked" || version.status === "superseded";
  return (
    <div className="space-y-4">
      {locked && (
        <Notice tone="neutral" title={`Version v${version.version_number} is ${version.status}`}>
          These criteria are frozen. Neither party can change them; a change requires a new version that the buyer locks again.
        </Notice>
      )}
      {version.criteria.map((c) => (
        <Card key={c.id} as="article">
          <CardHeader
            title={c.title}
            subtitle={KIND_PLAIN[c.kind]}
            action={
              <div className="flex flex-wrap gap-1.5">
                {c.verification_method === "manual_review_required" ? (
                  <Badge tone="review">Manual review</Badge>
                ) : (
                  <>
                    <Badge>{KIND_LABEL[c.kind]}</Badge>
                    <Badge>Automated</Badge>
                  </>
                )}
                {c.blocking ? <Badge tone="fail">Blocking</Badge> : <Badge>Non-blocking</Badge>}
                <Badge tone={c.source === "ai_proposed" ? "info" : "neutral"}>
                  {c.source === "ai_proposed" ? "AI-drafted, buyer-reviewed" : c.source === "template" ? "Template" : "Buyer-written"}
                </Badge>
              </div>
            }
          />
          <dl className="grid gap-x-6 gap-y-1.5 px-4 py-3 text-sm sm:grid-cols-[max-content_1fr] sm:px-5">
            {specSummary(c.spec).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="capitalize text-ink-500">{k}</dt>
                <dd className="min-w-0 break-words font-mono text-xs leading-5">{v}</dd>
              </div>
            ))}
          </dl>
        </Card>
      ))}
    </div>
  );
}
