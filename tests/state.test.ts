import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from '../src/state.js';
import { DEFAULT_SCORE, SCORE_KEYS } from '../src/config.js';
import { Commands, BOT_COMMANDS } from '../src/commands.js';
import { FakeTelegram, storeFor } from './helpers.js';

test('opens Python-created schema with preferences, pending alerts, offsets and cooldowns intact', t => {
  const dir = mkdtempSync(join(tmpdir(), 'legacy-test-')); t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, 'legacy.db');
  const legacy = new DatabaseSync(path); legacy.exec(readFileSync('tests/fixtures/python-state.sql', 'utf8')); legacy.close();
  let store = new Store(path);
  try {
    store.initialize();
    assert.deepEqual(store.terms('skill'), ['Python', 'Web Scraping']);
    assert.deepEqual(store.terms('search_word'), ['automation']);
    assert.equal(store.scoring().threshold, 40); assert.equal(store.scoring().config.identity_verified, 10);
    assert.equal(store.getSetting('telegram_update_offset'), '451');
    assert.equal(store.pendingCount(), 2); assert.equal(store.hasSeen(99), true);
    assert.equal(store.deferred(101, 2000000001), true);
    assert.equal(store.nextNotification(2000000001)!.project_id, 100);
    const reply = store.nextNotification(2000000031)!;
    assert.equal(reply.message, 'Saved command reply'); assert.equal(reply.attempts, 1);
    store.complete(reply); store.setScore('phone_verified', '7');
    store.close(); store = new Store(path); store.initialize();
    assert.equal(store.pendingCount(), 1); assert.equal(store.scoring().config.phone_verified, 7);
    const alert = store.nextNotification()!; store.complete(alert);
    assert.equal(store.pendingCount(), 0);
    assert.equal(store.db.prepare('SELECT notified FROM seen_projects WHERE project_id=100').get()!.notified, 1);
  } finally { store.close(); }
});
test('score defaults migrate once and preserve an existing high threshold', t => {
  const store = storeFor(t);
  store.db.exec("DELETE FROM runtime_settings WHERE key='scoring_config'");
  store.setSetting('score_threshold', '80'); store.initialize();
  assert.deepEqual(store.scoring(), { config: DEFAULT_SCORE, threshold: 80 });
  store.setThreshold(60); store.setScore('phone_verified', '5'); store.initialize();
  assert.equal(store.scoring().threshold, 60); assert.equal(store.scoring().config.phone_verified, 5);
});
test('all individual score setters, help and current values; generic command remains compatible', t => {
  const store = storeFor(t); store.setThreshold(0);
  const commands = new Commands(new FakeTelegram(), store, '123');
  for (const key of SCORE_KEYS) {
    assert.ok(BOT_COMMANDS.some(([command]) => command === `set_${key}`));
    assert.match(commands.handle(`/set_${key}@MonitorBot 12`), /Set <code>/);
    assert.equal(store.scoring().config[key], 12);
    assert.ok(commands.handle('/help').includes(`/set_${key}`));
    assert.ok(commands.handle(`/set_${key}`).includes('Current value: <b>12</b>'));
  }
  commands.handle('/set_score payment_verified 25'); assert.equal(store.scoring().config.payment_verified, 25);
  assert.equal(BOT_COMMANDS.length, 23);
});
test('invalid score and threshold commands leave state unchanged', t => {
  const store = storeFor(t); const commands = new Commands(new FakeTelegram(), store, '123');
  const original = store.scoring();
  for (const input of ['/set_phone_verified nan', '/set_reviews_cap 0', '/set_reviews_cap 1.5', '/set_score bogus 10', '/set_payment_verified 0', '/set_threshold 66', '/set_threshold -1', '/set_threshold Infinity', '/set_email_verified 2 3']) {
    commands.handle(input); assert.deepEqual(store.scoring(), original, input);
  }
  commands.handle('/set_threshold 45'); commands.handle('/set_payment_verified 0');
  assert.match(commands.handle('/set_threshold 46'), /between 0 and 45/);
});
test('commands enforce chat authorization, deduplicate updates and queue replies', async t => {
  const store = storeFor(t); const telegram = new FakeTelegram();
  telegram.updates = [
    { update_id: 1, message: { chat: { id: 999 }, text: '/set_phone_verified 20' } },
    { update_id: 2, message: { chat: { id: 123 }, text: '/set_phone_verified 5' } },
  ];
  const commands = new Commands(telegram, store, '123');
  assert.equal(await commands.pollOnce(), 1); assert.equal(await commands.pollOnce(), 0);
  assert.equal(store.scoring().config.phone_verified, 5); assert.equal(store.pendingCount(), 1);
  assert.equal(store.getSetting('telegram_update_offset'), '3');
  assert.equal(telegram.registrations, 1); assert.deepEqual(telegram.messages, []);
});
test('menu registration retries after 60 seconds without stopping commands', async t => {
  const store = storeFor(t); const telegram = new FakeTelegram(); let now = 0; let attempts = 0;
  telegram.setCommands = async () => { if (++attempts === 1) throw new Error('offline'); };
  const commands = new Commands(telegram, store, '123', () => now);
  await commands.pollOnce(); now = 59000; await commands.pollOnce(); assert.equal(attempts, 1);
  now = 60000; await commands.pollOnce(); await commands.pollOnce(); assert.equal(attempts, 2);
});
test('term commands handle duplicates, escaping, multiword names and removal', t => {
  const store = storeFor(t); const commands = new Commands(new FakeTelegram(), store, '123');
  commands.handle('/add_skill Web Scraping'); assert.match(commands.handle('/add_skill web scraping'), /Already exists/);
  assert.match(commands.handle('/show_skills'), /Web Scraping/);
  commands.handle('/add_search_word <bot>'); assert.match(commands.handle('/show_search_words'), /&lt;bot&gt;/);
  commands.handle('/delete_skill WEB SCRAPING'); assert.deepEqual(store.terms('skill'), []);
  assert.match(commands.handle('/add_skill'), /Usage/);
});
test('atomic enqueue, priority replies, failed sends and pending history protection', t => {
  const store = storeFor(t);
  assert.equal(store.enqueueProject(1, 'alert', 'https://example.test', 50), true);
  assert.equal(store.enqueueProject(1, 'duplicate', 'https://example.test', 50), false);
  store.enqueueReply('reply', 10); assert.equal(store.nextNotification()!.message, 'reply');
  const reply = store.nextNotification()!;
  assert.equal(store.retry(reply, 'offline', 1000), 30);
  assert.equal(store.nextNotification(1029)!.message, 'alert');
  assert.equal(store.nextNotification(1030)!.attempts, 1);
  assert.equal(store.clearHistory(), 0); assert.equal(store.pendingCount(), 2);
  store.retainBatch([]); assert.equal(store.hasSeen(1), true);
  store.complete(reply); store.complete(store.nextNotification()!);
  store.retainBatch([]); assert.equal(store.hasSeen(1), false);
});
test('latest batch bounded history including large batches and off-feed failures', t => {
  const store = storeFor(t);
  for (let id = 1; id <= 1100; id++) store.markSeen(id, 0);
  store.failProject(2000, 'missing', 1000);
  store.retainBatch([1099, 1100]);
  assert.equal(store.hasSeen(1), false); assert.equal(store.hasSeen(1100), true);
  assert.equal(store.deferred(2000, 1001), false);
  store.retainBatch([]); assert.equal(store.hasSeen(1100), false);
});
test('transaction failure rolls back both queue and dedup marker', t => {
  const store = storeFor(t);
  store.db.exec("CREATE TRIGGER fail_seen BEFORE INSERT ON seen_projects BEGIN SELECT RAISE(ABORT, 'test failure'); END;");
  assert.throws(() => store.enqueueProject(1, 'alert', 'url', 60));
  assert.equal(store.pendingCount(), 0); assert.equal(store.hasSeen(1), false);
});
