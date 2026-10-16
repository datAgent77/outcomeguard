import type { Metadata } from "next";
import { Suspense } from "react";
import { AgreementWizard } from "@/components/agreement/wizard";
import { LoadingBlock } from "@/components/ui";

export const metadata: Metadata = { title: "New agreement" };

export default function Page() {
  return (
    <Suspense fallback={<LoadingBlock rows={6} />}>
      <AgreementWizard />
    </Suspense>
  );
}
