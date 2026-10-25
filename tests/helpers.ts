import { readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import type { TestContext } from 'node:test';
import { Store } from '../src/state.js';
import { defaultEmployer, type Project } from '../src/models.js';
import { settingsFromEnv } from '../src/config.js';
import type { TelegramAPI, Update } from '../src/telegram.js';

export const fixture = () => JSON.parse(readFileSync('tests/fixtures/active_projects.json', 'utf8'));
export const settings = () => settingsFromEnv({ TELEGRAM_BOT_TOKEN: 'test-token', TELEGRAM_CHAT_ID: '123' });
export function storeFor(t: TestContext): Store {
  const dir = mkdtempSync(join(tmpdir(), 'freelancer-test-'));
  const store = new Store(join(dir, 'state.db')); store.initialize();
  t.after(() => { store.close(); rmSync(dir, { recursive: true, force: true }); });
  return store;
}
export const project = (id = 1, overrides: Partial<Project> = {}): Project => ({
  id, title: 'Build <Python> bot', description: 'Telegram automation', url: `https://example.test/${id}`,
  owner_id: null, skills: ['Python', 'API'], listing_types: [], currency_code: 'USD',
  budget_min: 100, budget_max: 500, hourly: false, submitted_at: null,
  employer: defaultEmployer(), employer_data_complete: true, ...overrides,
});
export class FakeTelegram implements TelegramAPI {
  updates: Update[] = [];
  messages: string[] = [];
  registrations = 0;
  async setCommands() { this.registrations++; }
  async getUpdates() { return this.updates; }
  async sendMessage(text: string) { this.messages.push(text); }
}
