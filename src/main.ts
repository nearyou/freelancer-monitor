import { pathToFileURL } from 'node:url';
import { loadDotenv, maximum, settingsFromEnv } from './config.js';
import { Store } from './state.js';
import { FreelancerClient } from './freelancer.js';
import { TelegramClient } from './telegram.js';
import { Commands } from './commands.js';
import { Monitor } from './monitor.js';
import { commandLoop, NotificationSender, pollLoop, senderLoop } from './runtime.js';
import { safeError } from './http.js';

export async function main(args = process.argv.slice(2)): Promise<number> {
  if (args.includes('--help')) { console.log('Usage: npm start -- [--check-config | --once]'); return 0; }
  if (args.some(arg => !['--check-config', '--once'].includes(arg))) { console.error('Unknown argument. Use --help.'); return 2; }
  loadDotenv();
  let settings;
  try { settings = settingsFromEnv(); } catch (error) { console.error('Configuration error:', safeError(error)); return 2; }
  if (args.includes('--check-config')) { console.log('Environment configuration OK. Use /show_score in Telegram for SQLite scoring settings.'); return 0; }
  if (['WARNING', 'WARN', 'ERROR', 'CRITICAL'].includes(settings.logLevel)) console.info = () => {};
  if (['ERROR', 'CRITICAL'].includes(settings.logLevel)) console.warn = () => {};
  const stop = new AbortController();
  const shutdown = () => stop.abort();
  process.on('SIGINT', shutdown); process.on('SIGTERM', shutdown);
  let store: Store | undefined;
  try {
    store = new Store(settings.dbPath); store.initialize();
    const scoring = store.scoring();
    if (scoring.threshold > maximum(scoring.config)) console.warn('Stored threshold exceeds maximum; use /show_score and /set_threshold in Telegram.');
    const source = new FreelancerClient(settings.apiUrl);
    const telegram = new TelegramClient(settings.telegramToken, settings.chatId, settings.sendInterval);
    const monitor = new Monitor(settings, source, store);
    const sender = new NotificationSender(store, telegram);
    if (args.includes('--once')) {
      const result = await monitor.runOnce(stop.signal);
      console.info(`Poll complete: processed=${result.processed} queued=${result.queued}`);
      while (!stop.signal.aborted && await sender.sendNext(stop.signal)) { /* drain ready entries */ }
      if (store.pendingCount()) { console.warn(`${store.pendingCount()} notifications remain queued.`); return 1; }
      return 0;
    }
    // Network requests and rate-limit waits yield to the other independent loops.
    // SQLite transactions are short and synchronous; none spans an await.
    const loops = [
      pollLoop(signal => monitor.runOnce(signal), settings.pollInterval * 1000, stop.signal),
      senderLoop(sender, stop.signal),
      commandLoop(new Commands(telegram, store, settings.chatId), stop.signal),
    ];
    const results = await Promise.allSettled(loops.map(async loop => {
      try { await loop; } catch (error) { stop.abort(); throw error; }
    }));
    return results.some(result => result.status === 'rejected') ? 1 : 0;
  } catch (error) {
    if (stop.signal.aborted) return 0;
    console.error('Monitor failed:', safeError(error)); return 1;
  } finally {
    stop.abort(); store?.close();
    process.off('SIGINT', shutdown); process.off('SIGTERM', shutdown);
  }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) process.exitCode = await main();
