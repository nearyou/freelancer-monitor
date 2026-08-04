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
| Profile Completed | 5 | Public `Profile complete` marker; all or nothing |
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

No Freelancer access token is used. Anonymous API responses omit their expanded `users` map, so
the monitor retrieves employer trust signals from each matching project's public, server-rendered
detail page. It parses semantic labels rather than visual CSS classes. To keep this polite, only
projects that first pass your word/skill filters are enriched, and
`DETAIL_REQUEST_DELAY_SECONDS` spaces out page requests.

The public page's `Profile complete` marker is shown as `Profile Completed` and contributes five
points by default. It is not treated as identity verification. If Freelancer omits the client
section or a required field, that project is deferred with backoff instead of receiving a
misleading trust score.

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

The monitor uses the active-project API for discovery and public project pages for trust details.
Automated access remains subject to Freelancer's terms and robots/rate-limit policies.
Projects posted at least one hour ago are marked as processed without page enrichment or a
Telegram notification. Projects whose submission time is unavailable remain eligible.

## Performance

The monitor minimizes local and network overhead without relaxing service limits:

- one pooled HTTPS session is reused for Freelancer API and detail-page requests;
- gzip/deflate responses are decompressed automatically;
- only the small client-information HTML section is parsed;
- project filters are normalized once per poll;
- seen-project checks and filter-miss writes are batched in SQLite;
- repeated employers within one poll reuse parsed trust details when an owner ID is available;
- an unavailable or changed public client panel defers only that project, using a
  five-minute exponential retry cooldown capped at one hour;
- Freelancer request spacing and Telegram flood-control limits remain enforced.
