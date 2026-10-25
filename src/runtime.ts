import { Commands } from './commands.js';
import { safeError, sleep, type Sleeper } from './http.js';
import { Store } from './state.js';
import type { TelegramAPI } from './telegram.js';

export class NotificationSender {
  constructor(private readonly store: Store, private readonly telegram: TelegramAPI) {}
  async sendNext(signal?: AbortSignal): Promise<boolean> {
    if (signal?.aborted) return false;
    const notification = this.store.nextNotification();
    if (!notification) return false;
    try { await this.telegram.sendMessage(notification.message, notification.button_url, signal); }
    catch (error) {
      if (signal?.aborted) return false;
      const delay = this.store.retry(notification, safeError(error));
      console.warn(`Notification ${notification.id} will retry in ${delay} seconds.`);
      return true;
    }
    this.store.complete(notification);
    console.info(`Delivered notification ${notification.id}.`);
    return true;
  }
}

export function nextPollTime(previous: number, now: number, interval: number): number {
  const next = previous + interval;
  return next <= now ? now + interval : next;
}

export async function pollLoop(
  poll: (signal?: AbortSignal) => Promise<{ processed: number; queued: number }>, intervalMs: number,
  signal: AbortSignal, wait: Sleeper = sleep, now = () => performance.now(),
): Promise<void> {
  let next = now();
  while (!signal.aborted) {
    try { const result = await poll(signal); console.info(`Poll complete: processed=${result.processed} queued=${result.queued}`); }
    catch (error) { if (signal.aborted) break; console.error('Project poll failed; will retry:', safeError(error)); }
    next = nextPollTime(next, now(), intervalMs);
    try { await wait(next - now(), signal); } catch (error) { if (!signal.aborted) throw error; }
  }
}

export async function senderLoop(sender: NotificationSender, signal: AbortSignal): Promise<void> {
  while (!signal.aborted) {
    try { if (!await sender.sendNext(signal)) await sleep(500, signal); }
    catch (error) {
      if (signal.aborted) break;
      console.error('Notification queue failed:', safeError(error));
      try { await sleep(5000, signal); } catch { break; }
    }
  }
}
export async function commandLoop(commands: Commands, signal: AbortSignal): Promise<void> {
  while (!signal.aborted) {
    try { await commands.pollOnce(signal); await sleep(50, signal); }
    catch (error) {
      if (signal.aborted) break;
      console.error('Telegram command poll failed:', safeError(error));
      try { await sleep(2000, signal); } catch { break; }
    }
  }
}
