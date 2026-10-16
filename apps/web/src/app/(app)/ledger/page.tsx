import type { Metadata } from "next";
import { Suspense } from "react";
import { LedgerView } from "@/components/ledger/ledger-view";
import { LoadingBlock } from "@/components/ui";

export const metadata: Metadata = { title: "Ledger" };

export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock rows={8} />}>
      <LedgerView />
    </Suspense>
  );
}
