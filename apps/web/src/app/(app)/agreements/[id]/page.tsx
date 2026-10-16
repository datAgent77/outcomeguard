import type { Metadata } from "next";
import { Suspense } from "react";
import { AgreementWorkspace } from "@/components/agreement/workspace";
import { LoadingBlock } from "@/components/ui";

export const metadata: Metadata = { title: "Agreement" };

export default function Page({ params }: { params: Promise<{ id: string }> }) {
  return (
    <Suspense fallback={<LoadingBlock rows={8} label="Loading agreement" />}>
      <AgreementWorkspace params={params} />
    </Suspense>
  );
}
