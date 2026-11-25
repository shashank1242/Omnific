import type { Metadata, Viewport } from "next";
import { Archivo, JetBrains_Mono } from "next/font/google";
import Link from "next/link";
import type { ReactNode } from "react";
import NavLinks from "@/components/NavLinks";
import "./globals.css";

const sans = Archivo({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: "Omnific",
  description: "Describe a physical scenario and get an inspectable, evaluated 3D simulation.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0b0c10" },
    { media: "(prefers-color-scheme: light)", color: "#f4f5f7" },
  ],
};

/** The wordmark's mark: one object and two fading exposures, the strobe in miniature. */
function Mark() {
  return (
    <svg width="26" height="14" viewBox="0 0 26 14" aria-hidden className="shrink-0">
      <circle cx="4" cy="7" r="3" fill="var(--c2)" fillOpacity="0.35" />
      <circle cx="12" cy="7" r="4" fill="var(--c1)" fillOpacity="0.6" />
      <circle cx="21" cy="7" r="5" fill="var(--c3)" />
    </svg>
  );
}

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body className="min-h-screen antialiased">
        <header className="sticky top-0 z-20 border-b border-line bg-bg">
          <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
            <Link href="/" className="flex items-center gap-2.5 text-lg font-extrabold tracking-tight">
              <Mark />
              Omnific
            </Link>
            <NavLinks />
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6">{children}</main>
      </body>
    </html>
  );
}
