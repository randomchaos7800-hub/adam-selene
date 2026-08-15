"""skill_manage — lets the agent author, patch, and retire its own SKILL.md
files at runtime. The self-learning half of the skills architecture.

skills/ already lets a human author markdown workflow definitions the
resolver routes to by trigger keyword ("thin harness, fat skills" — see
relay/skill_resolver.py). This tool extends that same mechanism to the
agent itself: after a corrected mistake or a hard-won multi-step
procedure, it can write down what worked as a new skill, so next time the
same situation comes up the resolver routes straight to the procedure
instead of the agent re-deriving it.

Safety posture, deliberately conservative:
  - name/description/content/trigger validation on create (see _validate_create)
  - declared tools checked against TOOL_DENYLIST — a self-authored skill can
    never grant itself shell, code-edit, config, vault, or service-restart
    access, no matter what its content says
  - patch only touches skills this tool itself created (hand-authored
    skills have no `created_by` frontmatter field, so they're naturally
    excluded — nothing built by a person is patchable by the agent)
  - archive moves to skills/.archive/, never deletes
  - a hard cap on self-created skills (MAX_SELF_CREATED, default 15),
    enforced before every create

Registered via relay/tool_registry.py rather than hand-added to
relay/tools.py's monolith — see that module's docstring for why.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from relay import config
from relay.tool_registry import REGISTRY, ToolEntry

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"
MANIFEST_PATH = SKILLS_DIR / "manifest.json"
ARCHIVE_DIR = SKILLS_DIR / ".archive"

# Tools a self-created skill can never declare, regardless of what its
# content says — shell, code-edit, config, vault, and service-restart stay
# out of reach of anything the agent writes for itself. Mirrors
# relay.tools.PRIVILEGED_TOOLS plus the config/backup-restore tools that
# aren't owner-gated today but still change runtime behavior.
TOOL_DENYLIST = frozenset({
    "vault_get", "vault_set", "store_credential", "read_credential",
    "write_my_code", "edit_my_code", "git_commit", "run_shell",
    "update_my_instructions",
    "update_config_setting", "set_default_model", "restart_agent_service",
    "backup_myself", "restore_from_backup",
})

MAX_SELF_CREATED_DEFAULT = 15

_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_TRIGGER_STOPWORDS = frozenset({
    "the", "a", "an", "you", "your", "it", "is", "are", "was", "were",
    "this", "that", "and", "or", "but", "what", "how", "why", "when",
    "help", "please", "can", "do", "does",
})

MIN_DESCRIPTION_LEN = 1
MAX_DESCRIPTION_LEN = 200
MIN_CONTENT_LEN = 50
MAX_CONTENT_LEN = 12000
MIN_TRIGGERS = 2
MAX_TRIGGERS = 12


def _max_self_created() -> int:
    settings = config.load_settings()
    return settings.get("skills", {}).get("max_self_created", MAX_SELF_CREATED_DEFAULT)


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"skills": []}
    return json.loads(MANIFEST_PATH.read_text())


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")


def _all_existing_triggers() -> set[str]:
    """Trigger phrases already claimed by any existing skill, lowercased."""
    from relay import skill_resolver
    claimed: set[str] = set()
    for skill in _load_manifest().get("skills", []):
        path = SKILLS_DIR / skill["path"]
        if not path.exists():
            continue
        content = path.read_text()
        for trigger in skill_resolver._parse_triggers(content):
            claimed.add(trigger.strip().lower())
    return claimed


def _self_created_skills() -> list[dict]:
    """Manifest entries for skills this tool created (have created_by set)."""
    return [s for s in _load_manifest().get("skills", []) if s.get("created_by")]


def _is_self_created(name: str) -> bool:
    for skill in _load_manifest().get("skills", []):
        if skill["name"] == name:
            return bool(skill.get("created_by"))
    return False


def _known_tool_names() -> set[str]:
    """All tool names the framework currently knows about — deferred import
    to avoid a circular dependency (relay.tools imports this module to
    register skill_manage; this module can't import relay.tools at
    module-load time without a cycle, but can safely do so inside a
    function body called at agent runtime, long after both modules have
    finished loading)."""
    from relay import tools as tools_module
    names = {t["name"] for t in tools_module.TOOL_DEFINITIONS}
    names.update(REGISTRY.list_toolsets().get("skills", []))
    return names


def _validate_create(name: str, description: str, content: str, triggers: list, tools: list) -> list[str]:
    """Return a list of validation errors — empty list means valid."""
    errors = []

    if not _NAME_RE.match(name or ""):
        errors.append("name must be lowercase-hyphenated (e.g. 'deploy-checklist'), starting with a letter")
    if (SKILLS_DIR / name).exists():
        errors.append(f"a skill named '{name}' already exists")

    if not description or not (MIN_DESCRIPTION_LEN <= len(description) <= MAX_DESCRIPTION_LEN):
        errors.append(f"description must be 1-{MAX_DESCRIPTION_LEN} chars")

    if not content or not (MIN_CONTENT_LEN <= len(content) <= MAX_CONTENT_LEN):
        errors.append(f"content must be {MIN_CONTENT_LEN}-{MAX_CONTENT_LEN} chars")

    if not isinstance(triggers, list) or not (MIN_TRIGGERS <= len(triggers) <= MAX_TRIGGERS):
        errors.append(f"triggers must be a list of {MIN_TRIGGERS}-{MAX_TRIGGERS} phrases")
    else:
        existing = _all_existing_triggers()
        for trig in triggers:
            t = (trig or "").strip().lower()
            if len(t) < 3:
                errors.append(f"trigger '{trig}' too short/generic (min 3 chars)")
            elif t in _TRIGGER_STOPWORDS:
                errors.append(f"trigger '{trig}' is too common — it would fire on nearly every message")
            elif t in existing:
                errors.append(f"trigger '{trig}' already claimed by another skill")

    if not isinstance(tools, list) or not tools:
        errors.append("tools must be a non-empty list of tool names this skill uses")
    else:
        denied = [t for t in tools if t in TOOL_DENYLIST]
        if denied:
            errors.append(f"tools {denied} are not allowed in a self-created skill")
        unknown = [t for t in tools if t not in TOOL_DENYLIST and t not in _known_tool_names()]
        if unknown:
            errors.append(f"unknown tool name(s): {unknown}")

    if len(_self_created_skills()) >= _max_self_created():
        errors.append(
            f"self-created skill cap reached ({_max_self_created()}) — "
            f"archive an unused one before creating another"
        )

    return errors


def _notify_owner(message: str) -> None:
    """Best-effort notification. Never raises — a failed notification
    shouldn't fail the skill operation itself."""
    try:
        from relay.telegram_sender import send_message_to_owner
        send_message_to_owner(message)
    except Exception as e:
        logger.debug(f"skill_manage notification skipped: {e}")


