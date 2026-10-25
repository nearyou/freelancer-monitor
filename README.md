# Freelancer Monitor

A TypeScript / Node.js bot that polls the Freelancer API, scores project owners, and sends matching
projects to Telegram. Python is no longer required. Runtime dependencies are built into Node.js:
HTTP fetch, SQLite, and timers. TypeScript and Node type definitions are development dependencies.

## Run locally

Install **Node.js 24.x** (see `.nvmrc`), then:

```sh
npm ci
npm run build
```

Copy `.env_example` to `.env` (`cp .env_example .env` on Linux, `Copy-Item .env_example .env` in
PowerShell). Enter your Telegram bot token and chat ID. Keep credentials only in the ignored `.env`.

```sh
npm run check-config
npm start
```

`--check-config` validates configuration without network requests or opening the database.
`npm start -- --once` fetches one batch and attempts all currently ready notifications. It returns
exit code 1 if notifications remain queued for retry. Stop continuous mode with Ctrl+C.
SIGINT and SIGTERM cancel HTTP requests and waits and close SQLite after the loops finish.

The start command enables the operating system certificate store with `--use-system-ca`.
Certificate verification remains enabled. Direct launches should use the same flag:

```sh
node --use-system-ca dist/src/main.js
```

## Upgrade from Python

1. Stop the old Python bot and disable its service before starting Node. Run only one process for
   the same bot/database, including across your computer and VPS.
2. Back up `.env` and SQLite. Use SQLite's backup facility if the source is running; copying only
   `.db` can omit writes in `-wal`. Prefer a clean shutdown before copying.
3. Install Node.js 24.x, pull this version, run `npm ci` and `npm run build`.
4. Keep the same `STATE_DB_PATH`. Relative paths resolve from the working directory, as before.
   The schema and Unix-second retry timestamps are unchanged. Skills, keywords, thresholds, weights,
   Telegram update offsets, recent project IDs, and pending alerts are read directly.
5. Replace your service's Python command with the Node command above, then start it.

No export/reset is needed. Existing queued messages keep their original contents and scores.
Tests open a database produced by the previous Python implementation and verify continued use.
Legacy `SCORE_*`, `SKILLS`, `SEARCH_WORDS`, `FILTER_MATCH_MODE`, and HTML-scraping variables are ignored.
Keep filters and scoring settings in Telegram; runtime settings remain in `.env`.

## VPS deployment (Ubuntu/Debian with systemd)

Install Node.js 24.x and npm first. Confirm `node --version` reports v24 and check the executable
location with `command -v node`. The supplied service expects `/usr/bin/node`; adjust if needed.

```sh
sudo apt update
sudo apt install -y git ca-certificates
sudo useradd --system --create-home --home-dir /opt/freelancer-monitor --shell /usr/sbin/nologin freelancer
sudo -u freelancer git clone https://github.com/nearyou/freelancer-monitor.git /opt/freelancer-monitor/app
cd /opt/freelancer-monitor/app
sudo -u freelancer npm ci
sudo -u freelancer npm run build
sudo -u freelancer cp .env_example .env
sudo -u freelancer chmod 600 .env
sudo -u freelancer nano .env
```

Set `STATE_DB_PATH=/opt/freelancer-monitor/freelancer_monitor.db` to keep data outside the checkout.
If migrating, copy the old database there before starting and give the `freelancer` user ownership
of the database and directory. A new database starts with empty filters and a threshold of 65.
You need outbound HTTPS to Freelancer and Telegram, not an incoming port or domain.

```sh
sudo -u freelancer npm run check-config
sudo cp deploy/freelancer-monitor.service /etc/systemd/system/freelancer-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now freelancer-monitor
sudo systemctl status freelancer-monitor
sudo journalctl -u freelancer-monitor -f
```

The service runs Node directly, starts on reboot, and restarts on failure. Exit the log viewer with
Ctrl+C; the service continues. Verify `/show_skills` and `/show_score` in Telegram after migration.

To update:

```sh
sudo systemctl stop freelancer-monitor
cd /opt/freelancer-monitor/app
sudo -u freelancer git pull --ff-only origin main
sudo -u freelancer npm ci
sudo -u freelancer npm run build
sudo systemctl start freelancer-monitor
```

## Workflow and delivery

Three independent async loops handle project polling, Telegram commands, and notification delivery.
Network requests and waits yield to the other loops; short SQLite operations run synchronously.
Polling starts every **15 seconds** by default. Polls never overlap and slow requests skip missed
ticks instead of causing catch-up bursts. API failures preserve the previous batch's history.

