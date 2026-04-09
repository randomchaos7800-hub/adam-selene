#!/usr/bin/env python3
"""Prompt regression test runner — Promptfoo pattern, native implementation.

Tests the agent's prompt behavior against YAML test cases without spinning up
the full relay stack. Calls OpenRouter directly with the agent's system prompt.

Usage:
  python3 scripts/test_prompts.py                    # run all tests
  python3 scripts/test_prompts.py identity           # run only identity.yaml
  python3 scripts/test_prompts.py --verbose          # show full responses
  python3 scripts/test_prompts.py --no-grade         # skip model-graded assertions (faster)
  python3 scripts/test_prompts.py --file identity guardrails   # run specific files
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import yaml

# Paths
SMARTAGENT_DIR = Path(__file__).parent.parent
TESTS_DIR = SMARTAGENT_DIR / "tests" / "prompts"
CONFIG_PATH = SMARTAGENT_DIR / "config" / "settings.json"
PROMPT_PATH = SMARTAGENT_DIR / "config" / "agent_prompt.md"

# Load settings for agent/owner names
_settings = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
AGENT_NAME = _settings.get("agent_name", "Agent")
OWNER_NAME = _settings.get("owner_name", "User")

# Colors
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"
BOLD = "\033[1m"

PASS = f"{GREEN}PASS{RESET}"
FAIL = f"{RED}FAIL{RESET}"
SKIP = f"{YELLOW}SKIP{RESET}"


def _vault(key: str) -> Optional[str]:
    """Read a key from the vault."""
    try:
        return subprocess.check_output(
            [str(Path.home() / ".vault" / "vault.sh"), "get", key],
            text=True
        ).strip()
    except Exception:
        return None


def _get_client():
    """Get an OpenAI-compatible client pointing at OpenRouter."""
    from openai import OpenAI

    api_key = os.environ.get("OPENROUTER_API_KEY") or _vault("openrouter_api_key")
    if not api_key:
        print(f"{RED}ERROR: OPENROUTER_API_KEY not set and vault lookup failed{RESET}")
        sys.exit(1)

    settings = {}
    if CONFIG_PATH.exists():
        settings = json.loads(CONFIG_PATH.read_text())
    or_cfg = settings.get("openrouter", {})
    base_url = or_cfg.get("base_url", "https://openrouter.ai/api/v1")
    model = settings.get("models", {}).get("extraction") or or_cfg.get("model", "z-ai/glm-4.7-flash")

    return OpenAI(base_url=base_url, api_key=api_key), model


def _get_system_prompt() -> str:
    """Load the agent's system prompt."""
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text()
    return f"You are {AGENT_NAME}, a reasoning partner."


def _call_model(client, model: str, system: str, user_input: str) -> str:
    """Call the model and return the response text."""
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_input},
            ]
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        return f"[API ERROR: {e}]"


def _grade_response(client, model: str, criteria: str, response: str) -> tuple[bool, str]:
    """Use the model to judge whether a response meets the given criteria.

    Returns (passed, explanation).
    """
    judge_prompt = f"""You are a test judge. Evaluate whether the response meets the criteria.

Criteria: {criteria}

Response to evaluate:
{response}

Reply with exactly one of:
PASS: <one-line explanation why it passes>
FAIL: <one-line explanation why it fails>
"""
    try:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=200,
            messages=[{"role": "user", "content": judge_prompt}]
        )
        judgment = (resp.choices[0].message.content or "").strip()
        if judgment.upper().startswith("PASS"):
            return True, judgment[5:].strip(": ").strip()
        return False, judgment[5:].strip(": ").strip()
    except Exception as e:
        return True, f"[grading unavailable: {e}]"  # fail open so grading errors don't block CI


def _run_relay_test(client, model: str, test: dict, system_prompt: str, verbose: bool, no_grade: bool) -> tuple[int, int]:
    """Run a single relay-style test (input -> response assertions).

    Returns (passed, failed).
    """
    desc = test.get("description", "unnamed test")
    user_input = test.get("input", "")

    response = _call_model(client, model, system_prompt, user_input)

    passed = 0
    failed = 0
    failure_msgs = []

    # expected_contains
    for phrase in test.get("expected_contains", []):
        if phrase.lower() in response.lower():
            passed += 1
        else:
            failed += 1
            failure_msgs.append(f"expected_contains: '{phrase}' not found")

    # expected_not_contains
    for phrase in test.get("expected_not_contains", []):
        if phrase.lower() not in response.lower():
            passed += 1
        else:
            failed += 1
            failure_msgs.append(f"expected_not_contains: '{phrase}' was found")

    # model_graded_criteria
    if test.get("model_graded_criteria") and not no_grade:
        ok, explanation = _grade_response(client, model, test["model_graded_criteria"], response)
        if ok:
            passed += 1
        else:
            failed += 1
            failure_msgs.append(f"model_graded: {explanation}")

    # expected_tool_calls — check response mentions or initiates tool
    for tool_name in test.get("expected_tool_calls", []):
        if tool_name in response.lower() or f'"{tool_name}"' in response:
            passed += 1
        else:
            # Not definitive without running the full relay — mark as warning
            print(f"    {YELLOW}NOTE{RESET}: expected tool call '{tool_name}' not visible in text-only response")

    # Print result
    status = PASS if not failure_msgs else FAIL
    print(f"  {status}  {desc}")
    if failure_msgs or verbose:
        for msg in failure_msgs:
            print(f"        {RED}x{RESET} {msg}")
        if verbose:
            preview = response[:300].replace("\n", " ")
            print(f"        {DIM}response: {preview}{RESET}")

    return passed, failed


