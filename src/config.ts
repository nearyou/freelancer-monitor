import { existsSync, readFileSync } from 'node:fs';

export const DEFAULT_SCORE = {
  payment_verified: 20, deposit_made: 15, profile_completed: 5,
  reviews_max: 15, member_age_max: 10, reviews_cap: 50, member_age_max_days: 100,
  identity_verified: 0, phone_verified: 0, email_verified: 0, rating_max: 0,
};
export type ScoreConfig = typeof DEFAULT_SCORE;
export type ScoreKey = keyof ScoreConfig;
export const SCORE_KEYS = Object.keys(DEFAULT_SCORE) as ScoreKey[];
const caps = new Set<ScoreKey>(['reviews_cap', 'member_age_max_days']);
export const maximum = (config: ScoreConfig): number => SCORE_KEYS.reduce((sum, key) => sum + (caps.has(key) ? 0 : config[key]), 0);

export function numeric(value: string): number {
  if (!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())) throw new Error('Enter a finite number.');
  const result = Number(value);
  if (!Number.isFinite(result)) throw new Error('Enter a finite number.');
  return result;
}

export function updateScore(config: ScoreConfig, name: string, value: string): ScoreConfig {
  if (!Object.hasOwn(DEFAULT_SCORE, name)) throw new Error('Unknown score setting. Use /show_score to see valid names.');
  const key = name as ScoreKey;
  const number = numeric(value);
  if (caps.has(key)) {
    if (!Number.isSafeInteger(number) || number < 1) throw new Error(`${name} must be a positive safe whole number.`);
  } else if (number < 0) throw new Error('Score weights must be zero or greater. Use 0 to disable a signal.');
  const updated = { ...config, [key]: number };
  if (!Number.isFinite(maximum(updated))) throw new Error('The combined maximum score is too large.');
  return updated;
}

export interface Settings {
  telegramToken: string; chatId: string; sendInterval: number;
  apiUrl: string; pageLimit: number; languages: string[];
  pollInterval: number; dbPath: string; logLevel: string;
}

export function loadDotenv(path = '.env', env: NodeJS.ProcessEnv = process.env): void {
  if (!existsSync(path)) return;
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const index = trimmed.indexOf('=');
    if (index < 0) continue;
    const key = trimmed.slice(0, index).trim();
    let value = trimmed.slice(index + 1).trim();
    if (value.length >= 2 && ['"', "'"].includes(value[0]!) && value.at(-1) === value[0]) value = value.slice(1, -1);
    if (env[key] === undefined) env[key] = value;
  }
}

export function settingsFromEnv(env: NodeJS.ProcessEnv = process.env): Settings {
  const number = (name: string, fallback: number, min: number, integer = false) => {
    const n = numeric(env[name] ?? String(fallback));
    if (n < min || (integer && !Number.isSafeInteger(n))) throw new Error(`${name} must be ${integer ? 'a whole number ' : ''}at least ${min}.`);
    return n;
  };
  const telegramToken = env.TELEGRAM_BOT_TOKEN?.trim() ?? '';
  const chatId = env.TELEGRAM_CHAT_ID?.trim() ?? '';
  if (!telegramToken || !chatId) throw new Error('TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required.');
  const apiUrl = env.FREELANCER_API_URL?.trim() || 'https://www.freelancer.com/api/projects/0.1/projects/active/';
  if (!['https:', 'http:'].includes(new URL(apiUrl).protocol)) throw new Error('Invalid FREELANCER_API_URL.');
  return {
    telegramToken, chatId, apiUrl,
    sendInterval: number('TELEGRAM_SEND_INTERVAL_SECONDS', 3.1, 3),
    pageLimit: number('FREELANCER_PAGE_LIMIT', 50, 1, true),
    languages: (env.FREELANCER_LANGUAGES ?? '').split(',').map(s => s.trim()).filter(Boolean),
    pollInterval: number('POLL_INTERVAL_SECONDS', 15, 5, true),
    dbPath: env.STATE_DB_PATH || 'freelancer_monitor.db',
    logLevel: (env.LOG_LEVEL || 'INFO').toUpperCase(),
  };
}
