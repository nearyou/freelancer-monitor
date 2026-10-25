import { escapeHtml as html, type Project } from './models.js';
import { retryAfter, sleep, type Fetcher, type Sleeper } from './http.js';

const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const dateText = (date: Date) => `${months[date.getUTCMonth()]} ${date.getUTCDate()}, ${date.getUTCFullYear()}`;
const pad = (n: number) => String(n).padStart(2, '0');
export function formatProject(project: Project): string {
  const owner = project.employer;
  const joined = owner.member_since ? dateText(owner.member_since) : 'Unknown';
  const jst = project.submitted_at ? new Date(project.submitted_at.getTime() + 9 * 3600000) : null;
  const posted = jst ? `${dateText(jst)} ${pad(jst.getUTCHours())}:${pad(jst.getUTCMinutes())} JST` : 'Unknown';
  let budget = project.budget_min === null && project.budget_max === null ? 'Unknown'
    : project.budget_min === project.budget_max ? String(project.budget_min)
    : `${project.budget_min ?? '?'}–${project.budget_max ?? '?'}`;
  if (budget !== 'Unknown') budget = `${budget} ${project.currency_code}`.trim() + (project.hourly ? ' / hour' : '');
  const status = (value: boolean | null) => value === null ? '❔' : value ? '✅' : '⛔';
  const row = (left: string, a: boolean | null, right: string, b: boolean | null) => `<code>${status(a)} ${left.padEnd(17)}  ${status(b)} ${right}</code>`;
  return [
    `<blockquote><b>${html(project.title)}</b></blockquote>`,
    `<b>Listing type:</b> ${html(project.listing_types.join(' • ') || 'Standard')}`,
    `<b>Payment Type:</b> ${project.hourly ? 'Hourly rate' : 'Fixed price'}`,
    `<b>Budget:</b> ${html(budget)}`, `<b>Posted:</b> ${html(posted)}`,
    '━━━━━━━━━━━━━━━━━━━━━━━━━━━',
    `🏠 ${html(owner.country || 'Unknown')}\t ⭐ ${Math.round(Math.min(Math.max(owner.rating, 0), 5))}\t 💬 ${owner.review_count}\t 📅 ${html(joined)}`,
    row('Identity verified', owner.identity_verified, 'Email verified', owner.email_verified),
    row('Payment verified', owner.payment_verified, 'Profile completed', owner.profile_completed),
    row('Deposit made', owner.deposit_made, 'Phone verified', owner.phone_verified),
  ].join('\n');
}

export type BotCommand = readonly [string, string];
export interface Update { update_id: number; message?: { chat?: { id?: number | string }; text?: string } }
export interface TelegramAPI {
  setCommands(commands: readonly BotCommand[], signal?: AbortSignal): Promise<void>;
  getUpdates(offset?: number, signal?: AbortSignal): Promise<Update[]>;
  sendMessage(text: string, buttonUrl?: string | null, signal?: AbortSignal): Promise<void>;
}
type ApiResult = { ok?: boolean; result?: unknown; error_code?: number; parameters?: { retry_after?: number } };

export class TelegramClient implements TelegramAPI {
  private lastAttempt = -Infinity;
  private blockedUntil = 0;
  constructor(
    private readonly token: string, private readonly chatId: string,
    private readonly interval = 3.1, private readonly request: Fetcher = fetch,
    private readonly wait: Sleeper = sleep, private readonly now = () => performance.now(),
  ) {}
  private async call(method: string, params: Record<string, unknown>, signal?: AbortSignal): Promise<{ payload: ApiResult; response: Response }> {
    const body = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined).map(([key, value]) => [key, typeof value === 'object' ? JSON.stringify(value) : String(value)]));
    const response = await this.request(`https://api.telegram.org/bot${this.token}/${method}`, {
      method: 'POST', body,
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(30000)]) : AbortSignal.timeout(30000),
    });
    let payload: ApiResult = {};
    try { payload = await response.json() as ApiResult; }
    catch { if (response.status !== 429) throw new Error(`Telegram ${method}: invalid response (HTTP ${response.status})`); }
    return { payload, response };
  }
  async setCommands(commands: readonly BotCommand[], signal?: AbortSignal): Promise<void> {
    const id = /^-?\d+$/.test(this.chatId) ? Number(this.chatId) : this.chatId;
    const { payload, response } = await this.call('setMyCommands', {
      commands: commands.map(([command, description]) => ({ command, description })), scope: { type: 'chat', chat_id: id },
    }, signal);
    if (!response.ok || !payload.ok || payload.result !== true) throw new Error('Telegram rejected command menu registration.');
  }
  async getUpdates(offset?: number, signal?: AbortSignal): Promise<Update[]> {
    const { payload, response } = await this.call('getUpdates', { offset, timeout: 2, allowed_updates: ['message'] }, signal);
    if (!response.ok || !payload.ok) throw new Error(`Telegram getUpdates failed (HTTP ${response.status}).`);
    return Array.isArray(payload.result) ? payload.result as Update[] : [];
  }
  async sendMessage(text: string, buttonUrl?: string | null, signal?: AbortSignal): Promise<void> {
    const params: Record<string, unknown> = {
      chat_id: this.chatId, text, parse_mode: 'HTML', disable_web_page_preview: true,
    };
    if (buttonUrl) params.reply_markup = { inline_keyboard: [[{ text: 'View on Freelancer', url: buttonUrl }]] };
    for (let attempt = 0; attempt <= 5; attempt++) {
      signal?.throwIfAborted();
      await this.wait(Math.max(0, this.lastAttempt + Math.max(this.interval, 3) * 1000 - this.now(), this.blockedUntil - this.now()), signal);
      let result: Awaited<ReturnType<TelegramClient['call']>>;
      try { result = await this.call('sendMessage', params, signal); }
      finally { this.lastAttempt = this.now(); }
      const { payload, response } = result;
      if (response.status === 429 || payload.error_code === 429) {
        const retry = payload.parameters?.retry_after;
        const cooldown = typeof retry === 'number' && Number.isFinite(retry) && retry >= 0
          ? retry * 1000 : retryAfter(response.headers.get('retry-after'), this.interval * 1000);
        this.blockedUntil = this.now() + Math.max(cooldown, this.interval * 1000);
        if (attempt === 5) throw new Error('Telegram rate limit: notification remains queued.');
        continue;
      }
      if (!response.ok || !payload.ok) throw new Error(`Telegram sendMessage failed (HTTP ${response.status}).`);
      return;
    }
  }
}
