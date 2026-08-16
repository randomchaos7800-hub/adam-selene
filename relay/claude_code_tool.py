"""
Claude Code tool for the agent.

Spawns a Claude Code subprocess in the sandbox and returns its output.
The agent can use this to request code, tools, or analysis from Claude Code.
"""

import subprocess
import shutil
from pathlib import Path

from relay import config

CLAUDE_BIN = shutil.which("claude")
DEFAULT_TIMEOUT = 300  # 5 minutes


def _sandbox_dir() -> Path:
    return config.project_root() / "sandbox"


def run_claude_code(prompt: str, context: str = None, subdir: str = None) -> str:
    """
    Run Claude Code non-interactively in the sandbox.

    Args:
        prompt: What to ask Claude Code to build or do.
        context: Optional prior output or context to prepend to the prompt.
        subdir: Optional subdirectory within sandbox to work in (e.g. 'projects/mytool').

    Returns:
        Claude Code's output as a string.
    """
    if not CLAUDE_BIN:
        return "Error: claude CLI not found on PATH. Is Claude Code installed?"

    sandbox = _sandbox_dir()

    # Resolve working directory — subdir is model-supplied, so it's
    # untrusted input. A value like "../../.." would otherwise escape the
    # sandbox entirely, and mkdir(parents=True) would happily create the
    # escaped path on disk before Claude Code runs non-interactively with
    # --dangerouslySkipPermissions inside it. Resolve first, then verify
    # containment via Path.parents membership (not a string-prefix check,
    # which a sibling directory name like "sandbox-evil" would spoof).
    if subdir:
        sandbox_root = sandbox.resolve()
        candidate = (sandbox / subdir).resolve()
        if candidate != sandbox_root and sandbox_root not in candidate.parents:
            return f"Error: subdir must stay inside the sandbox. {subdir!r} resolves outside {sandbox_root}."
        work_dir = candidate
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = sandbox

    # Build full prompt
    full_prompt = prompt
    if context:
        full_prompt = f"Context from previous step:\n\n{context}\n\n---\n\n{full_prompt}"

    cmd = [
        CLAUDE_BIN,
        "--dangerouslySkipPermissions",
        "--print",
        full_prompt,
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT,
        )

        output = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0 and not output:
            return f"Claude Code exited with code {result.returncode}.\n{stderr}"

        # Include stderr warnings if present but not overwhelming
        if stderr and len(stderr) < 500:
            return f"{output}\n\n[stderr: {stderr}]" if output else f"[stderr: {stderr}]"

        return output or "(Claude Code produced no output)"

    except subprocess.TimeoutExpired:
        return f"Error: Claude Code timed out after {DEFAULT_TIMEOUT} seconds."
    except Exception as e:
        return f"Error running Claude Code: {str(e)}"
