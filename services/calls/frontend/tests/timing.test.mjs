import test from 'node:test';
import assert from 'node:assert/strict';

async function timing() {
  const module = await import('../src/timing.mjs').catch(() => null);
  assert.ok(module, 'Audible response timing is not implemented');
  return module;
}

test('greeting is excluded and response starts at the last active microphone sample', async () => {
  const { TurnTiming } = await timing();
  const t = new TurnTiming();
  t.botStart(100);
  t.botStop(400);
  t.userStart(600);
  t.userStop(1000);
  assert.equal(t.botStart(1750)?.latencyMs, 750);
  assert.equal(t.responses.length, 1);
});

test('continued speech replaces a false turn boundary', async () => {
  const { TurnTiming } = await timing();
  const t = new TurnTiming();
  t.userStart(100); t.userStop(400); t.userStart(500); t.userStop(900);
  assert.equal(t.botStart(1500)?.latencyMs, 600);
});

test('overlapping speech is not reported as a response latency', async () => {
  const { TurnTiming } = await timing();
  const t = new TurnTiming();
  t.userStart(100); t.userStop(400); t.userStart(500);
  assert.equal(t.botStart(600), null);
  assert.equal(t.responses.length, 0);
});

test('a fast reply before the silence gate closes is still counted', async () => {
  const { TurnTiming } = await timing();
  const t = new TurnTiming();
  t.userStart(100);
  t.botStart(850); // Detection of mic silence is delayed, but the last sound was at 800.
  t.userStop(800);
  assert.equal(t.responses.length, 1);
  assert.equal(t.responses[0].latencyMs, 50);
});

test('real overlapping audio remains excluded after the silence gate closes', async () => {
  const { TurnTiming } = await timing();
  const t = new TurnTiming();
  t.userStart(100); t.botStart(600); t.userStop(800);
  assert.equal(t.responses.length, 0);
});

test('interruption measures how long audible output continued', async () => {
  const { TurnTiming } = await timing();
  const t = new TurnTiming();
  t.botStart(100); t.userStart(500); t.botStop(800);
  assert.deepEqual(t.interruptions, [300]);
});

test('percentiles have no fabricated values before measurement', async () => {
  const { summarize } = await timing();
  assert.equal(summarize([]).p50, null);
  assert.equal(summarize([100, 200, 300, 400]).p50, 250);
  assert.equal(summarize([100, 200, 300, 400]).p95, 400);
});

test('audio gate ignores brief clicks and marks the actual last sound before silence', async () => {
  const { ActivityGate } = await timing();
  const g = new ActivityGate({ threshold: .02, attackMs: 60, silenceMs: 200 });
  assert.equal(g.sample(.1, 100), null);
  assert.equal(g.sample(0, 120), null);
  assert.equal(g.sample(.1, 200), null);
  assert.deepEqual(g.sample(.1, 270), { type: 'start', at: 200 });
  assert.equal(g.sample(.1, 300), null);
  assert.equal(g.sample(0, 450), null);
  assert.deepEqual(g.sample(0, 510), { type: 'stop', at: 300 });
});
