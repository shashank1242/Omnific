"use client";

import { useEffect, useRef, useState } from "react";
import Icon from "./Icon";
import { INPUT_ACCEPT_ATTR, MAX_INPUTS, inputKind, recordingExtension, validateInput } from "@/lib/upload";

type SetFiles = React.Dispatch<React.SetStateAction<File[]>>;

const keyOf = (f: File, i: number) => `${i}-${f.name}-${f.size}-${f.lastModified}`;

/** Sketches/photos and voice notes that the intake agent reads as part of the prompt. */
export default function AttachmentPicker({ files, setFiles, disabled }: { files: File[]; setFiles: SetFiles; disabled?: boolean }) {
  const [error, setError] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [urls, setUrls] = useState<string[]>([]);
  const recorder = useRef<MediaRecorder | null>(null);
  const picker = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const next = files.map((f) => URL.createObjectURL(f));
    setUrls(next);
    return () => next.forEach((u) => URL.revokeObjectURL(u));
  }, [files]);

  useEffect(() => () => recorder.current?.stop(), []);

  function add(incoming: File[]) {
    const problems = incoming.map(validateInput).filter((m): m is string => m !== null);
    const ok = incoming.filter((f) => validateInput(f) === null);
    setFiles((cur) => {
      const room = Math.max(0, MAX_INPUTS - cur.length);
      if (ok.length > room) problems.push(`At most ${MAX_INPUTS} attachments per job.`);
      return [...cur, ...ok.slice(0, room)];
    });
    setError(problems.length > 0 ? problems.join(" ") : null);
  }

  async function startRecording() {
    if (typeof MediaRecorder === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setError("Voice recording isn't supported in this browser. Attach an audio file instead.");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      setError("Microphone access was denied.");
      return;
    }
    const rec = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    rec.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      recorder.current = null;
      setRecording(false);
      const type = rec.mimeType || "audio/webm";
      const stamp = new Date().toISOString().slice(11, 19).replaceAll(":", "");
      if (chunks.length > 0) add([new File(chunks, `voice-note-${stamp}.${recordingExtension(type)}`, { type })]);
    };
    rec.start();
    recorder.current = rec;
    setRecording(true);
    setError(null);
  }

  const button = "inline-flex h-9 items-center gap-1.5 rounded-full border border-line bg-surface px-3.5 text-sm font-medium text-muted transition-colors hover:border-fg/40 hover:text-fg disabled:opacity-50";
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={button} disabled={disabled || recording || files.length >= MAX_INPUTS} onClick={() => picker.current?.click()}
          title="Draw or photograph the setup; labelled values are read exactly as written">
          <Icon name="plus" size={15} />Sketch, photo or audio
        </button>
        {recording ? (
          <button type="button" className={`${button} border-bad text-bad hover:text-bad`} onClick={() => recorder.current?.stop()}>
            <span aria-hidden className="inline-block h-2 w-2 animate-pulse rounded-full bg-bad" />Stop recording
          </button>
        ) : (
          <button type="button" className={button} disabled={disabled || files.length >= MAX_INPUTS} onClick={() => void startRecording()}>
            <span aria-hidden className="inline-block h-2 w-2 rounded-full bg-accent" />Record a voice note
          </button>
        )}
        <input
          ref={picker}
          type="file"
          multiple
          accept={INPUT_ACCEPT_ATTR}
          className="hidden"
          aria-label="Attach a sketch, photo or audio file"
          onChange={(e) => { add(Array.from(e.target.files ?? [])); e.target.value = ""; }}
        />
      </div>
      {error ? <p role="alert" className="mt-2 text-sm text-bad">{error}</p> : null}
      {files.length > 0 ? (
        <ul className="mt-3 flex flex-wrap gap-3" aria-label="Attachments">
          {files.map((f, i) => (
            <li key={keyOf(f, i)} className="w-44 rounded-lg border border-line bg-surface p-2 text-xs">
              {inputKind(f.name) === "image" ? (
                // eslint-disable-next-line @next/next/no-img-element -- local object URL preview
                <img src={urls[i]} alt={f.name} className="h-24 w-full rounded object-contain" />
              ) : (
                <audio controls src={urls[i]} className="w-full" />
              )}
              <div className="mt-1 flex items-center justify-between gap-2">
                <span className="truncate font-mono" title={f.name}>{f.name}</span>
                <button
                  type="button"
                  disabled={disabled}
                  aria-label={`Remove ${f.name}`}
                  className="rounded p-0.5 text-muted hover:text-bad"
                  onClick={() => setFiles((cur) => cur.filter((_, j) => j !== i))}
                >
                  <Icon name="x" size={14} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
