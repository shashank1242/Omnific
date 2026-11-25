"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import { MAX_SPEED, MIN_SPEED, RUN_SECONDS } from "@/lib/speed";

export interface PlayerState { time: number; playing: boolean; speed: number }

/** Longest step one animation frame may take, so a stalled tab doesn't jump the clock (seconds). */
const MAX_FRAME_DT = 0.1;

/** External playback clock so only transport/charts re-render per frame, not the whole view. */
export class Player {
  state: PlayerState;
  private listeners = new Set<() => void>();
  private raf = 0;
  private last = 0;

  constructor(public t0: number, public t1: number) {
    this.state = { time: t0, playing: false, speed: 1 };
  }

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => {
      this.listeners.delete(fn);
    };
  };

  getState = () => this.state;

  private set(patch: Partial<PlayerState>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn());
  }

  seek(time: number) {
    this.set({ time: Math.min(this.t1, Math.max(this.t0, time)) });
  }

  setSpeed(speed: number) {
    if (Number.isFinite(speed)) this.set({ speed: Math.min(MAX_SPEED, Math.max(MIN_SPEED, speed)) });
  }

  play() {
    if (this.state.playing) return;
    if (this.state.time >= this.t1) this.state = { ...this.state, time: this.t0 };
    this.last = performance.now();
    this.set({ playing: true });
    const tick = (now: number) => {
      if (!this.state.playing) return;
      const dt = Math.min(MAX_FRAME_DT, Math.max(0, (now - this.last) / 1000));
      this.last = now;
      const span = this.t1 - this.t0;
      const next = this.state.time + (dt * this.state.speed * span) / RUN_SECONDS;
      if (next >= this.t1) {
        this.set({ time: this.t1, playing: false });
        return;
      }
      this.set({ time: next });
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  pause() {
    cancelAnimationFrame(this.raf);
    if (this.state.playing) this.set({ playing: false });
  }

  toggle() {
    if (this.state.playing) this.pause();
    else this.play();
  }

  dispose() {
    cancelAnimationFrame(this.raf);
    this.listeners.clear();
  }
}

export function usePlayerState(p: Player): PlayerState {
  return useSyncExternalStore(p.subscribe, p.getState, p.getState);
}

/**
 * Calls `fn` with the playback state now and on every change, without re-rendering the component: for things that
 * move every frame (chart cursors, live readouts), which write straight to the DOM through refs.
 */
export function usePlayerFrame(p: Player | null, fn: (s: PlayerState) => void): void {
  const cb = useRef(fn);
  cb.current = fn;
  useEffect(() => p?.subscribe(() => cb.current(p.state)), [p]);
  // and after every render, so a newly mounted readout (e.g. a comparison added while paused) is filled at once
  useEffect(() => {
    if (p) cb.current(p.state);
  });
}

const NO_PLAYER: PlayerState = { time: 0, playing: false, speed: 1 };
const noSubscribe = () => () => undefined;

/** Like usePlayerState, for views that may render before playback has loaded. */
export function useMaybePlayerState(p: Player | null): PlayerState {
  return useSyncExternalStore(p ? p.subscribe : noSubscribe, p ? p.getState : () => NO_PLAYER, p ? p.getState : () => NO_PLAYER);
}
