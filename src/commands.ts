import { DEFAULT_SCORE, SCORE_KEYS, maximum, numeric, type ScoreKey } from './config.js';
import { escapeHtml as html } from './models.js';
import { Store, type TermKind } from './state.js';
import type { BotCommand, TelegramAPI } from './telegram.js';

export const SCORE_COMMANDS: Record<ScoreKey, string> = {
  payment_verified: 'Set payment verification points', deposit_made: 'Set deposit points',
  profile_completed: 'Set completed profile points', identity_verified: 'Set identity verification points',
  phone_verified: 'Set phone verification points', email_verified: 'Set email verification points',
  rating_max: 'Set maximum rating points', reviews_max: 'Set maximum review points',
  reviews_cap: 'Set review count for maximum points', member_age_max: 'Set maximum account age points',
  member_age_max_days: 'Set account age in days for maximum points',
};
export const BOT_COMMANDS: readonly BotCommand[] = [
  ['show_skills', 'Show monitored skills'], ['add_skill', 'Add a monitored skill'], ['delete_skill', 'Delete a monitored skill'],
  ['show_search_words', 'Show search keywords'], ['add_search_word', 'Add a search keyword'], ['delete_search_word', 'Delete a search keyword'],
  ['show_score', 'Show owner scoring settings'],
  ...Object.entries(SCORE_COMMANDS).map(([key, description]): BotCommand => [`set_${key}`, description]),
  ['show_threshold', 'Show the minimum project score'], ['set_threshold', 'Change the minimum project score'],
  ['clear_history', 'Clear history; projects may notify again'], ['help', 'Show commands and usage'], ['start', 'Show getting-started commands'],
];

export class Commands {
  private menuRegistered = false;
  private menuRetryAt = 0;
  constructor(private readonly telegram: TelegramAPI, private readonly store: Store, private readonly chatId: string, private readonly now = () => performance.now()) {}
  async pollOnce(signal?: AbortSignal): Promise<number> {
    if (!this.menuRegistered && this.now() >= this.menuRetryAt) {
      try { await this.telegram.setCommands(BOT_COMMANDS, signal); this.menuRegistered = true; }
      catch (error) {
        if (signal?.aborted) throw error;
        console.warn('Command menu registration failed; retrying in 60 seconds.');
        this.menuRetryAt = this.now() + 60000;
      }
    }
    const offset = this.store.getSetting('telegram_update_offset');
    const updates = await this.telegram.getUpdates(offset === undefined ? undefined : Number(offset), signal);
    let handled = 0;
    for (const update of updates) {
      if (!Number.isSafeInteger(update.update_id)) continue;
      const savedOffset = Number(this.store.getSetting('telegram_update_offset') ?? 0);
      if (update.update_id < savedOffset) continue;
      const message = update.message;
      if (String(message?.chat?.id) === this.chatId && typeof message?.text === 'string' && message.text.startsWith('/')) {
        this.store.enqueueReply(this.handle(message.text), update.update_id); handled++;
      } else this.store.setSetting('telegram_update_offset', String(update.update_id + 1));
    }
    return handled;
  }
  handle(text: string): string {
    const [first = '', ...rest] = text.trim().split(/\s+/);
    const command = first.split('@')[0]!.toLowerCase();
    const argument = rest.join(' ');
    const termCommands: Record<string, [TermKind, string, boolean]> = {
      '/add_skill': ['skill', 'skill', true], '/delete_skill': ['skill', 'skill', false],
      '/add_search_word': ['search_word', 'search word', true], '/delete_search_word': ['search_word', 'search word', false],
    };
    const term = termCommands[command];
    if (term) {
      const [kind, label, add] = term;
      if (!argument) return `Usage: ${html(first)} &lt;${label}&gt;`;
      const changed = add ? this.store.addTerm(kind, argument) : this.store.deleteTerm(kind, argument);
      return `${add ? (changed ? 'Added' : 'Already exists') : (changed ? 'Deleted' : 'Not found')}: <b>${html(argument)}</b>`;
    }
    if (command === '/show_skills' || command === '/show_search_words') {
      const skills = command === '/show_skills';
      const terms = this.store.terms(skills ? 'skill' : 'search_word');
      return `<b>${skills ? 'Skills' : 'Search words'}</b>\n${terms.length ? terms.map(t => `• ${html(t)}`).join('\n') : 'None configured.'}`;
    }
    if (command === '/show_threshold') {
      const { config, threshold } = this.store.scoring();
      return `Score threshold: <b>${threshold}</b> / ${maximum(config)} maximum`;
    }
    if (command === '/show_score') {
      const { config, threshold } = this.store.scoring();
      return [
        '<b>Owner scoring</b>', `Threshold: <b>${threshold}</b> / ${maximum(config)} maximum`,
        ...SCORE_KEYS.map(key => `<code>${key}</code>: ${config[key]} — /set_${key}`), '',
        'Weights are points; 0 disables a signal.', 'reviews_cap: review count for maximum points.',
        'member_age_max_days: account age in days for maximum points.', 'rating_max: maximum points at 5 stars (linear).',
        '', 'Use a /set_ command followed by a value.', '/set_threshold &lt;score&gt;',
        ...(threshold > maximum(config) ? ['Threshold exceeds maximum; use /set_threshold to enable alerts.'] : []),
      ].join('\n');
    }
    const setting = command.slice('/set_'.length);
    if (command === '/set_score' || (command.startsWith('/set_') && Object.hasOwn(DEFAULT_SCORE, setting))) {
      let name = setting;
      let value = argument;
      if (command === '/set_score') {
        if (rest.length !== 2) return 'Usage: /set_score &lt;setting&gt; &lt;value&gt;. Use /show_score for settings.';
        name = rest[0]!.toLowerCase(); value = rest[1]!;
      } else if (rest.length !== 1) {
        return `Usage: ${html(command)} &lt;value&gt;\n${SCORE_COMMANDS[name as ScoreKey]}. Current value: <b>${this.store.scoring().config[name as ScoreKey]}</b>.`;
      }
      try {
        const { config, threshold } = this.store.setScore(name, value);
        return `Set <code>${html(name)}</code> to <b>${config[name as ScoreKey]}</b>.\nThreshold: ${threshold} / ${maximum(config)} maximum. Applies to the next poll.`;
      } catch (error) { return html((error as Error).message); }
    }
    if (command === '/set_threshold') {
      try { const value = numeric(argument); this.store.setThreshold(value); return `Score threshold set to <b>${value}</b>.`; }
      catch (error) { return html((error as Error).message); }
    }
    if (command === '/clear_history') return `Notification history cleared: <b>${this.store.clearHistory()}</b> records deleted.`;
    if (command === '/help' || command === '/start') return [
      '<b>Filter commands</b>', '/show_skills', '/add_skill &lt;skill&gt;', '/delete_skill &lt;skill&gt;', '',
      '/show_search_words', '/add_search_word &lt;word&gt;', '/delete_search_word &lt;word&gt;', '', '/show_score',
      ...Object.entries(SCORE_COMMANDS).map(([key, description]) => `/set_${key} &lt;value&gt; — ${description}`),
      '/show_threshold', '/set_threshold &lt;score&gt;', '', '/clear_history',
    ].join('\n');
    return 'Unknown command. Use /help to see available commands.';
  }
}
