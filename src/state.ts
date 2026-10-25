import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { DEFAULT_SCORE, maximum, updateScore, type ScoreConfig } from './config.js';
import { fold } from './models.js';

export interface Notification {
  id: number; project_id: number | null; message: string; button_url: string | null; score: number; attempts: number;
}
export type TermKind = 'skill' | 'search_word';
const seconds = () => Date.now() / 1000;

export class Store {
  readonly db: DatabaseSync;
  constructor(path: string) {
    if (path !== ':memory:') mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    // Names, column types and timestamp units intentionally match the Python database.
    this.db.exec(`
      PRAGMA journal_mode = WAL;
      PRAGMA synchronous = NORMAL;
      PRAGMA temp_store = MEMORY;
      PRAGMA busy_timeout = 5000;
      CREATE TABLE IF NOT EXISTS seen_projects (
        project_id INTEGER PRIMARY KEY, seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        notified INTEGER NOT NULL, score REAL NOT NULL);
      CREATE TABLE IF NOT EXISTS filter_terms (
        kind TEXT NOT NULL CHECK(kind IN ('skill','search_word')), value TEXT NOT NULL,
        normalized_value TEXT NOT NULL, PRIMARY KEY(kind, normalized_value));
      CREATE TABLE IF NOT EXISTS runtime_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS project_failures (
        project_id INTEGER PRIMARY KEY, attempts INTEGER NOT NULL, retry_after REAL NOT NULL,
        last_error TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
      CREATE INDEX IF NOT EXISTS idx_project_failures_retry_after ON project_failures(retry_after);
      CREATE TABLE IF NOT EXISTS notification_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER UNIQUE, update_id INTEGER UNIQUE,
        message TEXT NOT NULL, button_url TEXT, score REAL NOT NULL DEFAULT 0,
        priority INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
        retry_after REAL NOT NULL DEFAULT 0, last_error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    `);
  }

  transaction<T>(action: () => T): T {
    this.db.exec('BEGIN IMMEDIATE');
    try { const result = action(); this.db.exec('COMMIT'); return result; }
    catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }

