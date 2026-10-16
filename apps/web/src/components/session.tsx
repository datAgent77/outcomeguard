"use client";

import { createContext, useContext, useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useApi } from "@/lib/client/hooks";
import { SESSION_EXPIRED_EVENT } from "@/lib/client/api";
import type { WorkspaceContext } from "@/lib/types";

type Session = {
  user: WorkspaceContext;
  reload: () => void;
  /** UI hint only — the API enforces every role. */
  can: (orgId: string, ...roles: string[]) => boolean;
  buyerOrgs: WorkspaceContext["organizations"];
  merchantOrgs: WorkspaceContext["organizations"];
  isBuyer: boolean;
  isProvider: boolean;
};

const Ctx = createContext<Session | null>(null);

export function useSession(): Session {
  const s = useContext(Ctx);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}

export function SessionProvider({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  const router = useRouter();
  const q = useApi<WorkspaceContext>("/workspace/context");
  useEffect(() => {
    const onExpired = () => {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      router.replace(`/login?expired=1&next=${next}`);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [router]);
  if (!q.data) {
    if (q.error && q.error.status !== 401) {
      return (
        <div className="mx-auto max-w-md p-6">
          <p role="alert" className="text-sm text-fail-700">
            Could not load your workspace: {q.error.message}
          </p>
          <button className="mt-3 text-sm font-medium text-brand-700 underline" onClick={q.reload}>
            Retry
          </button>
        </div>
      );
    }
    return <>{fallback}</>;
  }
  const user = q.data;
  const buyerOrgs = user.organizations.filter((o) => o.kind === "buyer");
  const merchantOrgs = user.organizations.filter((o) => o.kind === "merchant");
  const value: Session = {
    user,
    reload: q.reload,
    can: (orgId, ...roles) => user.organizations.some((o) => o.id === orgId && o.roles.some((r) => roles.includes(r))),
    buyerOrgs,
    merchantOrgs,
    isBuyer: buyerOrgs.length > 0,
    isProvider: merchantOrgs.length > 0,
  };
  return <Ctx value={value}>{children}</Ctx>;
}

export const ROLE_LABEL: Record<string, string> = {
  buyer_admin: "Buyer admin",
  buyer_approver: "Buyer approver",
  buyer_reviewer: "Buyer reviewer",
  merchant_admin: "Provider admin",
  merchant_approver: "Provider approver",
  provider: "Provider",
};
