import type { Metadata } from "next";
import { ProviderWorkspace } from "./provider-workspace";

export const metadata: Metadata = { title: "Provider workspace" };

export default function Page() {
  return <ProviderWorkspace />;
}