  initialize(): void {
    this.transaction(() => {
      this.db.prepare('INSERT OR IGNORE INTO runtime_settings(key,value) VALUES (?,?)').run('scoring_config', JSON.stringify(DEFAULT_SCORE));
      if (this.getSetting('preferences_initialized') !== '1') {
        this.setSetting('score_threshold', '65');
        this.setSetting('preferences_initialized', '1');
      }
    });
  }
  getSetting(key: string): string | undefined {
    return this.db.prepare('SELECT value FROM runtime_settings WHERE key=?').get(key)?.value as string | undefined;
  }
  setSetting(key: string, value: string): void {
    this.db.prepare('INSERT INTO runtime_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').run(key, value);
  }
  scoring(): { config: ScoreConfig; threshold: number } {
    const rows = this.db.prepare("SELECT key,value FROM runtime_settings WHERE key IN ('scoring_config','score_threshold')").all();
    const values = Object.fromEntries(rows.map(row => [String(row.key), String(row.value)]));
    if (!values.scoring_config || values.score_threshold === undefined) throw new Error('Runtime preferences have not been initialized.');
    const config: ScoreConfig = { ...DEFAULT_SCORE, ...JSON.parse(values.scoring_config) };
    return { config, threshold: Number(values.score_threshold) };
  }
  setThreshold(value: number): void {
    this.transaction(() => {
      const max = maximum(this.scoring().config);
      if (!Number.isFinite(value) || value < 0 || value > max) throw new Error(`Threshold must be between 0 and ${max}.`);
      this.setSetting('score_threshold', String(value));
    });
  }
  setScore(name: string, value: string) {
    return this.transaction(() => {
      const { config, threshold } = this.scoring();
      const updated = updateScore(config, name, value);
      if (threshold > maximum(updated)) throw new Error(`New maximum would be ${maximum(updated)}, below threshold ${threshold}. Use /set_threshold ${maximum(updated)} (or lower) first.`);
      this.setSetting('scoring_config', JSON.stringify(updated));
      return { config: updated, threshold };
    });
  }
  terms(kind: TermKind): string[] {
    return this.db.prepare('SELECT value FROM filter_terms WHERE kind=? ORDER BY normalized_value').all(kind).map(row => String(row.value));
  }
  addTerm(kind: TermKind, value: string): boolean {
    const cleaned = value.trim();
    if (!cleaned) return false;
    return this.db.prepare('INSERT OR IGNORE INTO filter_terms(kind,value,normalized_value) VALUES (?,?,?)').run(kind, cleaned, fold(cleaned)).changes > 0;
  }
  deleteTerm(kind: TermKind, value: string): boolean {
    return this.db.prepare('DELETE FROM filter_terms WHERE kind=? AND normalized_value=?').run(kind, fold(value.trim())).changes > 0;
  }
  hasSeen(id: number): boolean { return !!this.db.prepare('SELECT 1 FROM seen_projects WHERE project_id=?').get(id); }
  deferred(id: number, now = seconds()): boolean {
    return !!this.db.prepare('SELECT 1 FROM project_failures WHERE project_id=? AND retry_after>?').get(id, now);
  }
  failProject(id: number, error: string, now = seconds()): number {
    const attempts = Number(this.db.prepare('SELECT attempts FROM project_failures WHERE project_id=?').get(id)?.attempts ?? 0) + 1;
    const delay = Math.min(300 * 2 ** Math.min(attempts - 1, 20), 3600);
    this.db.prepare(`INSERT INTO project_failures(project_id,attempts,retry_after,last_error) VALUES (?,?,?,?)
      ON CONFLICT(project_id) DO UPDATE SET attempts=excluded.attempts,retry_after=excluded.retry_after,
      last_error=excluded.last_error,updated_at=CURRENT_TIMESTAMP`).run(id, attempts, now + delay, error.slice(0, 1000));
    return delay;
  }
  markSeen(id: number, score: number, notified = false): void {
    this.transaction(() => {
      this.db.prepare('INSERT OR IGNORE INTO seen_projects(project_id,notified,score) VALUES (?,?,?)').run(id, Number(notified), score);
      this.db.prepare('DELETE FROM project_failures WHERE project_id=?').run(id);
    });
  }
  clearHistory(): number {
    return Number(this.db.prepare('DELETE FROM seen_projects WHERE project_id NOT IN (SELECT project_id FROM notification_queue WHERE project_id IS NOT NULL)').run().changes);
  }
  retainBatch(ids: number[]): void {
    this.transaction(() => {
      this.db.exec('CREATE TEMP TABLE IF NOT EXISTS current_project_batch(project_id INTEGER PRIMARY KEY); DELETE FROM current_project_batch;');
      const insert = this.db.prepare('INSERT OR IGNORE INTO current_project_batch VALUES (?)');
      for (const id of ids) insert.run(id);
      this.db.exec(`DELETE FROM seen_projects WHERE project_id NOT IN (SELECT project_id FROM current_project_batch)
        AND project_id NOT IN (SELECT project_id FROM notification_queue WHERE project_id IS NOT NULL);
        DELETE FROM project_failures WHERE project_id NOT IN (SELECT project_id FROM current_project_batch);`);
    });
  }
  enqueueProject(id: number, message: string, url: string, score: number): boolean {
    return this.transaction(() => {
      const result = this.db.prepare(`INSERT OR IGNORE INTO notification_queue(project_id,message,button_url,score)
        SELECT ?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM seen_projects WHERE project_id=?)`).run(id, message, url, score, id);
      if (!result.changes) return false;
      this.db.prepare('INSERT INTO seen_projects(project_id,notified,score) VALUES (?,0,?)').run(id, score);
      this.db.prepare('DELETE FROM project_failures WHERE project_id=?').run(id);
      return true;
    });
  }
  enqueueReply(message: string, updateId: number): void {
    this.transaction(() => {
      this.db.prepare('INSERT OR IGNORE INTO notification_queue(update_id,message,priority) VALUES (?,?,1)').run(updateId, message);
      this.setSetting('telegram_update_offset', String(updateId + 1));
    });
  }
  nextNotification(now = seconds()): Notification | undefined {
    return this.db.prepare('SELECT id,project_id,message,button_url,score,attempts FROM notification_queue WHERE retry_after<=? ORDER BY priority DESC,id LIMIT 1').get(now) as unknown as Notification | undefined;
  }
  complete(notification: Notification): void {
    this.transaction(() => {
      if (notification.project_id !== null) this.db.prepare('UPDATE seen_projects SET notified=1 WHERE project_id=?').run(notification.project_id);
      this.db.prepare('DELETE FROM notification_queue WHERE id=?').run(notification.id);
    });
  }
  retry(notification: Notification, error: string, now = seconds()): number {
    const delay = Math.min(30 * 2 ** Math.min(notification.attempts, 7), 3600);
    this.db.prepare('UPDATE notification_queue SET attempts=attempts+1,retry_after=?,last_error=? WHERE id=?').run(now + delay, error.slice(0, 1000), notification.id);
    return delay;
  }
  pendingCount(): number { return Number(this.db.prepare('SELECT COUNT(*) AS n FROM notification_queue').get()!.n); }
  close(): void { this.db.close(); }
}
