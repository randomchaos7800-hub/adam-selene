import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location(
    "skill_curator", Path(__file__).parent.parent / "scripts" / "skill_curator.py"
)
skill_curator = importlib.util.module_from_spec(_SPEC)
sys.modules["skill_curator"] = skill_curator
_SPEC.loader.exec_module(skill_curator)


def _iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class TestSkillCurator(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.skills_dir = Path(self.tempdir.name) / "skills"
        self.skills_dir.mkdir()
        self.manifest_path = self.skills_dir / "manifest.json"
        self.usage_path = self.skills_dir / ".usage.json"
        self.archive_dir = self.skills_dir / ".archive"

        self.patchers = [
            patch.object(skill_curator, "SKILLS_DIR", self.skills_dir),
            patch.object(skill_curator, "MANIFEST_PATH", self.manifest_path),
            patch.object(skill_curator, "USAGE_PATH", self.usage_path),
            patch.object(skill_curator, "ARCHIVE_DIR", self.archive_dir),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def _make_skill(self, name, created_at, pinned=False, created_by="TestAgent"):
        skill_dir = self.skills_dir / name
        skill_dir.mkdir()
        pinned_str = "true" if pinned else "false"
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ncreated_by: {created_by}\ncreated_at: {created_at}\npinned: {pinned_str}\n---\n\nbody\n"
        )
        return {"name": name, "path": f"{name}/SKILL.md", "description": "d", "created_by": created_by, "created_at": created_at}

    def _write_manifest(self, entries):
        self.manifest_path.write_text(json.dumps({"skills": entries}))

    def _write_usage(self, usage: dict):
        self.usage_path.write_text(json.dumps(usage))

    def test_recently_used_skill_stays_active(self):
        entry = self._make_skill("fresh-skill", _iso(0))
        self._write_manifest([entry])
        self._write_usage({"fresh-skill": {"use_count": 5, "last_used": _iso(1)}})

        result = skill_curator.run(stale_after_days=30, archive_after_days=90, dry_run=False, force_digest=False)

        self.assertEqual(result["stale"], [])
        self.assertEqual(result["archived"], [])
        self.assertTrue((self.skills_dir / "fresh-skill").exists())

    def test_skill_idle_past_stale_threshold_is_flagged_not_moved(self):
        entry = self._make_skill("aging-skill", _iso(40))
        self._write_manifest([entry])
        self._write_usage({"aging-skill": {"use_count": 1, "last_used": _iso(40)}})

        result = skill_curator.run(stale_after_days=30, archive_after_days=90, dry_run=False, force_digest=False)

        self.assertIn("aging-skill", result["stale"])
        self.assertEqual(result["archived"], [])
        self.assertTrue((self.skills_dir / "aging-skill").exists())  # not moved

    def test_skill_idle_past_archive_threshold_is_moved_and_removed_from_manifest(self):
        entry = self._make_skill("dead-skill", _iso(100))
        self._write_manifest([entry])
        self._write_usage({"dead-skill": {"use_count": 1, "last_used": _iso(100)}})

        result = skill_curator.run(stale_after_days=30, archive_after_days=90, dry_run=False, force_digest=False)

        self.assertIn("dead-skill", result["archived"])
        self.assertFalse((self.skills_dir / "dead-skill").exists())
        self.assertTrue((self.archive_dir / "dead-skill").exists())
        manifest = json.loads(self.manifest_path.read_text())
        names = [s["name"] for s in manifest["skills"]]
        self.assertNotIn("dead-skill", names)

    def test_pinned_skill_never_archived_regardless_of_idle_time(self):
        entry = self._make_skill("pinned-skill", _iso(200), pinned=True)
        self._write_manifest([entry])
        self._write_usage({})  # no usage at all — falls back to created_at, still 200 days old

        result = skill_curator.run(stale_after_days=30, archive_after_days=90, dry_run=False, force_digest=False)

        self.assertNotIn("pinned-skill", result["archived"])
        self.assertNotIn("pinned-skill", result["stale"])
        self.assertIn("pinned-skill", result["skipped_pinned"])
        self.assertTrue((self.skills_dir / "pinned-skill").exists())

    def test_missing_usage_record_falls_back_to_created_at(self):
        # No .usage.json entry at all for this skill — it should be judged
        # by created_at, not silently skipped or treated as "just used".
        entry = self._make_skill("never-routed-skill", _iso(95))
        self._write_manifest([entry])
        self._write_usage({})

        result = skill_curator.run(stale_after_days=30, archive_after_days=90, dry_run=False, force_digest=False)

        self.assertIn("never-routed-skill", result["archived"])

    def test_hand_authored_skill_is_never_considered(self):
        # No created_by field at all — must be completely ignored by the curator.
        skill_dir = self.skills_dir / "hand-authored"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: hand-authored\n---\n\nbody\n")
        self._write_manifest([{"name": "hand-authored", "path": "hand-authored/SKILL.md", "description": "d"}])
        self._write_usage({})

        result = skill_curator.run(stale_after_days=30, archive_after_days=90, dry_run=False, force_digest=False)

        self.assertEqual(result["archived"], [])
        self.assertEqual(result["stale"], [])
        self.assertTrue((self.skills_dir / "hand-authored").exists())

    def test_dry_run_does_not_move_files_or_touch_manifest(self):
        entry = self._make_skill("dead-skill", _iso(100))
        self._write_manifest([entry])
        self._write_usage({"dead-skill": {"use_count": 1, "last_used": _iso(100)}})
        original_manifest_text = self.manifest_path.read_text()

        result = skill_curator.run(stale_after_days=30, archive_after_days=90, dry_run=True, force_digest=False)

        self.assertIn("dead-skill", result["archived"])  # reported...
        self.assertTrue((self.skills_dir / "dead-skill").exists())  # ...but not actually moved
        self.assertFalse((self.archive_dir / "dead-skill").exists())
        self.assertEqual(self.manifest_path.read_text(), original_manifest_text)

    def test_archiving_into_existing_dest_uses_timestamped_fallback(self):
        # Simulate a name collision in .archive/ from a prior archive run.
        self.archive_dir.mkdir()
        (self.archive_dir / "dead-skill").mkdir()

        entry = self._make_skill("dead-skill", _iso(100))
        self._write_manifest([entry])
        self._write_usage({"dead-skill": {"use_count": 1, "last_used": _iso(100)}})

        skill_curator.run(stale_after_days=30, archive_after_days=90, dry_run=False, force_digest=False)

        # Original collision dir untouched, a second timestamped dir created.
        archived_dirs = list(self.archive_dir.iterdir())
        self.assertGreaterEqual(len(archived_dirs), 2)


if __name__ == "__main__":
    unittest.main()
