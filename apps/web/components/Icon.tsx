// One drawn icon set: 24px grid, 1.75 stroke, round caps. Decorative by default (aria-hidden); pass `label` when the
// icon carries meaning on its own.
import type { SVGProps } from "react";

const PATHS = {
  check: "M5 12.5l4.5 4.5L19 7.5",
  x: "M6 6l12 12M18 6L6 18",
  arrowRight: "M5 12h13M13 6.5l5.5 5.5-5.5 5.5",
  loop: "M20 12a8 8 0 1 1-2.34-5.66M20 4.5V9h-4.5",
  reset: "M4 12a8 8 0 1 0 2.34-5.66M4 4.5V9h4.5",
  search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.3 15.3L20 20",
  file: "M14 3.5H7a1.5 1.5 0 0 0-1.5 1.5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8L14 3.5zM14 3.5V8h4.5",
  download: "M12 4v11M7 10.5l5 5 5-5M5 19.5h14",
  plus: "M12 5v14M5 12h14",
  play: "M8 5.5v13l10.5-6.5L8 5.5z",
  pause: "M9 5.5v13M15 5.5v13",
  // strobe: one object and its fading exposures
  strobe: "M18.5 12a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0zM10 12a1.75 1.75 0 1 1-3.5 0 1.75 1.75 0 0 1 3.5 0zM4.25 12h.01",
  move: "M12 3v18M3 12h18M12 3l-2.5 2.5M12 3l2.5 2.5M12 21l-2.5-2.5M12 21l2.5-2.5M3 12l2.5-2.5M3 12l2.5 2.5M21 12l-2.5-2.5M21 12l-2.5 2.5",
} as const;

export type IconName = keyof typeof PATHS;

export default function Icon({ name, label, size = 16, ...rest }: { name: IconName; label?: string; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
      {...rest}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
