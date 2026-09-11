# GitHub Follower Bot

Based on [Github_Automation_Follower_Bot](https://github.com/OfficialCodeVoyage/Github_Automation_Follower_Bot), configured for local use.

## Setup

1. Install Python 3.10+ (WSL/Debian):

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

2. Create a [classic PAT](https://github.com/settings/tokens) with scopes:
   - `read:user`
   - `user:follow`

3. Configure env:

```bash
cd ~/GitHubFollowerBot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and set GITHUB_TOKEN=ghp_...
```

4. Run:

```bash
python main.py
```

## Config (`.env`)

| Variable | Default | Meaning |
|----------|---------|---------|
| `GITHUB_TOKEN` | — | required PAT |
| `FETCH_COUNT` | `50` | users fetched per cycle (max 100) |
| `FOLLOW_DELAY_MIN` / `MAX` | `3` / `6` | pause between follows (seconds) |
| `LOOP_SLEEP_SECONDS` | `90` | pause between cycles |
| `MAX_CALLS_PER_HOUR` | `1000` | local rate limit |

Progress is stored in `state.json` and `usernames.txt`.

## Notes

- GitHub rate-limits follow actions; keep delays conservative.
- Automated mass-following may conflict with [GitHub Terms of Service](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service) — use at your own risk.
- Never commit `.env`.
