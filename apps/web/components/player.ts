"use client";

import { useSyncExternalStore } from "react";

export interface PlayerState { time: number; playing: boolean; speed: number }

/** Seconds of wall-clock a full run takes at 1x. */
const RUN_SECONDS = 10;

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
    this.set({ speed });
  }

  play() {
    if (this.state.playing) return;
    if (this.state.time >= this.t1) this.state = { ...this.state, time: this.t0 };
    this.last = performance.now();
    this.set({ playing: true });
    const tick = (now: number) => {
      if (!this.state.playing) return;
      const dt = (now - this.last) / 1000;
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
