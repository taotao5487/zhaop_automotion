#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LEGACY_DIR="/Users/xiongtao/Documents/scaper_zhaopin/Wechat_api/wechat-download-api"
LEGACY_DATA_DIR="$LEGACY_DIR/data"
LEGACY_ENV_FILE="$LEGACY_DIR/.env"
BACKUP_DIR="$ROOT_DIR/data_backups/$(date +%Y%m%d_%H%M%S)"

mkdir -p "$ROOT_DIR/data" "$ROOT_DIR/logs" "$BACKUP_DIR"

if [ -f "$ROOT_DIR/data/rss.db" ]; then
  cp -a "$ROOT_DIR/data/." "$BACKUP_DIR/"
fi

if [ -d "$LEGACY_DATA_DIR" ]; then
  cp -a "$LEGACY_DATA_DIR/." "$ROOT_DIR/data/"
fi

if [ ! -f "$ROOT_DIR/.env" ] && [ -f "$LEGACY_ENV_FILE" ]; then
  cp "$LEGACY_ENV_FILE" "$ROOT_DIR/.env"
fi

docker rm -f wechat-download-api >/dev/null 2>&1 || true

cd "$ROOT_DIR"
docker compose up -d

echo "Migration complete."
echo "Current project runtime:"
echo "  data: $ROOT_DIR/data"
echo "  env:  $ROOT_DIR/.env"
