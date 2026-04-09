"""
Shell execution tool for the agent.

Lets the agent run commands on the host machine with guardrails against destructive operations.
"""

import subprocess
import re

from relay import config

# Hard-blocked patterns — commands that can't run regardless of context
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
    r"\.vault/",                          # vault directory
    r"ssh.*authorized_keys",              # SSH key modification
    r"sshd_config",                       # SSH config
    r"git\s+push\s+.*--force",           # force push
    r"curl\s+.*\|\s*(ba)?sh",            # curl pipe to shell (code injection)
    r"wget\s+.*\|\s*(ba)?sh",            # wget pipe to shell
    r"python[23]?\s+-c\s+['\"]import\s+os.*system",  # python os.system injection
    r"chmod\s+[0-7]*7[0-7]*\s+.*\.(sh|py)",  # make scripts world-executable
]

BLOCKED_RE = [re.compile(p) for p in BLOCKED_PATTERNS]
DEFAULT_TIMEOUT = 60


def _is_blocked(command: str) -> str | None:
    """Return the matched pattern string if blocked, else None."""
    for pattern in BLOCKED_RE:
        if pattern.search(command):
            return pattern.pattern
    return None


def run_shell(command: str, cwd: str = None, timeout: int = DEFAULT_TIMEOUT) -> str:
    """
    Run a shell command on the host machine.

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

    work_dir = cwd or str(config.project_root())
    timeout = min(timeout, 300)

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

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
