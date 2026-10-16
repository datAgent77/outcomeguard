import type { Metadata } from "next";
import { BuyerDashboard } from "./buyer-dashboard";

export const metadata: Metadata = { title: "Buyer dashboard" };

export default function Page() {
  return <BuyerDashboard />;
}