def _create(name: str, description: str, content: str, triggers: list, tools: list) -> str:
    errors = _validate_create(name, description, content, triggers, tools)
    if errors:
        return "Cannot create skill:\n" + "\n".join(f"  - {e}" for e in errors)

    skill_dir = SKILLS_DIR / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    triggers_yaml = "\n".join(f'  - "{t}"' for t in triggers)
    tools_yaml = "\n".join(f"  - {t}" for t in tools)
    frontmatter = (
        "---\n"
        f"name: {name}\n"
        "version: 1.0.0\n"
        f"description: {description}\n"
        f"triggers:\n{triggers_yaml}\n"
        f"tools:\n{tools_yaml}\n"
        "mutating: false\n"
        f"created_by: {config.agent_name()}\n"
        f"created_at: {now}\n"
        "pinned: false\n"
        "---\n\n"
    )
    (skill_dir / "SKILL.md").write_text(frontmatter + content.strip() + "\n")

    manifest = _load_manifest()
    manifest.setdefault("skills", []).append({
        "name": name,
        "path": f"{name}/SKILL.md",
        "description": description,
        "created_by": config.agent_name(),
        "created_at": now,
    })
    _save_manifest(manifest)

    from relay import skill_resolver
    skill_resolver.reload()

    _notify_owner(f"🧠 Learned a new skill: *{name}* — {description}")
    logger.info(f"skill_manage: created '{name}'")
    return f"Skill '{name}' created ({len(_self_created_skills())}/{_max_self_created()} self-created slots used)."


