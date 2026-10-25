import { setTimeout as delay } from 'node:timers/promises';

export type Fetcher = typeof fetch;
export const sleep = async (ms: number, signal?: AbortSignal): Promise<void> => {
  // Node timers overflow above 2^31-1; split long server-provided cooldowns.
  while (ms > 0) { const part = Math.min(ms, 2147483647); await delay(part, undefined, { signal }); ms -= part; }
  signal?.throwIfAborted();
};
export type Sleeper = typeof sleep;
export const safeError = (error: unknown): string => error instanceof Error
  ? error.message.replace(/bot\d+:[^/\s]+/g, 'bot[redacted]') : 'Unknown error';
export function retryAfter(value: string | null, fallback: number): number {
  if (!value) return fallback;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1000;
  const date = Date.parse(value);
  return Number.isFinite(date) ? Math.max(0, date - Date.now()) : fallback;
}

export async function getJson(url: URL, signal?: AbortSignal, request: Fetcher = fetch, wait: Sleeper = sleep): Promise<unknown> {
  for (let attempt = 0; ; attempt++) {
    let response: Response;
    try {
      response = await request(url, {
        headers: { Accept: 'application/json', 'User-Agent': 'FreelancerMonitor/1.0' },
        signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000),
      });
    } catch (error) {
      if (signal?.aborted || attempt >= 3) throw error;
      await wait(500 * 2 ** attempt, signal); continue;
    }
    if ([429, 500, 502, 503, 504].includes(response.status) && attempt < 3) {
      const cooldown = retryAfter(response.headers.get('retry-after'), 500 * 2 ** attempt);
      await response.body?.cancel();
      await wait(cooldown, signal); continue;
    }
    if (!response.ok) { await response.body?.cancel(); throw new Error(`Freelancer HTTP ${response.status}`); }
    return response.json();
  }
}
