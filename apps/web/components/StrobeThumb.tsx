"use client";

import { useEffect, useState } from "react";
import { fetchJson } from "@/lib/api";
import { parsePlayback } from "@/lib/playback";
import { exposures, exposureTimes, longExposure, project, type Projected } from "@/lib/strobe";

const W = 400;
const H = 225;
const COUNT = 11;
const GELS = ["var(--c1)", "var(--c2)", "var(--c3)"]; // the three gels; more objects reuse them

/** A simulation's motion as a multiple exposure, drawn from its own playback data. */
export default function StrobeThumb({ playbackUrl, className = "" }: { playbackUrl: string; className?: string }) {
  const [pic, setPic] = useState<Projected | null>(null);
  const [still, setStill] = useState<[number, number][]>([]);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    fetchJson(playbackUrl)
      .then((raw) => {
        const pb = parsePlayback(raw);
        if (!live) return;
        const p = project(exposures(pb, exposureTimes(pb, COUNT)), W, H, 26);
        setPic(p);
        if (p.trails.length === 0) setStill(longExposure(pb, W, H, 30));
      })
      .catch(() => live && setFailed(true));
    return () => { live = false; };
  }, [playbackUrl]);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className={`block h-full w-full bg-[var(--studio)] ${className}`} role="img" aria-label="Strobe exposure of the motion" preserveAspectRatio="xMidYMid meet">
      <rect width={W} height={H} fill="var(--studio)" />
      {pic && !failed ? (
        <>
          {still.length > 1 ? (
            <>
              <polyline points={still.map((p) => p.join(",")).join(" ")} fill="none" stroke="var(--c3)" strokeOpacity={0.25} strokeWidth={14} strokeLinecap="round" strokeLinejoin="round" />
              {still.filter((_, i) => i % 4 === 0 || i === still.length - 1).map(([x, y], i, arr) => (
                <circle key={`s${i}`} className="exposure" cx={x} cy={y} r={i === arr.length - 1 ? 9.5 : 3.5 + (4.5 * (i + 1)) / arr.length} fill={i === arr.length - 1 ? "var(--c1)" : "var(--c3)"} fillOpacity={0.35 + (0.55 * (i + 1)) / arr.length} />
              ))}
            </>
          ) : null}
          {pic.links.map((l, i) => (
            <line key={`l${i}`} x1={l.from[0]} y1={l.from[1]} x2={l.to[0]} y2={l.to[1]} stroke="rgb(var(--fg))" strokeOpacity={0.55} strokeWidth={2} strokeLinecap="round" />
          ))}
          {pic.trails.map((t, ti) =>
            t.points.map(([x, y], i) => {
              const age = (i + 1) / t.points.length; // older exposures are fainter, the last is the flash
              const last = i === t.points.length - 1;
              return (
                <circle
                  key={`${t.entity}-${i}`}
                  className="exposure"
                  cx={x}
                  cy={y}
                  r={last ? 9.5 : 3.5 + 4.5 * age}
                  fill={GELS[ti % GELS.length]}
                  fillOpacity={last ? 1 : 0.35 + 0.55 * age}
                />
              );
            }),
          )}
        </>
      ) : null}
    </svg>
  );
}
