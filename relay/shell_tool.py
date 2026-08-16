"""
Shell execution tool for the agent.

Lets the agent run commands on the host machine, sandboxed via bubblewrap
(bwrap) — OS-level isolation, not just the regex blocklist below. The
blocklist alone was never a security boundary (a bare `subprocess.run(cmd,
shell=True)` with no sandbox, `rm -fr` vs `rm -rf`, `find . -delete`, or
any tool that doesn't route through this blocklist at all trivially get
past pattern matching) — it's kept as a cheap first-pass filter, but the
sandbox is the actual control now.

Design, in order of what each piece closes:
  - `--ro-bind / /`                    whole host filesystem read-only —
                                        the agent can still read/execute
                                        anything on the system, but can't
                                        tamper with it
  - `--tmpfs $HOME` + `--bind` back    blanks the home directory, then
    project_root/memory_root           restores read-write ONLY the
                                        agent's own project and memory
                                        directories — this is an ALLOWLIST
                                        of what's writable, not a denylist
                                        of what's hidden (a denylist
                                        approach was tried first in a
                                        sibling deployment of this same
                                        framework and found — via
                                        adversarial review — to miss
                                        ~/.netrc, ~/.config/rclone,
                                        credential files, and browser
                                        profiles; don't repeat that)
  - explicit secret-path masks         config/secrets.env (this
    (--ro-bind /dev/null <path>)       framework's own live API keys/
                                        tokens) and common credential
                                        locations (~/.ssh, ~/.aws, ~/.netrc,
                                        ~/.gnupg, ~/.docker, ...) stay
                                        unreadable even though they'd
                                        otherwise fall under a broader bind
  - `--clearenv` + a small allowlist   dotenv-loaded secrets
                                        (OPENROUTER_API_KEY,
                                        TELEGRAM_BOT_TOKEN, ...) live in
                                        os.environ by the time this runs;
                                        bwrap inherits the parent
                                        environment by default unless
                                        cleared, so without this,
                                        `echo $SOME_KEY` or reading
                                        /proc/self/environ inside the
                                        sandbox would leak everything
                                        regardless of file-level masking
  - `--tmpfs /tmp`                     private, isolated scratch space,
                                        wiped on exit
  - namespace unshares + new-session   process/namespace isolation: blocks
    + die-with-parent                  TIOCSTI terminal injection, no
                                        orphaned processes, nothing
                                        outlives the parent

Fails CLOSED if bubblewrap isn't available — refuses to execute rather
than silently falling back to unsandboxed shell=True. This is deliberate,
not an oversight: a sibling deployment's first sandbox attempt DID fall
back silently on a missing dependency, and that was itself found and
fixed as a real vulnerability. See settings.json's `shell.require_sandbox`
if you need an explicit, logged opt-out for a non-Linux dev environment —
bubblewrap is Linux-only.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from relay import config

logger = logging.getLogger(__name__)

# Hard-blocked patterns — cheap first-pass filter, NOT the security
# boundary (the sandbox below is). Catches the lazy/obvious cases before
# a subprocess even spawns.
BLOCKED_PATTERNS = [
    r"rm\s+-rf",                          # mass delete
    r"rm\s+.*--no-preserve-root",
    r">\s*/dev/sd",                        # overwrite block devices
    r"dd\s+.*of=/dev/",                   # write to block device
    r"mkfs",                              # format filesystem
    r"systemctl\s+(stop|disable|mask|kill)\s+(nginx|tailscale|postgresql|cloudflared)", # stop critical services
    r"kill\s+.*\b1\b",                    # kill init/systemd
    r"pkill\s+-9\s+(python|node|nginx)",  # mass kill agents/services
    r"vault\.sh",                         # vault access
    r"secrets\.age",                      # age-encrypted vault
    r"\.vault",                           # vault directory/files
    r"ssh.*authorized_keys",              # SSH key modification
    r"sshd_config",                       # SSH config
    r"git\s+push\s+.*--force",           # force push
    r"curl\s+.*\|\s*(ba)?sh",            # curl pipe to shell (code injection)
    r"wget\s+.*\|\s*(ba)?sh",            # wget pipe to shell
    r"python[23]?\s+-c\s+['\"]import\s+os.*system",  # python os.system injection
    r"chmod\s+[0-7]*7[0-7]*\s+.*\.(sh|py)",  # make scripts world-executable
    r"base64\s+.*-d.*\|\s*(ba)?sh",      # base64 decode piped to shell
    r"eval\s+",                           # eval anything
    r"\$\(.*vault",                       # command substitution accessing vault
    r"`.*vault",                          # backtick substitution accessing vault
    r"cat\s+.*\.vault",                   # cat vault files
    r"source\s+.*\.vault",               # source vault files
]

BLOCKED_RE = [re.compile(p) for p in BLOCKED_PATTERNS]
DEFAULT_TIMEOUT = 60

BWRAP_BIN = shutil.which("bwrap")

# Credential/secret locations masked inside the sandbox even if they'd
# otherwise fall under a broader read-only or read-write bind. Generic
# common-sense defaults — not exhaustive, but closes the well-known ones.
_SECRET_MASK_PATHS = [
    Path.home() / ".ssh",
    Path.home() / ".netrc",
    Path.home() / ".aws",
    Path.home() / ".gnupg",
    Path.home() / ".docker" / "config.json",
    Path.home() / ".npmrc",
    Path.home() / ".pypirc",
    Path.home() / ".config" / "gh",
    Path.home() / ".config" / "rclone",
    Path.home() / ".vault",
]

# Environment variables allowed to pass through. Everything else —
# including any dotenv-loaded secret — is stripped.
_ENV_ALLOWLIST = ["PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL"]


def _is_blocked(command: str) -> str | None:
    """Return the matched pattern string if blocked, else None."""
    for pattern in BLOCKED_RE:
        if pattern.search(command):
            return pattern.pattern
    return None


def _require_sandbox() -> bool:
    settings = config.load_settings()
    return settings.get("shell", {}).get("require_sandbox", True)


def _resolve_chdir(work_dir: Path, project_root: Path, memory_root: Path, home: Path) -> Path:
    """Pick a --chdir target that will actually exist inside the sandbox.

    project_root/memory_root are the only things restored under a
    tmpfs-blanked $HOME — a caller-supplied cwd elsewhere under $HOME
    would otherwise vanish and bwrap's --chdir would fail. Anything
    outside $HOME stays visible (read-only) via the whole-root bind, so
    it's safe as-is.
    """
    try:
        resolved = work_dir.resolve()
    except OSError:
        return project_root
    under_home = resolved == home or home in resolved.parents
    if not under_home:
        return resolved
    preserved = (
        resolved == project_root or project_root in resolved.parents
        or resolved == memory_root or memory_root in resolved.parents
    )
    return resolved if preserved else project_root


def _build_bwrap_argv(command: str, work_dir: Path) -> list[str]:
    project_root = config.project_root().resolve()
    memory_root = config.memory_root().resolve()
    home = Path.home().resolve()
    chdir_target = _resolve_chdir(work_dir, project_root, memory_root, home)

    # Order matters: every broad "blank this whole tree" op must come
    # BEFORE the specific restores/masks nested under it, or a later blank
    # silently wipes an earlier restore. $HOME and /tmp are blanked first
    # (regardless of whether project_root/memory_root happen to live under
    # either of them — they might, e.g. in a test environment using a
    # tempdir under /tmp), then the specific paths that need to survive
    # are bound back on top, then secret masks are layered on top of THAT.
    argv = [
        BWRAP_BIN,
        "--ro-bind", "/", "/",
        "--tmpfs", str(home),
        "--tmpfs", "/tmp",
        "--bind", str(project_root), str(project_root),
    ]
    if memory_root != project_root and not str(memory_root).startswith(str(project_root) + os.sep):
        argv += ["--bind", str(memory_root), str(memory_root)]

    secrets_env = project_root / "config" / "secrets.env"
    if secrets_env.exists():
        argv += ["--ro-bind", "/dev/null", str(secrets_env)]
    for path in _SECRET_MASK_PATHS:
        if path.exists():
            argv += ["--ro-bind", "/dev/null", str(path)]

    argv += ["--clearenv"]
    for var in _ENV_ALLOWLIST:
        if var in os.environ:
            argv += ["--setenv", var, os.environ[var]]

    argv += [
        "--unshare-pid", "--unshare-uts", "--unshare-ipc",
        "--new-session", "--die-with-parent",
        "--chdir", str(chdir_target),
        "/bin/sh", "-c", command,
    ]
    return argv


def run_shell(command: str, cwd: str = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    """
    Run a shell command, sandboxed via bubblewrap when available.

    Args:
        command: Shell command to run.
        cwd: Working directory (defaults to project root).
        timeout: Max seconds to wait (default 60, max 300).

    Returns:
        Combined stdout + stderr output.
    """
    blocked = _is_blocked(command)

    # Always audit shell executions
    try:
        from relay.session_log import log_shell_exec
        log_shell_exec(command, blocked=blocked is not None)
    except Exception:
        pass

    if blocked:
        return f"Blocked: command matches restricted pattern '{blocked}'. This operation is not allowed."

    work_dir = Path(cwd) if cwd else config.project_root()
    timeout = min(timeout, 300)

    if BWRAP_BIN:
        argv = _build_bwrap_argv(command, work_dir)
        run_kwargs = {"capture_output": True, "text": True, "timeout": timeout}
    elif _require_sandbox():
        logger.error("run_shell blocked: bubblewrap (bwrap) not found and shell.require_sandbox is true")
        return (
            "Error: bubblewrap (bwrap) is not installed, and shell.require_sandbox is true "
            "(the default) — refusing to run this command unsandboxed rather than silently "
            "falling back to unguarded host access. Install bubblewrap (Linux-only), or set "
            "settings.json's shell.require_sandbox to false to explicitly accept running "
            "unsandboxed (e.g. for local development on a non-Linux machine)."
        )
    else:
        logger.warning(f"run_shell running UNSANDBOXED (bwrap unavailable, shell.require_sandbox=false): {command[:100]}")
        argv = command
        run_kwargs = {"shell": True, "cwd": str(work_dir), "capture_output": True, "text": True, "timeout": timeout}

    try:
        result = subprocess.run(argv, **run_kwargs)

        # Update audit with exit code
        try:
            from relay.session_log import log_shell_exec
            log_shell_exec(command, blocked=False, exit_code=result.returncode)
        except Exception:
            pass

        output_parts = []
        if result.stdout.strip():
            output_parts.append(result.stdout.strip())
        if result.stderr.strip():
            output_parts.append(f"[stderr]\n{result.stderr.strip()}")
        if result.returncode != 0:
            output_parts.append(f"[exit code: {result.returncode}]")

        return "\n".join(output_parts) if output_parts else "(no output)"

    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout} seconds."
    except Exception as e:
        return f"Error: {str(e)}"
