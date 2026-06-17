#!/bin/bash
# Build and (re)launch the Snowywood Discord bot container.
# Uses host networking so 127.0.0.1 reaches both the game (1337) and the ingest port (5000).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "Missing .env (copy .env.example to .env and fill it in)." >&2
  exit 1
fi

docker build -t snowywood-discord-bot .
docker rm -f snowywood-discord-bot 2>/dev/null || true
docker run -d \
  --name snowywood-discord-bot \
  --restart unless-stopped \
  --network host \
  --env-file .env \
  -v "$(pwd)/data:/app/data" \
  snowywood-discord-bot

echo "Bot started. Logs: docker logs -f snowywood-discord-bot"
