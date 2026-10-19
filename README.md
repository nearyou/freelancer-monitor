# Freelancer Monitor

A small Python bot that polls new Freelancer projects, filters them by search words and skills,
scores the employer's trust signals, and sends only projects above a configurable threshold to
Telegram.

## Trust score

The default score is out of 65. Manage every owner-score weight, cap, and threshold in Telegram
with `/show_score`, `/set_score <setting> <value>`, and `/set_threshold <score>`.

| Signal | Default maximum | Calculation |
|---|---:|---|
| Payment verified | 20 | All or nothing |
| Deposit made | 15 | All or nothing |
| Profile Completed | 5 | API `owner_info.status.profile_complete`; all or nothing |
| Identity verified | 0 (disabled) | All or nothing |
| Phone verified | 0 (disabled) | All or nothing |
| Email verified | 0 (disabled) | All or nothing |
| Overall rating | 0 (disabled) | Linear: rating / 5 × configured maximum |
| Number of reviews | 15 | Logarithmic, capped at 50 reviews |
| Member since | 10 | Logarithmic age in whole days, capped at 100 days |

The logarithmic review curve gives useful credit to the first few reviews without allowing very old,
high-volume accounts to dominate the score. Settings persist in SQLite and apply without a restart.
The maximum score is the sum of enabled weights; caps determine when reviews and account age earn
their full points. A weight of zero disables that signal. Caps must be positive whole numbers.
The displayed star rating is rounded, but scoring uses the original API rating.

This is a risk-ranking aid, not a guarantee that a project is genuine. Always inspect the brief and
keep communication and payments on-platform.

## Setup

Requires Python 3.10 or newer. Runtime dependencies are `requests`, used for pooled HTTPS
connections and safe retries, and `truststore`, used to verify HTTPS through the operating system's
native certificate store. Certificate verification is never disabled.

```powershell
Copy-Item .env_example .env
# Edit .env with your Telegram token and chat ID.
python -m pip install -e .
python -m freelancer_monitor --check-config
python -m freelancer_monitor --once
```

Run continuously with:

```powershell
python -m freelancer_monitor
```

Stop it with `Ctrl+C`. Deduplication keeps processed IDs from the latest successful project poll in
the SQLite file configured by `STATE_DB_PATH`. Each completed poll removes older history and
off-feed employer-data retry records. A failed poll leaves the previous history intact; a successful
empty response clears it. Existing accumulated history is trimmed on the first completed poll.

Pending notifications and their duplicate-protection records are retained separately until delivery,
even after leaving the fetched batch, and survive restarts. Their old history records are removed
on the next completed poll after delivery. Thus processed history stays bounded by the latest batch
plus pending alerts, rather than growing forever. If a delivered project disappears from one batch
and later returns, it can be notified again if it still passes the age, filter, and score checks.
Use one running monitor process per bot/database.

Project polling runs in its own worker every **15 seconds** by default, independently of message
delivery and Telegram command handling. Set `POLL_INTERVAL_SECONDS=15` in an existing `.env` if it
still specifies 60. Polls do not overlap; a slow API request skips missed ticks instead of causing
a burst of catch-up requests.

Matching projects enter a persistent notification queue. One sender delivers all project alerts and
command replies, maintaining the shared Telegram rate limit. Replies take priority over waiting
project alerts, and settings changes take effect on the next poll. Alerts already queued retain the
message and score captured when they matched, even if filters change or they leave the API feed.

`--once` polls once and attempts delivery of ready queue entries before exiting. It returns a nonzero
exit code if notifications remain in retry cooldown; a later run resumes them. Shutdown interrupts
rate-limit waits and preserves pending alerts. Already delivered queue entries are removed, but a
crash or lost response after Telegram accepts a message and before SQLite records delivery can
still cause a duplicate on retry.

## Telegram setup

1. Create a bot with `@BotFather` and put its token in `TELEGRAM_BOT_TOKEN`.
2. Send your bot a message.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the chat ID into
   `TELEGRAM_CHAT_ID`.

Telegram sends are explicitly spaced by `TELEGRAM_SEND_INTERVAL_SECONDS`, which defaults to 3.1
seconds so the bot remains below the stricter 20-messages-per-minute group limit. Values below 3.0
seconds are rejected. If Telegram still responds with flood-control status `429`, the sender waits
for Telegram's `retry_after` duration and retries up to five times.

No Freelancer access token is used. The active-project API is requested with `owner_info=true`,
`webapp=1`, and `compact=true`. Each project's inline `owner_info` supplies the employer's country,
registration date, reputation, and verification states in the same response. There are no HTML
requests or per-project enrichment delays. Existing `DETAIL_REQUEST_DELAY_SECONDS` values are ignored.

Telegram displays verification states in three paired rows: identity/email, payment/profile,
and deposit/phone. Each row uses inline fixed-width text with a padded first column, keeping the
columns aligned without a preformatted block's Copy header or indentation. Narrow screens or
large text sizes may still wrap long rows. Identity,
phone, and email have zero score weight by default; enable them with `/set_score`. Missing
optional verification states use a question mark. When their weight is positive, missing states
defer the project for an employer-data retry. Payment and deposit map directly to their
API names, replacing the old HTML label swap; scores can therefore differ for employers with only
one of those two verifications.

