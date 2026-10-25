import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TelegramClient } from '../src/telegram.js';
import { BOT_COMMANDS } from '../src/commands.js';
import { Monitor } from '../src/monitor.js';
import { NotificationSender, nextPollTime, pollLoop } from '../src/runtime.js';
import { sleep, type Fetcher } from '../src/http.js';
import { FreelancerClient, type ProjectSource } from '../src/freelancer.js';
import { storeFor, project, settings, fixture, FakeTelegram } from './helpers.js';

class Source implements ProjectSource {
  projects = [project()];
  async resolveSkills() { return []; }
  async fetchProjects() { return this.projects; }
}
test('API to queue to Telegram integration and duplicate suppression', async t => {
  const store = storeFor(t); store.setThreshold(0);
  const payload = fixture(); for (const item of payload.result.projects) item.submitdate = Date.now() / 1000;
  let calls = 0;
  const client = new FreelancerClient('https://example.test/api', async () => { calls++; return Response.json(payload); });
  const monitor = new Monitor(settings(), client, store); const telegram = new FakeTelegram();
  assert.deepEqual(await monitor.runOnce(), { processed: 2, queued: 2 });
  assert.equal(calls, 1); assert.deepEqual(telegram.messages, []);
  const sender = new NotificationSender(store, telegram);
  assert.equal(await sender.sendNext(), true); assert.equal(await sender.sendNext(), true); assert.equal(await sender.sendNext(), false);
  assert.match(telegram.messages[0]!, /Phone verified/);
  assert.deepEqual(await monitor.runOnce(), { processed: 0, queued: 0 });
});
test('failed API poll preserves history; successful empty batch prunes it', async t => {
  const store = storeFor(t); store.setThreshold(0); store.markSeen(99, 50);
  const source = new Source(); source.fetchProjects = async () => { throw new Error('offline'); };
  const monitor = new Monitor(settings(), source, store);
  await assert.rejects(monitor.runOnce()); assert.equal(store.hasSeen(99), true);
  source.fetchProjects = async () => []; await monitor.runOnce(); assert.equal(store.hasSeen(99), false);
});
test('incomplete owner defers one project; stale and unmatched projects are marked processed', async t => {
  const store = storeFor(t); store.setThreshold(0); store.addTerm('skill', 'Python');
  const source = new Source(); source.projects = [
    project(1, { employer_data_complete: false }), project(2),
    project(3, { submitted_at: new Date(Date.now() - 7200000) }), project(4, { skills: ['Rust'] }),
  ];
  const monitor = new Monitor(settings(), source, store);
  assert.deepEqual(await monitor.runOnce(), { processed: 4, queued: 1 });
  assert.equal(store.deferred(1), true); assert.equal(store.hasSeen(1), false);
  assert.equal(store.hasSeen(3), true); assert.equal(store.hasSeen(4), true);
  assert.deepEqual(await monitor.runOnce(), { processed: 0, queued: 0 });
});
test('missing optional verification only defers when weight enabled', async t => {
  const store = storeFor(t); store.setThreshold(0);
  const source = new Source(); const monitor = new Monitor(settings(), source, store);
  assert.equal((await monitor.runOnce()).queued, 1);
  store.setScore('identity_verified', '10'); source.projects = [project(2)];
  assert.equal((await monitor.runOnce()).queued, 0); assert.equal(store.deferred(2), true);
});
test('new scoring settings apply to next poll; queued score remains unchanged', async t => {
  const store = storeFor(t); store.setThreshold(20);
  const source = new Source(); const p = project(); p.employer.payment_verified = true; source.projects = [p];
  const monitor = new Monitor(settings(), source, store);
  assert.equal((await monitor.runOnce()).queued, 1);
  store.setScore('payment_verified', '0'); source.projects = [{ ...p, id: 2 }];
  assert.equal((await monitor.runOnce()).queued, 0); assert.equal(store.nextNotification()!.score, 20);
});
test('slow notification delivery does not block subsequent project poll', async t => {
  const store = storeFor(t); store.setThreshold(0); const source = new Source();
  const monitor = new Monitor(settings(), source, store); await monitor.runOnce();
  const telegram = new FakeTelegram(); let release!: () => void;
  telegram.sendMessage = () => new Promise<void>(resolve => { release = resolve; });
  const sending = new NotificationSender(store, telegram).sendNext();
  source.projects = [project(2)]; assert.equal((await monitor.runOnce()).queued, 1);
  assert.equal(store.pendingCount(), 2); release(); await sending; assert.equal(store.pendingCount(), 1);
});
test('notification failure retries independently of current batch', async t => {
  const store = storeFor(t); store.enqueueProject(1, 'alert', 'url', 50);
  const telegram = new FakeTelegram(); telegram.sendMessage = async () => { throw new Error('offline'); };
  assert.equal(await new NotificationSender(store, telegram).sendNext(), true);
  store.retainBatch([]); assert.equal(store.hasSeen(1), true); assert.equal(store.nextNotification(), undefined);
  assert.equal(store.nextNotification(Date.now() / 1000 + 31)!.attempts, 1);
});
test('shutdown leaves notification ready without incrementing attempts', async t => {
  const store = storeFor(t); store.enqueueProject(1, 'alert', 'url', 50);
  const stop = new AbortController(); const telegram = new FakeTelegram();
  telegram.sendMessage = async () => { stop.abort(); throw new Error('stopped'); };
  assert.equal(await new NotificationSender(store, telegram).sendNext(stop.signal), false);
  assert.equal(store.nextNotification()!.attempts, 0);
});
test('15-second scheduler avoids overlap and skips missed ticks', async () => {
  assert.equal(nextPollTime(0, 1000, 15000), 15000);
  assert.equal(nextPollTime(0, 16000, 15000), 31000);
  const stop = new AbortController(); let now = 0; const starts: number[] = [];
  await pollLoop(async () => { starts.push(now); now += starts.length === 1 ? 20000 : 1000; if (starts.length === 3) stop.abort(); return { processed: 0, queued: 0 }; }, 15000, stop.signal, async ms => { now += ms; }, () => now);
  assert.deepEqual(starts, [0, 35000, 50000]);
});
test('abortable wait stops immediately', async () => {
  const stop = new AbortController(); const waiting = sleep(10000, stop.signal); stop.abort();
  await assert.rejects(waiting);
});
test('Telegram sends HTML and inline button, spaces messages by 3.1 seconds', async () => {
  let now = 100000; const sent: { time: number; params: URLSearchParams }[] = [];
  const request: Fetcher = async (_input, init) => { sent.push({ time: now, params: new URLSearchParams(String(init?.body)) }); return Response.json({ ok: true }); };
  const telegram = new TelegramClient('token', '123', 3.1, request, async ms => { now += ms; }, () => now);
  await telegram.sendMessage('<b>hello</b>', 'https://example.test/1'); await telegram.sendMessage('reply');
  assert.equal(sent[1]!.time - sent[0]!.time, 3100);
  assert.equal(sent[0]!.params.get('parse_mode'), 'HTML');
  assert.equal(JSON.parse(sent[0]!.params.get('reply_markup')!).inline_keyboard[0][0].url, 'https://example.test/1');
});
test('Telegram 429 cooldown survives exhausted retries and affects the next message', async () => {
  let now = 100000; const times: number[] = []; let calls = 0;
  const request: Fetcher = async () => { times.push(now); calls++; return calls <= 6 ? Response.json({ ok: false, parameters: { retry_after: 120 } }, { status: 429 }) : Response.json({ ok: true }); };
  const telegram = new TelegramClient('token', '123', 3.1, request, async ms => { now += ms; }, () => now);
  await assert.rejects(telegram.sendMessage('first')); await telegram.sendMessage('second');
  assert.equal(times.length, 7); for (let i = 1; i < times.length; i++) assert.ok(times[i]! - times[i - 1]! >= 120000);
});
test('Telegram network failures count for pacing and can be aborted during wait', async () => {
  let now = 0; let calls = 0; const waits: number[] = [];
  const telegram = new TelegramClient('token', '123', 3.1, async () => { calls++; throw new Error('offline'); }, async ms => { waits.push(ms); now += ms; }, () => now);
  await assert.rejects(telegram.sendMessage('one')); await assert.rejects(telegram.sendMessage('two'));
  assert.equal(waits[1], 3100); assert.equal(calls, 2);
  const stop = new AbortController(); stop.abort(); await assert.rejects(telegram.sendMessage('three', null, stop.signal)); assert.equal(calls, 2);
});
test('Telegram menu scoped to configured chat with all commands and descriptions', async () => {
  let params!: URLSearchParams;
  const client = new TelegramClient('token', '-123', 3.1, async (input, init) => {
    assert.ok(String(input).endsWith('/setMyCommands')); params = new URLSearchParams(String(init?.body));
    return Response.json({ ok: true, result: true });
  });
  await client.setCommands(BOT_COMMANDS);
  assert.deepEqual(JSON.parse(params.get('scope')!), { type: 'chat', chat_id: -123 });
  assert.equal(JSON.parse(params.get('commands')!).length, 23);
  for (const [name, description] of BOT_COMMANDS) { assert.match(name, /^[a-z_]{1,32}$/); assert.ok(description.length > 0 && description.length <= 256); }
});
