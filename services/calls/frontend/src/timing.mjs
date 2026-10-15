// Audio timing uses one browser monotonic clock. No server timestamp subtraction.
export class TurnTiming {
  constructor() {
    this.responses = []; this.interruptions = [];
    this.userActive = false; this.botActive = false;
    this.pendingEnd = null; this.interruptionStart = null;
    this.deferredBotStart = null;
  }
  userStart(at) {
    this.userActive = true;
    this.pendingEnd = null;
    this.deferredBotStart = null;
    if (this.botActive && this.interruptionStart === null) this.interruptionStart = at;
  }
  userStop(lastActiveAt) {
    this.userActive = false; this.pendingEnd = lastActiveAt;
    if (this.deferredBotStart !== null) {
      const start = this.deferredBotStart; this.deferredBotStart = null;
      if (start >= lastActiveAt) this.recordResponse(start);
      else this.pendingEnd = null; // Genuine overlap, rather than a fast response.
    }
  }
  botStart(at) {
    this.botActive = true;
    if (this.userActive) {
      // Wait for the mic silence gate to report the actual last sound timestamp.
      this.deferredBotStart ??= at;
      return null;
    }
    if (this.pendingEnd === null || at < this.pendingEnd) return null;
    return this.recordResponse(at);
  }
  recordResponse(at) {
    const result = { latencyMs: Math.round(at - this.pendingEnd), userEndMs: this.pendingEnd, botStartMs: at };
    this.responses.push(result); this.pendingEnd = null;
    return result;
  }
  botStop(at) {
    this.botActive = false;
    if (this.interruptionStart !== null) {
      this.interruptions.push(Math.max(0, Math.round(at - this.interruptionStart)));
      this.interruptionStart = null;
    }
  }
}

export function summarize(values) {
  const sorted = [...values].filter(Number.isFinite).sort((a, b) => a - b);
  if (!sorted.length) return { n: 0, p50: null, p95: null };
  const middle = Math.floor(sorted.length / 2);
  return { n: sorted.length, p50: sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2,
    p95: sorted[Math.ceil(sorted.length * .95) - 1] };
}

export class ActivityGate {
  constructor({ threshold = .015, attackMs = 60, silenceMs = 200 } = {}) {
    Object.assign(this, { threshold, attackMs, silenceMs });
    this.active = false; this.first = null; this.last = null;
  }
  sample(rms, at) {
    if (rms >= this.threshold) {
      this.last = at;
      if (this.first === null) this.first = at;
      if (!this.active && at - this.first >= this.attackMs) {
        this.active = true;
        return { type: 'start', at: this.first };
      }
    } else if (!this.active) this.first = null;
    else if (at - this.last >= this.silenceMs) {
      this.active = false; this.first = null;
      return { type: 'stop', at: this.last };
    }
    return null;
  }
}

