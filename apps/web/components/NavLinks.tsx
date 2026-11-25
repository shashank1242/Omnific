"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "New simulation", match: (p: string) => p === "/" },
  { href: "/explore", label: "Explore", match: (p: string) => p.startsWith("/explore") },
  { href: "/library", label: "Documents", match: (p: string) => p.startsWith("/library") },
];

export default function NavLinks() {
  const path = usePathname() ?? "/";
  return (
    <nav aria-label="Main" className="flex items-center gap-1 text-sm">
      {LINKS.map((l) => {
        const on = l.match(path);
        return (
          <Link
            key={l.href}
            href={l.href}
            aria-current={on ? "page" : undefined}
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${on ? "bg-surface-2 text-fg" : "text-muted hover:text-fg"} ${l.href === "/" ? "hidden sm:inline-block" : ""}`}
          >
            {l.label}
          </Link>
        );
      })}
    </nav>
  );
}
