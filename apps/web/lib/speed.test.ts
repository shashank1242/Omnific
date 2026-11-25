import { describe, expect, it } from "vitest";
import { steadyFormatter } from "./format";
import { parseSpeed, realTimeSpeed, secondsPer, speedLabel } from "./speed";

describe("playback speed", () => {
  it("knows real time for time units", () => {
    expect(secondsPer("second")).toBe(1);
    expect(secondsPer("Minutes")).toBe(60);
    expect(secondsPer("kelvin")).toBeNull();
    expect(realTimeSpeed(5, "second")).toBeCloseTo(2); // a 5 s run in real time is 2x the 10 s default
    expect(realTimeSpeed(2, "minute")).toBeCloseTo(10 / 120);
    expect(realTimeSpeed(3, "furlong")).toBeNull();
    expect(realTimeSpeed(3.15e9, "second")).toBeNull(); // a century in real time: below the slowest speed
  });

  it("parses custom speeds", () => {
    expect(parseSpeed("3")).toBe(3);
    expect(parseSpeed(" 2.5× ")).toBe(2.5);
    expect(parseSpeed("0.5x")).toBe(0.5);
    expect(parseSpeed("0")).toBeNull();
    expect(parseSpeed("fast")).toBeNull();
    expect(parseSpeed("5000")).toBeNull();
    expect(speedLabel(0.333333)).toBe("0.333×");
  });
});

describe("steady live readouts", () => {
  it("keeps one width across the whole range", () => {
    const { format, width } = steadyFormatter(-2.5, 6.333);
    const samples = [-2.5, -0.0001, 0, 0.999, 1.0001, 6.333].map(format);
    expect(samples.every((s) => s.length <= width)).toBe(true);
    expect(samples).toContain("0.000");
    expect(samples).not.toContain("-0.000");
    expect(format(1.0001)).toBe("1.000");
  });

  it("uses a fixed exponential form for extreme magnitudes", () => {
    const { format } = steadyFormatter(0, 3.2e7);
    expect(format(1.23e6)).toBe("1.23e+6");
    expect(format(Number.NaN)).toBe("n/a");
    const neg = steadyFormatter(-100000, 0);
    expect(neg.format(-1e-100).length).toBeLessThanOrEqual(neg.width);
    expect(steadyFormatter(1e-6, 1e-6).format(1e-6)).toBe("1.00e-6"); // a flat tiny series keeps its digits
  });
});
