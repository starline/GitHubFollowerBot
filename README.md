# GitHub Follower Bot

Local bot that discovers GitHub users and follows them via the API — with Cursor-focused discovery, profile filters, and rate-limit controls.

![GitHubFollowerBot preview](assets/preview.jpg)

## Requirements

- Python 3.10+
- A [classic PAT](https://github.com/settings/tokens) with scopes `read:user` and `user:follow`

## Quick start

```bash
./run.sh
```

The script creates a `.venv`, installs dependencies, copies `.env.example` → `.env` if needed, and starts `python -m bot`. On first run it asks for `GITHUB_TOKEN` if the value is empty.

Keep the process running (`tmux`, systemd, …). Logs go to stderr and `data/github_follow.log`.

Stop with `Ctrl+C`.

## How it works

Each `./run.sh` cycle (default `BOT_MODE=both`, `USER_SOURCE=cursor`):

1. **Search by criteria** — GitHub Search across bio / repos / stargazers / contributors / `.cursorrules`, then apply filters (active, not empty, …)
2. **Queue** — append new logins to `data/usernames.txt`
3. **Follow** — walk the queue with rate limits
4. Sleep `LOOP_SLEEP_SECONDS`, repeat

No one-shot script — search is the first step of the bot loop.

Progress: `data/usernames.txt`, `data/state.json` → `discovery`, logs.

## Modes and sources

| Variable | Default | Options |
|----------|---------|---------|
| `BOT_MODE` | `both` | `both` — search + follow · `discover` — search/queue only · `follow` — follow queue only |
| `USER_SOURCE` | `cursor` | `cursor` — criteria search · `random` — paginated `/users` |

Search-only (fill the list, no follows):

```bash
# in .env
BOT_MODE=discover
USER_SOURCE=cursor
FETCH_COUNT=20
./run.sh
```

## Criteria search (`USER_SOURCE=cursor`)

Built into every cycle via `bot/search.py`. Channels rotate; progress in `state.json` → `discovery`.

| Channel | What it does |
|---------|----------------|
| `users` | Search users (bio mentions of Cursor / cursor.sh / anysphere, …) |
| `repos` | Search Cursor-related repos → owners; enqueue repos for mining |
| `stargazers` | Paginate stargazers of queued repos |
| `contributors` | Paginate contributors of queued repos |
| `code` | Code search for `.cursorrules`, `.cursor/rules`, `AGENTS.md` → owners |

Configure with:

- `DISCOVERY_CHANNELS` — comma list (default: all five)
- `CURSOR_USER_QUERIES` / `CURSOR_REPO_QUERIES` / `CURSOR_CODE_QUERIES` — pipe-separated custom queries (defaults are built into the bot)

## Configuration

Copy `.env.example` or edit `.env`. Only `GITHUB_TOKEN` is required; everything else has defaults in code.

### Runtime

| Variable | Default | Meaning |
|----------|---------|---------|
| `FETCH_COUNT` | `50` | Users collected per discovery cycle (max 100) |
| `FOLLOW_DELAY_MIN` / `FOLLOW_DELAY_MAX` | `3` / `6` | Random pause between follows (seconds) |
| `LOOP_SLEEP_SECONDS` | `90` | Pause between cycles (minimum 30) |
| `MAX_CALLS_PER_HOUR` | `1000` | Local follow rate limit |
| `BOT_DATA_DIR` | `./data` | State, queue, logs |
| `USERNAMES_FILE` / `STATE_FILE` / `LOG_FILE` | under `data/` | Override paths |
| `USER_AGENT` | `GitHubFollowerBot` | API User-Agent |

### Filters

Cheap list checks (on by default):

| Variable | Default | Meaning |
|----------|---------|---------|
| `FILTER_USERS_ONLY` | `1` | Keep `type=User` only |
| `FILTER_SKIP_SITE_ADMIN` | `1` | Skip site admins |
| `FILTER_SKIP_BOT_LOGINS` | `1` | Skip `*[bot]`, `*-bot`, `bot-*` |
| `FILTER_SKIP_ALREADY_FOLLOWING` | `1` | Skip if already following |
| `FILTER_ON_FOLLOW` | `1` | Re-check before each follow |
| `LOGIN_EXCLUDE` | off | Exact logins to skip (comma-separated) |

Profile / activity filters (extra API calls when enabled):

| Variable | Default | Meaning |
|----------|---------|---------|
| `MIN_FOLLOWERS` / `MAX_FOLLOWERS` (also `FOLLOWING` / `REPOS`) | off | Numeric ranges |
| `REQUIRE_BIO` / `REQUIRE_LOCATION` / `REQUIRE_HIREABLE` | `0` | Require profile fields |
| `LOCATION_CONTAINS` / `LOCATION_EXCLUDE` | off | Location substrings (case-insensitive) |
| `FILTER_SKIP_DEAD` | `1` | Drop inactive accounts |
| `MAX_INACTIVE_DAYS` | `30` | Max age of profile update / last public event |
| `FILTER_REQUIRE_PUBLIC_EVENTS` | `1` | Require a public event in that window |
| `FILTER_SKIP_EMPTY_PROFILES` | `1` | Skip 0 repos + 0 followers |

## Layout

```
GitHubFollowerBot/
├── bot/              # search (criteria) + filters + follow loop
├── data/             # queue + state + logs (gitignored)
├── assets/           # preview image
├── .env.example
├── requirements.txt
└── run.sh
```

## Notes

- GitHub rate-limits follow actions; keep delays conservative.
- Automated mass-following may conflict with the [GitHub Terms of Service](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service) — use at your own risk.
- Never commit `.env`.
