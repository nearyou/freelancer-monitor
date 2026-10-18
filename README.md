# Freelancer Monitor

A small Python bot that polls new Freelancer projects, filters them by search words and skills,
scores the employer's trust signals, and sends only projects above a configurable threshold to
Telegram.

## Trust score

The default score is out of 65. Employer rating is displayed as a rounded integer in Telegram but
does not affect the score:

| Signal | Default maximum | Calculation |
|---|---:|---|
| Payment verified | 20 | All or nothing |
| Deposit made | 15 | All or nothing |
| Profile Completed | 5 | API `owner_info.status.profile_complete`; all or nothing |
| Overall rating | Not scored | Display-only, rounded to a whole star |
| Number of reviews | 15 | Logarithmic, capped at 50 reviews |
| Member since | 10 | Logarithmic age in whole days, capped at 100 days |

The logarithmic review curve gives useful credit to the first few reviews without allowing very old,
high-volume accounts to dominate the score. Every weight and cap is configurable in `.env`.

This is a risk-ranking aid, not a guarantee that a project is genuine. Always inspect the brief and
keep communication and payments on-platform.

## Setup

Requires Python 3.10 or newer. Runtime dependencies are `requests`, used for pooled HTTPS
connections and safe retries, and `truststore`, used to verify HTTPS through the operating system's
native certificate store. Certificate verification is never disabled.

```powershell
Copy-Item .env_example .env
# Edit .env with your Telegram token, chat ID, filters, threshold, and score weights.
python -m pip install -e .
python -m freelancer_monitor --check-config
python -m freelancer_monitor --once
```

Run continuously with:

```powershell
python -m freelancer_monitor
```

Stop it with `Ctrl+C`. Already processed project IDs are stored in the SQLite file configured by
`STATE_DB_PATH`, so restarts do not send duplicate messages.

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

Telegram displays payment, deposit, profile, identity, phone, and email verification. Identity,
phone, and email are display-only; score weights and the 65-point maximum are unchanged. Missing
optional verification states use a question mark. Payment and deposit now map directly to their
API names, replacing the old HTML label swap; scores can therefore differ for employers with only
one of those two verifications.

If required scoring data is missing, only that project is deferred with backoff. Compact responses
omit review counts for unrated employers; these are treated as zero when the API explicitly reports
a zero rating. An API error or malformed response fails the poll instead of looking like an empty feed.

## Filters

Skills, search words, and the notification threshold are stored in the SQLite database. On the first
startup after this feature is installed, existing `SKILLS`, `SEARCH_WORDS`, and `SCORE_THRESHOLD`
environment values are imported once. SQLite is authoritative after that migration.

Send these commands to the bot from the chat configured by `TELEGRAM_CHAT_ID`:

```text
/show_skills
/add_skill Python
/delete_skill Python

/show_search_words
/add_search_word API integration
/delete_search_word API integration

/show_threshold
/set_threshold 65

/clear_history
/help
```

Commands from every other chat ID are ignored. Changes apply to the next project poll without a
restart. `FILTER_MATCH_MODE=any` allows either a search-word or skill match; `all` requires at least
one match from each non-empty group. An empty group is disabled.

`/clear_history` deletes only processed-project notification history. It does not change skills,
search words, threshold, command offset, or other settings. Active projects may be processed and
notified again after clearing history.

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
- incomplete employer data or a failed Telegram notification defers only that project, using a
  five-minute exponential retry cooldown capped at one hour; retries occur if it remains in the feed;
- Telegram send spacing and flood-control limits remain enforced.

The feed still uses one page per poll. Increase `FREELANCER_PAGE_LIMIT` or narrow filters for a busy
feed; the monitor does not guarantee recovery of projects that have already left that page.

## Tests

After installing the package, run `python -m unittest discover -s tests -v`.
The API fixture in `tests/fixtures/active_projects.json` is based on an anonymous live response,
with identifying project text and IDs replaced. Tests cover compact owner data, verification
mapping, missing fields, query parameters, notification failure isolation, and duplicate suppression
without sending messages to Telegram.
