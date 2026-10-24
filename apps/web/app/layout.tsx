import type { Metadata, Viewport } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "Omnific",
  description: "Describe a physical scenario and get an inspectable, evaluated 3D simulation.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <header className="border-b border-line bg-surface">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
            <Link href="/" className="text-lg font-semibold tracking-tight">Omnific</Link>
            <nav aria-label="Main" className="flex items-center gap-4 text-sm">
              <Link href="/library" className="text-accent underline underline-offset-2">Library</Link>
              <span className="hidden text-xs text-muted sm:inline">prompt to evaluated simulation</span>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
