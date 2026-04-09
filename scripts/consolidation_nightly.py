#!/usr/bin/env python3
"""Nightly memory consolidation runner.

Runs after lighthouse_nightly.py to consolidate extracted memories.

Usage:
  python scripts/consolidation_nightly.py            # full run
  python scripts/consolidation_nightly.py --dry-run  # log what would happen, no writes
"""

import json
import logging
import os
import sys
from pathlib import Path

SMARTAGENT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SMARTAGENT_ROOT))

# Load secrets from config/secrets.env if present
secrets_path = SMARTAGENT_ROOT / "config" / "secrets.env"
if secrets_path.exists():
    for line in secrets_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [consolidation] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("consolidation_nightly")

from memory.consolidation import run

if __name__ == "__main__":
    logger.info("Starting nightly memory consolidation pass")
    try:
        result = run()
        print(json.dumps(result, indent=2))
        logger.info("Consolidation pass complete")
    except Exception as e:
        logger.error(f"Consolidation pass failed: {e}", exc_info=True)
        sys.exit(1)