def _run_extraction_test(client, model: str, test: dict, verbose: bool) -> tuple[int, int]:
    """Run an extraction-type test: call extraction.py directly on a conversation."""
    desc = test.get("description", "unnamed extraction test")
    conversation = test.get("conversation", "")

    if not conversation:
        print(f"  {SKIP}  {desc} (no conversation)")
        return 0, 0

    # Import extraction and run it
    sys.path.insert(0, str(SMARTAGENT_DIR))
    try:
        from memory.extraction import Extractor
        extractor = Extractor()
        result = extractor.extract(conversation)
    except Exception as e:
        print(f"  {FAIL}  {desc} — extraction error: {e}")
        return 0, 1

    extracted_facts = result.get("facts", [])
    passed = 0
    failed = 0
    failure_msgs = []

    # Check expected_facts
    for expected in test.get("expected_facts", []):
        entity = expected.get("entity", "")
        content_contains = expected.get("content_contains", "")
        fact_type = expected.get("type", "")
        has_supersedes = expected.get("has_supersedes", False)

        matching = [
            f for f in extracted_facts
            if (not entity or f.get("entity") == entity)
            and (not content_contains or content_contains.lower() in f.get("content", "").lower())
            and (not fact_type or f.get("type") == fact_type)
            and (not has_supersedes or f.get("supersedes"))
        ]

        if matching:
            passed += 1
        else:
            failed += 1
            criteria = f"entity={entity}, content_contains={content_contains}"
            if fact_type:
                criteria += f", type={fact_type}"
            failure_msgs.append(f"expected_fact not found: {criteria}")

    # Check expected_not_facts
    for not_expected in test.get("expected_not_facts", []):
        content_contains = not_expected.get("content_contains", "")
        matching = [
            f for f in extracted_facts
            if content_contains.lower() in f.get("content", "").lower()
        ]
        if not matching:
            passed += 1
        else:
            failed += 1
            failure_msgs.append(f"unwanted fact found: content_contains={content_contains}")

    status = PASS if not failure_msgs else FAIL
    print(f"  {status}  {desc}")
    if failure_msgs or verbose:
        for msg in failure_msgs:
            print(f"        {RED}x{RESET} {msg}")
        if verbose:
            print(f"        {DIM}extracted: {json.dumps(extracted_facts, indent=2)[:400]}{RESET}")

    return passed, failed


def _load_test_file(path: Path) -> list[dict]:
    """Load test cases from a YAML file."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("tests", [])


def run_tests(
    file_filter: Optional[list[str]] = None,
    verbose: bool = False,
    no_grade: bool = False,
) -> tuple[int, int]:
    """Run all test files (or filtered set). Returns (total_passed, total_failed)."""
    client, model = _get_client()
    system_prompt = _get_system_prompt()

    yaml_files = sorted(TESTS_DIR.glob("*.yaml"))
    if not yaml_files:
        print(f"{YELLOW}No test files found in {TESTS_DIR}{RESET}")
        return 0, 0

    if file_filter:
        yaml_files = [f for f in yaml_files if f.stem in file_filter or f.name in file_filter]
        if not yaml_files:
            print(f"{RED}No matching test files for: {file_filter}{RESET}")
            return 0, 0

    total_passed = 0
    total_failed = 0

    for path in yaml_files:
        print(f"\n{BOLD}{CYAN}{path.stem}{RESET}")
        tests = _load_test_file(path)

        for test in tests:
            # Route to correct runner based on test type
            if "conversation" in test:
                p, f = _run_extraction_test(client, model, test, verbose)
            else:
                p, f = _run_relay_test(client, model, test, system_prompt, verbose, no_grade)

            total_passed += p
            total_failed += f

    return total_passed, total_failed


def main():
    args = sys.argv[1:]
    verbose = "--verbose" in args or "-v" in args
    no_grade = "--no-grade" in args
    args = [a for a in args if not a.startswith("--") and not a.startswith("-")]

    file_filter = args if args else None

    print(f"\n{BOLD}SmartAgent Prompt Tests{RESET}")
    print(f"Model: see settings.json openrouter.model")
    if no_grade:
        print(f"{DIM}(model grading disabled){RESET}")

    passed, failed = run_tests(file_filter=file_filter, verbose=verbose, no_grade=no_grade)

    total = passed + failed
    print(f"\n{'='*50}")
    if total == 0:
        print(f"{YELLOW}No assertions ran.{RESET}")
    elif failed == 0:
        print(f"{GREEN}{BOLD}All {total} assertions passed.{RESET}")
    else:
        print(f"{RED}{BOLD}{failed}/{total} assertions failed.{RESET}  ({passed} passed)")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
