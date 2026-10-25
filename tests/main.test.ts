import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync, rmSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { tmpdir } from 'node:os';
import { pathToFileURL } from 'node:url';
import { Store } from '../src/state.js';

for (const scenario of ['success', 'failure', 'continuous']) test(`CLI ${scenario} uses durable queue and exits cleanly`, t => {
  const dir = mkdtempSync(join(tmpdir(), 'cli-test-')); t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, 'state.db'); const captured = join(dir, 'requests.jsonl');
  const original = new Store(path); original.initialize(); original.enqueueProject(1, 'Saved alert', 'https://example.test/1', 60); original.close();
  const result = spawnSync(process.execPath, [
    '--import', pathToFileURL(resolve('tests/fixtures/mock-api.mjs')).href, resolve('dist/src/main.js'),
    ...(scenario === 'continuous' ? [] : ['--once']),
  ], {
    cwd: dir, timeout: 15000, encoding: 'utf8', env: {
      ...process.env, TELEGRAM_BOT_TOKEN: 'test-token', TELEGRAM_CHAT_ID: '123', STATE_DB_PATH: path,
      FREELANCER_API_URL: 'https://example.test/api', MOCK_REQUESTS: captured, MOCK_SCENARIO: scenario,
    },
  });
  assert.equal(result.error, undefined, String(result.error));
  assert.equal(result.status, scenario === 'failure' ? 1 : 0, result.stderr);
  const reopened = new Store(path);
  try { assert.equal(reopened.pendingCount(), scenario === 'failure' ? 1 : 0); }
  finally { reopened.close(); }
  const calls = readFileSync(captured, 'utf8').trim().split('\n').map(line => JSON.parse(line));
  assert.equal(calls.filter(c => c.method === 'sendMessage').length, 1);
  assert.equal(calls.some(c => c.method === 'setMyCommands'), scenario === 'continuous');
});
test('CLI config check is offline and does not create the database', t => {
  const dir = mkdtempSync(join(tmpdir(), 'check-test-')); t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, 'state.db');
  const result = spawnSync(process.execPath, [resolve('dist/src/main.js'), '--check-config'], {
    cwd: dir, encoding: 'utf8', env: { ...process.env, TELEGRAM_BOT_TOKEN: 'test-token', TELEGRAM_CHAT_ID: '123', STATE_DB_PATH: path },
  });
  assert.equal(result.status, 0, result.stderr); assert.match(result.stdout, /configuration OK/); assert.equal(existsSync(path), false);
});
