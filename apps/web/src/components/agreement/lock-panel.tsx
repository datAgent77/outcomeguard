"use client";

import { useState } from "react";
import { api, ApiError } from "@/lib/client/api";
import type { Agreement, Version } from "@/lib/types";
import { ERROR_HELP, KIND_LABEL, VERSION_STATUS } from "@/lib/status";
import { money, dateTime } from "@/lib/format";
import { Badge, Button, Card, CardHeader, Dialog, Hash, Notice } from "../ui";
import { useSession } from "../session";

/** Propose → approve (locks). Two explicit, separate human actions. AI never locks. */
export function LockPanel({
  agreement,
  merchantName,
  onChange,
}: {
  agreement: Agreement;
  merchantName: string;
  onChange: (a: Agreement) => void;
}) {
  const { can } = useSession();
  const v: Version = agreement.versions.reduce((a, b) => (b.version_number > a.version_number ? b : a));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [confirm, setConfirm] = useState(false);
  const org = agreement.buyer_organization_id;
  const canEdit = can(org, "buyer_admin");
  const canApprove = can(org, "buyer_admin", "buyer_approver");

  async function act(kind: "propose" | "approve" | "withdraw") {
    setBusy(kind);
    setError(null);
    try {
      const body = kind === "approve" ? { content_hash: v.content_hash } : { expected_revision: v.revision };
      const a = await api<Agreement>(`/agreements/${agreement.id}/versions/${v.version_number}/${kind}`, { method: "POST", body });
      onChange(a);
      setConfirm(false);
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setBusy(null);
    }
  }

  const vs = VERSION_STATUS[v.status];
  return (
    <Card>
      <CardHeader title="Review and lock" subtitle="Locking freezes this version. Payment and verification bind to its content hash." action={<Badge tone={vs.tone}>{vs.label}</Badge>} />
      <div className="space-y-4 px-4 py-4 sm:px-5">
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
          <dt className="text-ink-500">Title</dt>
          <dd className="font-medium">{v.title}</dd>
          <dt className="text-ink-500">Price</dt>
          <dd className="font-medium">{money(v.amount, v.currency)}</dd>
          <dt className="text-ink-500">Provider (payee)</dt>
          <dd className="font-medium">{merchantName}</dd>
          <dt className="text-ink-500">Delivery deadline</dt>
          <dd className="font-medium">{dateTime(v.delivery_deadline)}</dd>
          <dt className="text-ink-500">Version</dt>
          <dd className="font-medium">
            v{v.version_number} · revision {v.revision}
          </dd>
          {v.content_hash && (
            <>
              <dt className="text-ink-500">Content hash</dt>
              <dd>
                <Hash value={v.content_hash} label="Content hash" n={16} />
              </dd>
            </>
          )}
        </dl>
        <ul className="divide-y divide-ink-100 rounded-lg border border-ink-200">
          {v.criteria.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 text-sm">
              <span className="min-w-0">
                <span className="font-medium">{c.title}</span>
                <span className="ml-2 text-xs text-ink-500">{KIND_LABEL[c.kind]}</span>
              </span>
              <span className="flex gap-1.5">
                {c.blocking ? <Badge tone="fail">Blocking</Badge> : <Badge>Non-blocking</Badge>}
                {c.source === "ai_proposed" && <Badge tone="info">AI-drafted, buyer-reviewed</Badge>}
              </span>
            </li>
          ))}
        </ul>
        {error && (
          <Notice tone="fail" title={error.code}>
            {ERROR_HELP[error.code] ?? error.message}
          </Notice>
        )}
        {v.status === "draft" && (
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => act("propose")} busy={busy === "propose"} disabled={!canEdit}>
              Propose this version
            </Button>
            <p className="text-xs text-ink-500">Proposing freezes the content and computes its hash. Nothing is locked yet.</p>
          </div>
        )}
        {v.status === "proposed" && (
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => setConfirm(true)} disabled={!canApprove}>
              Approve and lock…
            </Button>
            {canEdit && (
              <Button variant="secondary" onClick={() => act("withdraw")} busy={busy === "withdraw"}>
                Withdraw to edit
              </Button>
            )}
            {!canApprove && <p className="text-xs text-ink-500">A buyer approver or admin must approve this version.</p>}
          </div>
        )}
        {v.status === "locked" && (
          <Notice tone="pass" title="Locked">
            Locked {dateTime(v.locked_at)}. The provider can now deliver against these criteria.
          </Notice>
        )}
      </div>
      <Dialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Approve and lock this agreement?"
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirm(false)}>
              Cancel
            </Button>
            <Button onClick={() => act("approve")} busy={busy === "approve"}>
              Approve and lock
            </Button>
          </>
        }
      >
        <p>
          You are approving version v{v.version_number} with content hash <Hash value={v.content_hash} n={16} />. Your approval quotes this
          hash; if the content changed since you loaded it, the approval is refused.
        </p>
        <p className="mt-3">
          After locking, the criteria cannot be changed — only replaced by a new version that must be locked again. Locking does not move
          any money.
        </p>
      </Dialog>
    </Card>
  );
}
