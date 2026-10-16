import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: { default: "OutcomeGuard — Pay for proof", template: "%s · OutcomeGuard" },
  description: "Don't just pay for a promise. Pay for proof. Evidence-based payment governance on PayPal (Sandbox).",
};

export const viewport: Viewport = { themeColor: "#0f1f3a", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full bg-ink-50 text-ink-900">{children}</body>
    </html>
  );
}
