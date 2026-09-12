#!/usr/bin/env bash
# Install deps if needed, then start the bot.
#   ./run.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -r requirements.txt
else
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env — will ask for GitHub token on first start."
fi

mkdir -p data
exec python -m bot "$@"
