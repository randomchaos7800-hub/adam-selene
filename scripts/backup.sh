#!/usr/bin/env bash
set -euo pipefail

# SmartAgent backup script
# Configurable via environment variables:
#   SMARTAGENT_BACKUP_DIR  — where to store backups (default: /mnt/backup/smartagent)
#   SMARTAGENT_MEMORY_DIR  — memory directory name relative to parent (default: smartagent-memory)
#   SMARTAGENT_GIT_REMOTE  — git remote URL (optional, skips push if unset)
#   SMARTAGENT_GIT_TOKEN   — git token for push (optional)

SMARTAGENT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${SMARTAGENT_BACKUP_DIR:-/mnt/backup/smartagent}"
MEMORY_DIR="${SMARTAGENT_MEMORY_DIR:-smartagent-memory}"
DATE=$(date +%Y-%m-%d)

mkdir -p "$BACKUP_DIR"

ARCHIVE="$BACKUP_DIR/smartagent-backup-${DATE}.tar.gz"

# Build tar from the project root's parent so we can include the memory dir
tar czf "$ARCHIVE" \
  -C "$(dirname "$SMARTAGENT_ROOT")" \
  --ignore-failed-read \
  "$(basename "$SMARTAGENT_ROOT")/config" \
  "$(basename "$SMARTAGENT_ROOT")/relay" \
  "$(basename "$SMARTAGENT_ROOT")/interfaces" \
  "$MEMORY_DIR/" \
  2>/dev/null || true

# Prune backups older than 30 days
find "$BACKUP_DIR" -name "smartagent-backup-*.tar.gz" -mtime +30 -delete 2>/dev/null || true

SIZE=$(du -h "$ARCHIVE" | cut -f1)
echo "SmartAgent backup complete: $ARCHIVE ($SIZE)"

# Regenerate manifest and commit to git (if configured)
cd "$SMARTAGENT_ROOT"
find . -type f \
  -not -path "./venv/*" \
  -not -path "./__pycache__/*" \
  -not -path "./*/__pycache__/*" \
  -not -name "*.pyc" \
  -not -name "secrets.env" \
  -not -name "*.db-shm" \
  -not -name "*.db-wal" \
  | sort | xargs sha256sum > MANIFEST.sha256

git add -A
git diff --cached --quiet && echo "No changes to commit" && exit 0
git commit -m "Nightly backup ${DATE} — auto-commit from backup.sh"

# Push to remote if configured
if [ -n "${SMARTAGENT_GIT_REMOTE:-}" ]; then
  if [ -n "${SMARTAGENT_GIT_TOKEN:-}" ]; then
    git push "$SMARTAGENT_GIT_REMOTE" main
  else
    git push origin main
  fi
  echo "SmartAgent repo pushed to remote"
fi
