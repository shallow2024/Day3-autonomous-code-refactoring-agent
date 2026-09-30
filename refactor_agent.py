"""A guarded autonomous Python refactoring loop.

The agent validates a target with ast, runs it in a bounded subprocess, asks Groq
for a complete corrected source file, and repeats for at most three attempts.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_ATTEMPTS = 3
DEFAULT_TIMEOUT = 10
MAX_SOURCE_CHARS = 120_000


class AgentError(RuntimeError):
    """Expected, user-facing agent error."""


@dataclass
class ExecutionResult:
    passed: bool
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


class CompletionClient(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str: ...


class GroqClient:
    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        try:
            from groq import Groq
        except ImportError as exc:
            raise AgentError("Install dependencies first: py -m pip install -r requirements.txt") from exc
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise AgentError("GROQ_API_KEY is missing. Set it in PowerShell before using --apply.")
        self._client = Groq(api_key=api_key)
        self.model = model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            temperature=0,
            max_tokens=12_000,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise AgentError("Groq returned an empty response.")
        return content


SYSTEM_PROMPT = """You are a careful Python code repair assistant.
Return ONLY the complete corrected Python source code between <fixed_code> and
</fixed_code>. Do not include explanations, Markdown fences, shell commands, or
changes unrelated to the reported failure. Preserve the program's intent.
Never add network calls, file deletion, credential access, subprocess calls, or
obfuscated code unless they already exist and are required by the fix.
"""


def validate_source(source: str) -> None:
    if len(source) > MAX_SOURCE_CHARS:
        raise AgentError(f"Target source exceeds the {MAX_SOURCE_CHARS:,}-character safety limit.")
    try:
        ast.parse(source)
    except SyntaxError as exc:
        raise AgentError(f"Syntax validation failed at line {exc.lineno}: {exc.msg}") from exc


def run_target(path: Path, timeout: int) -> ExecutionResult:
    try:
        completed = subprocess.run(
            [sys.executable, str(path)],
            cwd=path.parent,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return ExecutionResult(
            passed=completed.returncode == 0,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    except subprocess.TimeoutExpired as exc:
        return ExecutionResult(
            passed=False,
            returncode=None,
            stdout=exc.stdout or "",
            stderr=f"Process exceeded the {timeout}-second timeout.",
            timed_out=True,
        )


def format_failure(result: ExecutionResult) -> str:
    return (
        f"returncode={result.returncode}; timed_out={result.timed_out}\n"
        f"STDOUT:\n{result.stdout[-8_000:]}\n"
        f"STDERR:\n{result.stderr[-12_000:]}"
    )


def extract_fixed_code(response: str) -> str:
    match = re.search(r"<fixed_code>\s*(.*?)\s*</fixed_code>", response, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip() + "\n"
    fenced = re.search(r"```(?:python)?\s*(.*?)```", response, re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip() + "\n"
    raise AgentError("The model response did not contain a <fixed_code> block.")


def build_prompt(source: str, failure: str, attempt: int) -> str:
    return (
        f"Attempt {attempt} of {DEFAULT_ATTEMPTS}.\n"
        "Repair the following Python file so it executes successfully.\n\n"
        "Failure captured by the bounded subprocess:\n"
        f"{failure}\n\n"
        "Current source:\n<source>\n"
        f"{source}\n</source>"
    )


def create_backup(path: Path) -> Path:
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    return backup


def refactor_file(
    target: Path,
    client: CompletionClient,
    *,
    max_attempts: int = DEFAULT_ATTEMPTS,
    timeout: int = DEFAULT_TIMEOUT,
    apply: bool = False,
) -> int:
    target = target.resolve()
    if target.suffix != ".py":
        raise AgentError("The target must be a .py file.")
    if not target.is_file():
        raise AgentError(f"Target file was not found: {target}")

    source = target.read_text(encoding="utf-8")
    validate_source(source)
    backup = create_backup(target) if apply else None
    working = Path(tempfile.mkdtemp(prefix="day3-agent-")) / target.name
    working.write_text(source, encoding="utf-8")
    print(f"Target: {target}")
    if backup:
        print(f"Backup: {backup}")

    for attempt in range(1, max_attempts + 1):
        print(f"\nAttempt {attempt}/{max_attempts}: running target...")
        result = run_target(working, timeout)
        if result.passed:
            if apply:
                target.write_text(working.read_text(encoding="utf-8"), encoding="utf-8")
            print("Success: the target exits with code 0.")
            if result.stdout.strip():
                print(result.stdout.rstrip())
            return 0

        failure = format_failure(result)
        print(failure)
        if attempt == max_attempts:
            print("Stopped: maximum attempts reached; original target was not replaced.")
            return 1
        print("Asking Groq for a complete corrected source file...")
        candidate = extract_fixed_code(client.complete(SYSTEM_PROMPT, build_prompt(working.read_text(encoding="utf-8"), failure, attempt)))
        validate_source(candidate)
        working.write_text(candidate, encoding="utf-8")

    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely iterate on a Python file using Groq and subprocess feedback.")
    parser.add_argument("target", type=Path, help="Path to the Python file to inspect")
    parser.add_argument("--apply", action="store_true", help="Replace the target only after a successful run; creates .bak first")
    parser.add_argument("--dry-run", action="store_true", help="Run and request fixes, but never replace the target (default)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Groq model (default: {DEFAULT_MODEL})")
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_ATTEMPTS, choices=range(1, 4), metavar="1-3")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, choices=range(1, 61), metavar="SECONDS")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        client = GroqClient(args.model)
        return refactor_file(args.target, client, max_attempts=args.max_attempts, timeout=args.timeout, apply=args.apply)
    except AgentError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