def _patch(name: str, old_str: str, new_str: str) -> str:
    if not _is_self_created(name):
        return (
            f"Cannot patch '{name}' — it wasn't created by this tool. "
            f"Only self-created skills (with a created_by field) can be patched here."
        )

    skill_path = SKILLS_DIR / name / "SKILL.md"
    if not skill_path.exists():
        return f"Skill '{name}' not found on disk."

    content = skill_path.read_text()
    count = content.count(old_str)
    if count == 0:
        return "old_str not found in skill content."
    if count > 1:
        return f"old_str appears {count} times — must be unique. Include more surrounding context."

    skill_path.write_text(content.replace(old_str, new_str, 1))

    from relay import skill_resolver
    skill_resolver.reload()

    logger.info(f"skill_manage: patched '{name}'")
    return f"Skill '{name}' patched."


def _archive(name: str) -> str:
    if not _is_self_created(name):
        return (
            f"Cannot archive '{name}' — it wasn't created by this tool. "
            f"Hand-authored skills aren't managed here."
        )

    skill_dir = SKILLS_DIR / name
    if not skill_dir.exists():
        return f"Skill '{name}' not found on disk."

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    dest = ARCHIVE_DIR / name
    if dest.exists():
        dest = ARCHIVE_DIR / f"{name}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    skill_dir.rename(dest)

    manifest = _load_manifest()
    manifest["skills"] = [s for s in manifest.get("skills", []) if s["name"] != name]
    _save_manifest(manifest)

    from relay import skill_resolver
    skill_resolver.reload()

    logger.info(f"skill_manage: archived '{name}' -> {dest}")
    return f"Skill '{name}' archived to {dest.relative_to(SKILLS_DIR.parent)}."


def _handle_skill_manage(tool_input: dict, **_ctx) -> str:
    action = tool_input.get("action", "")
    name = (tool_input.get("name") or "").strip()

    if action == "create":
        return _create(
            name=name,
            description=tool_input.get("description", "").strip(),
            content=tool_input.get("content", ""),
            triggers=tool_input.get("triggers", []),
            tools=tool_input.get("tools", []),
        )
    elif action == "patch":
        return _patch(name, tool_input.get("old_str", ""), tool_input.get("new_str", ""))
    elif action == "archive":
        return _archive(name)
    else:
        return f"Unknown action '{action}'. Use 'create', 'patch', or 'archive'."


SKILL_MANAGE_SCHEMA = {
    "name": "skill_manage",
    "description": (
        "Create, patch, or archive your own skills — procedural memory for how to do "
        "things, as opposed to write_memory's factual memory. Use 'create' after "
        "figuring out a non-obvious multi-step procedure, getting corrected on a "
        "workflow, or repeatedly re-deriving the same approach — so the next time this "
        "comes up, the skill resolver routes straight to it. Use 'patch' when an active "
        "self-created skill turns out to be wrong or outdated. Use 'archive' to retire "
        "one that's no longer useful (never deletes — moves to skills/.archive/). "
        "Only skills this tool created can be patched or archived; hand-authored skills "
        "are read-only here."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "patch", "archive"],
            },
            "name": {
                "type": "string",
                "description": "Skill name, lowercase-hyphenated (e.g. 'deploy-checklist'). Required for all actions.",
            },
            "description": {
                "type": "string",
                "description": "One-line description (create only, max 200 chars).",
            },
            "content": {
                "type": "string",
                "description": "The skill body — the actual procedure/workflow instructions (create only, 50-12000 chars).",
            },
            "triggers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2-12 phrases that should route a message to this skill (create only). Must be specific — generic single words are rejected.",
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tool names this skill uses (create only). Cannot include shell/code-edit/config/vault/service-restart tools.",
            },
            "old_str": {
                "type": "string",
                "description": "Exact text to find and replace (patch only). Must appear exactly once.",
            },
            "new_str": {
                "type": "string",
                "description": "Replacement text (patch only).",
            },
        },
        "required": ["action", "name"],
    },
}

REGISTRY.register(ToolEntry(
    name="skill_manage",
    toolset="skills",
    schema=SKILL_MANAGE_SCHEMA,
    handler=_handle_skill_manage,
))