If required scoring data is missing, only that project is deferred with backoff. Compact responses
omit review counts for unrated employers; these are treated as zero when the API explicitly reports
a zero rating. An API error or malformed response fails the poll instead of looking like an empty feed.

## Filters

Skills, search words, and all scoring settings are managed through Telegram and stored in SQLite.
A new database starts with no skill or search-word filters, the default weights above, and a
threshold of 65. Add filters with `/add_skill` and `/add_search_word`; existing database filters
are preserved on upgrade.

Legacy `SKILLS`, `SEARCH_WORDS`, and `SCORE_*` environment variables are ignored and can be removed
from an existing `.env`. If your filters existed only in `.env`, enter them through Telegram.
An existing database keeps its saved threshold and gains the default weights once. If you previously
customized weights in `.env`, enter them through Telegram after upgrading. `/show_score` warns if
the saved threshold exceeds the new maximum; adjust the weights or threshold before expecting alerts.

Send these commands to the bot from the chat configured by `TELEGRAM_CHAT_ID`:

When running continuously, the bot registers all 13 commands with short descriptions in that chat.
Type `/` to see Telegram's command suggestions. Select a command, then add its arguments where
needed (for example `/add_skill Python`). Menu registration retries after 60 seconds if unavailable;
project polling and message delivery continue. `--once` and `--check-config` do not register the menu.

```text
/show_skills
/add_skill Python
/delete_skill Python

/show_search_words
/add_search_word API integration
/delete_search_word API integration

/show_threshold
/set_threshold 65

/show_score
/set_score payment_verified 20
/set_score deposit_made 15
/set_score profile_completed 5
/set_score identity_verified 10
/set_score phone_verified 5
/set_score email_verified 5
/set_score rating_max 10
/set_score reviews_max 15
/set_score reviews_cap 50
/set_score member_age_max 10
/set_score member_age_max_days 100

/clear_history
/help
```

Commands from every other chat ID are ignored. Changes apply to the next project poll without a
restart, with one consistent scoring configuration used for each batch. The `/set_score` examples
above demonstrate every setting; they enable the optional signals and increase the maximum to 95.
Use `/show_score` to see your current values. The threshold must be between zero and the current
maximum. Before reducing weights below your threshold, lower the threshold first with `/set_threshold`.
Invalid settings leave the saved configuration unchanged.

Score changes affect newly processed projects; they do not rescore processed IDs or queued alerts.
Use `/clear_history` to reconsider processed projects still in the feed (which can resend prior alerts).

The local filter accepts a project when it matches at least one search word OR one skill.
With both groups empty, it accepts every project returned by the API for scoring. The old
`FILTER_MATCH_MODE` environment variable is ignored and can be removed from existing `.env` files.

`/clear_history` deletes processed-project history except for projects with pending notifications.
It preserves the queue, skills, search words, threshold, command offset, and other settings.
Previously completed projects may be processed and notified again after clearing history.

The monitor uses the active-project API for both discovery and employer trust details.
The bot automatically resolves every skill registered through `/add_skill` to Freelancer's skill
ID. It refreshes that mapping when the registered list changes, then sends all IDs in one project
request, sorted by `submitdate`. `FREELANCER_LANGUAGES` optionally limits the feed by language code.
Registered search words and skill names are also combined into the request; the existing local
filter remains the final correctness check.
`FREELANCER_API_URL` can also contain a full query string. URL filters such as `jobs[]`,
`languages[]`, `project_types[]=fixed`, and `max_price=1500` are retained. Non-empty runtime skills,
languages, and search terms override the corresponding URL filters; `FREELANCER_PAGE_LIMIT` always
controls the page size. Required owner/detail flags and submission-date sorting are set by the client.
The default URL does not hard-code the example's skills, languages, budget, or fixed-price restriction.
Automated access remains subject to Freelancer's terms and robots/rate-limit policies.
Projects posted at least one hour ago are marked as processed without a
Telegram notification. Projects whose submission time is unavailable remain eligible.

## Performance

The monitor minimizes local and network overhead without relaxing service limits:

- one pooled HTTPS session is reused for Freelancer API requests;
- gzip/deflate responses are decompressed automatically;
- one project API response supplies project and employer data for the whole page;
- project filters are normalized once per poll;
- seen-project checks and filter-miss writes are batched in SQLite;
- transient API errors use bounded retries and respect `Retry-After`;
- incomplete employer data defers only that project, using a five-minute exponential retry cooldown
  capped at one hour; these data retries require the project to remain in the feed;
- failed notifications remain queued and retry independently of the feed, with exponential backoff
  starting at 30 seconds and capped at one hour;
- project polling, command handling, and queue delivery use separate workers/connections;
- Telegram send spacing and flood-control limits remain enforced.

The feed still uses one page per poll. Increase `FREELANCER_PAGE_LIMIT` or narrow filters for a busy
feed; the monitor does not guarantee recovery of projects that have already left that page.

## Tests

After installing the package, run `python -m unittest discover -s tests -v`.
The API fixture in `tests/fixtures/active_projects.json` is based on an anonymous live response,
with identifying project text and IDs replaced. Tests cover compact owner data, verification
mapping, missing fields, query parameters, 15-second scheduling, queue persistence, concurrent polling,
shared Telegram send limits, notification failure isolation, duplicate suppression, Telegram scoring
configuration, migration, validation, persistence, and scoring changes during polling
without sending messages to Telegram.