The active-project API supplies projects and inline owner information in one response; no HTML
scraping or per-project enrichment is used. Skill names resolve to Freelancer job IDs in one catalog
request, cached until the list changes. All IDs are sent as repeated `jobs[]` values. Search words
and skill names form the text query. `FREELANCER_LANGUAGES` optionally filters languages.
Other parameters in `FREELANCER_API_URL` (budget, project types, URL skill IDs) are preserved;
nonempty runtime skills/languages/search terms override the corresponding URL filters. Page size,
owner/detail flags, and newest-first sorting are set by the bot.

The local filter accepts a keyword OR skill match. Empty groups are disabled; with both empty, all
returned projects proceed to scoring. Skills currently require exact catalog names: `/add_skill`
saves text without API validation, so remove unrecognized entries using `/delete_skill`.

Projects posted at least one hour ago are marked processed without notification. Missing timestamps
remain eligible. Missing core owner trust fields defer that project with backoff (five minutes,
doubling to one hour). Optional identity/phone/email states are required only when their weights are
positive. A missing field displays a question mark; a false field earns zero points. Compact unrated
owners with an explicit zero rating and no review count are treated as having zero reviews.

Qualifying alerts and duplicate-protection records are saved atomically. Only the newest completed
batch's history is retained, plus projects with pending alerts. A successful empty batch clears old
history; a failed poll does not. Off-feed owner-data retries are pruned. Projects that leave the feed
and later return may notify again if their prior alert was delivered. The bot fetches one page per
poll and cannot recover projects that leave that page between polls.

One sender delivers every alert and command reply, spacing attempts by at least **3.1 seconds** by
default (values below 3 are rejected). Command replies have priority. Telegram 429 cooldowns are
respected, with up to five immediate retries; exhausted cooldowns also apply to the next message.
Other delivery failures remain queued with exponential backoff from 30 seconds to one hour.
Shutdown retains unsent entries. If Telegram accepts a message but its response or the local
acknowledgement is lost, retrying may deliver it twice.

Alerts show title, listing/payment type, budget, JST posting time, country, rounded rating, reviews,
and join date. Verification rows use inline fixed-width text, with identity/email, payment/profile,
and deposit/phone columns. Narrow screens or large fonts may wrap a row. The project link is an
inline button. Queued alerts retain their captured message and score after settings change.

## Telegram commands

Create your bot with BotFather and put its token and authorized chat ID in `.env`. On continuous
startup the bot registers **23 commands** with short descriptions in that chat. Type `/` to select
one. Registration retries after 60 seconds on failure. `--once` and `--check-config` do not register
the menu. Other chats cannot change settings. Commands requiring values show usage when invoked
without them; scoring setters also show their current value.

```text
/show_skills
/add_skill Python
/delete_skill Python
/show_search_words
/add_search_word API integration
/delete_search_word API integration
/show_score
/show_threshold
/set_threshold 60
/set_payment_verified 20
/set_deposit_made 15
/set_profile_completed 5
/set_identity_verified 10
/set_phone_verified 5
/set_email_verified 5
/set_rating_max 10
/set_reviews_max 15
/set_reviews_cap 50
/set_member_age_max 10
/set_member_age_max_days 100
/clear_history
/help
/start
```

The legacy `/set_score <setting> <value>` syntax is also accepted. These examples illustrate the
commands, not a required configuration. Changes persist in SQLite and apply to newly processed
projects on the next poll. `/clear_history` reconsiders completed projects still in the feed and may
resend alerts; it preserves pending alerts, filters, scores, and command offsets.

## Scoring

Default maximum and threshold are both **65**. All weights/caps are configurable through Telegram.
Weights are nonnegative finite points; zero disables a signal. Caps are positive safe integers.
The threshold cannot exceed the sum of weights. Lower it before reducing weights below it.

| Signal | Default maximum | Formula |
|---|---:|---|
| Payment verified | 20 | All or nothing |
| Deposit made | 15 | All or nothing |
| Profile completed | 5 | All or nothing |
| Reviews | 15 | `reviews_max * min(log(1 + count) / log(1 + reviews_cap), 1)` |
| Account age | 10 | Same logarithmic curve in whole UTC days; default cap 100 days |
| Identity verified | 0 | All or nothing |
| Phone verified | 0 | All or nothing |
| Email verified | 0 | All or nothing |
| Rating | 0 | `rating_max * clamp(rating, 0, 5) / 5` |

Default review cap is 50. The total is rounded to two decimal places before comparison. Displayed
stars are rounded to an integer, but scoring uses the original rating. Scores rank owner trust
signals; they do not guarantee that a project is genuine.

## Tests

```sh
npm test
```

This compiles strict TypeScript and runs Node's built-in test runner. Tests use temporary databases
and mocked HTTP; they never send Telegram messages or access the production database. Coverage
includes API parsing/filter overrides, Python score/message parity, legacy SQLite migration,
commands and authorization, durable queue recovery, retry timing, nonblocking polling, CLI exit
codes, offline configuration checks, and process shutdown. Python-generated fixtures contain only
synthetic data and need no Python interpreter to run.
