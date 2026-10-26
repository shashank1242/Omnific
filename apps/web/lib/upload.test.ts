import { describe, expect, it } from "vitest";
import { MAX_INPUT_BYTES, inputKind, recordingExtension, validateInput, validateUpload } from "./upload";

describe("job attachments", () => {
  it("accepts images and audio only", () => {
    expect(validateInput({ name: "ramp.PNG", size: 10 })).toBeNull();
    expect(validateInput({ name: "note.m4a", size: 10 })).toBeNull();
    expect(validateInput({ name: "report.pdf", size: 10 })).toMatch(/unsupported type/);
    expect(validateInput({ name: "noext", size: 10 })).toMatch(/unsupported type/);
  });
  it("rejects empty and oversized files", () => {
    expect(validateInput({ name: "a.wav", size: 0 })).toMatch(/empty/);
    expect(validateInput({ name: "a.wav", size: MAX_INPUT_BYTES + 1 })).toMatch(/25 MB/);
  });
  it("library uploads keep their own limits", () => {
    expect(validateUpload({ name: "report.pdf", size: 10 })).toBeNull();
    expect(validateUpload({ name: "a.pdf", size: 51 * 1024 * 1024 })).toMatch(/50 MB/);
  });
  it("classifies by extension", () => {
    expect(inputKind("sketch.jpeg")).toBe("image");
    expect(inputKind("voice.webm")).toBe("audio");
  });
  it("names recordings from the recorder mime type", () => {
    expect(recordingExtension("audio/webm;codecs=opus")).toBe("webm");
    expect(recordingExtension("audio/mp4")).toBe("m4a");
    expect(recordingExtension("audio/ogg; codecs=opus")).toBe("ogg");
    expect(recordingExtension("")).toBe("webm");
  });
});

describe("attachment citations", () => {
  it("parses omnific://input urls only", async () => {
    const { parseInputCitation, parseDocCitation } = await import("./citations");
    expect(parseInputCitation("omnific://input/ramp_sketch.png")).toBe("ramp_sketch.png");
    expect(parseInputCitation("omnific://doc/abc#page=1")).toBeNull();
    expect(parseInputCitation("https://x.org")).toBeNull();
    expect(parseDocCitation("omnific://input/ramp_sketch.png")).toBeNull();
  });
});
