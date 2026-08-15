#!/usr/bin/env python3
"""Nightly skill curator — lifecycle management for self-created skills.

Deterministic, no LLM call: pure date arithmetic against
skills/.usage.json (written by relay/skill_resolver.py's _bump_usage() on
every skill routing). Deliberately imports nothing from the relay/memory
app package — this is a standalone stdlib script, runnable via cron
independent of whether the main service is even up.

Lifecycle (for skills with a created_by field in their frontmatter — i.e.
skill_manage-authored, never hand-authored ones):
  active  -> stale     after STALE_AFTER_DAYS (default 30) unused
  stale   -> archived  after ARCHIVE_AFTER_DAYS (default 90) unused

"stale" is informational only (logged, not a file change). "archived"
physically moves skills/<name>/ to skills/.archive/<name>/ and removes it
from manifest.json — never deletes.

A skill with `pinned: true` in its frontmatter is exempt from both
transitions regardless of idle time.

If skills/.usage.json has no entry for a skill yet (freshly created,
hasn't been routed to since), last_used is seeded from the skill's own
created_at so a brand-new skill isn't punished for lacking telemetry.

If anything was archived and --restart-service-on-change is passed, the
configured systemd --user service is restarted so the running process's
in-memory manifest cache picks up the change (the running relay caches
skills/manifest.json in memory; an external file change alone doesn't
invalidate that cache). Off by default — auto-restarting a production
agent process from a cron job is a real operational choice, not something
this template does silently.

Every 7th run (or --force-digest) prints a use-count summary — capture it
via cron mail or a systemd timer's journal, same as the other nightly
scripts in this directory.

Usage:
  python scripts/skill_curator.py                          # full run
  python scripts/skill_curator.py --dry-run                # log only, no writes
  python scripts/skill_curator.py --restart-service-on-change
  python scripts/skill_curator.py --stale-after 14 --archive-after 45
"""

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SKILLS_DIR = PROJECT_ROOT / "skills"
MANIFEST_PATH = SKILLS_DIR / "manifest.json"
USAGE_PATH = SKILLS_DIR / ".usage.json"
ARCHIVE_DIR = SKILLS_DIR / ".archive"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.json"

STALE_AFTER_DAYS_DEFAULT = 30
ARCHIVE_AFTER_DAYS_DEFAULT = 90

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [skill_curator] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("skill_curator")


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _parse_frontmatter_field(content: str, field: str) -> str | None:
    """Minimal single-line frontmatter field extractor — mirrors the
    hand-rolled parsing already used by relay/skill_resolver.py rather than
    pulling in a YAML dependency for a handful of scalar fields."""
    if not content.startswith("---"):
        return None
    end = content.find("---", 3)
    if end == -1:
        return None
    frontmatter = content[3:end]
    for line in frontmatter.split("\n"):
        stripped = line.strip()
        if stripped.startswith(f"{field}:"):
            return stripped.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def _service_name() -> str:
    settings = _load_json(SETTINGS_PATH, {})
    return settings.get("service_name", "adam-selene.service")


def _self_created_skills(manifest: dict) -> list[dict]:
    return [s for s in manifest.get("skills", []) if s.get("created_by")]


def run(stale_after_days: int, archive_after_days: int, dry_run: bool, force_digest: bool) -> dict:
    manifest = _load_json(MANIFEST_PATH, {"skills": []})
    usage = _load_json(USAGE_PATH, {})
    now = datetime.now(timezone.utc)

    stale = []
    archived = []
    skipped_pinned = []

    for entry in _self_created_skills(manifest):
        name = entry["name"]
        skill_path = SKILLS_DIR / entry["path"]
        if not skill_path.exists():
            logger.warning(f"Manifest references missing skill file: {entry['path']}")
            continue

        content = skill_path.read_text()
        if _parse_frontmatter_field(content, "pinned") == "true":
            skipped_pinned.append(name)
            continue

        last_used_str = usage.get(name, {}).get("last_used")
        if not last_used_str:
            last_used_str = entry.get("created_at")
        if not last_used_str:
            logger.warning(f"No usage or created_at data for '{name}' — skipping")
            continue

        last_used = datetime.fromisoformat(last_used_str)
        if last_used.tzinfo is None:
            last_used = last_used.replace(tzinfo=timezone.utc)
        idle_days = (now - last_used).days

        if idle_days >= archive_after_days:
            archived.append((name, idle_days))
        elif idle_days >= stale_after_days:
            stale.append((name, idle_days))

    for name, idle_days in stale:
        logger.info(f"Stale: '{name}' unused for {idle_days} days (threshold {stale_after_days})")

    for name, idle_days in archived:
        logger.info(f"Archiving: '{name}' unused for {idle_days} days (threshold {archive_after_days})")
        if dry_run:
            continue
        skill_dir = SKILLS_DIR / name
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        dest = ARCHIVE_DIR / name
        if dest.exists():
            dest = ARCHIVE_DIR / f"{name}-{now.strftime('%Y%m%d%H%M%S')}"
        shutil.move(str(skill_dir), str(dest))
        manifest["skills"] = [s for s in manifest["skills"] if s["name"] != name]

    if archived and not dry_run:
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")

    day_of_week = now.weekday()  # 0 = Monday
    if force_digest or day_of_week == 0:
        _print_digest(manifest, usage)

    return {
        "stale": [n for n, _ in stale],
        "archived": [n for n, _ in archived],
        "skipped_pinned": skipped_pinned,
        "dry_run": dry_run,
    }


def _print_digest(manifest: dict, usage: dict) -> None:
    self_created = _self_created_skills(manifest)
    logger.info(f"--- Weekly skill digest: {len(self_created)} self-created skill(s) ---")
    for entry in sorted(self_created, key=lambda s: usage.get(s["name"], {}).get("use_count", 0), reverse=True):
        name = entry["name"]
        stats = usage.get(name, {"use_count": 0, "last_used": None})
        logger.info(f"  {name}: {stats['use_count']} uses, last used {stats.get('last_used') or 'never'}")


def _restart_service() -> None:
    service = _service_name()
    try:
        subprocess.run(["systemctl", "--user", "restart", service], check=True, timeout=30)
        logger.info(f"Restarted {service} to pick up manifest changes")
    except Exception as e:
        logger.error(f"Failed to restart {service}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Log what would happen, make no changes")
    parser.add_argument("--stale-after", type=int, default=STALE_AFTER_DAYS_DEFAULT, dest="stale_after_days")
    parser.add_argument("--archive-after", type=int, default=ARCHIVE_AFTER_DAYS_DEFAULT, dest="archive_after_days")
    parser.add_argument("--force-digest", action="store_true", help="Print the weekly digest regardless of day")
    parser.add_argument(
        "--restart-service-on-change", action="store_true",
        help="Restart the configured systemd --user service if any skill was archived, "
             "so the running process's in-memory manifest cache refreshes. Off by default.",
    )
    args = parser.parse_args()

    logger.info("Starting nightly skill curation pass")
    try:
        result = run(args.stale_after_days, args.archive_after_days, args.dry_run, args.force_digest)
        print(json.dumps(result, indent=2))
        if result["archived"] and args.restart_service_on_change and not args.dry_run:
            _restart_service()
        logger.info("Skill curation pass complete")
    except Exception as e:
        logger.error(f"Skill curation pass failed: {e}", exc_info=True)
        sys.exit(1)
