from pathlib import Path

import pytest

from refactor_agent import (
    ExecutionResult,
    extract_fixed_code,
    format_failure,
    refactor_file,
    run_target,
    validate_source,
)


def test_validate_source_accepts_python():
    validate_source("print('ok')\n")


def test_validate_source_rejects_syntax_error():
    with pytest.raises(Exception, match="Syntax validation failed"):
        validate_source("def broken(:\n")


def test_extract_fixed_code():
    assert extract_fixed_code("<fixed_code>\nprint('ok')\n</fixed_code>") == "print('ok')\n"


def test_extract_code_fence():
    assert extract_fixed_code("```python\nprint('ok')\n```") == "print('ok')\n"


def test_run_target_captures_runtime_error(tmp_path: Path):
    target = tmp_path / "broken.py"
    target.write_text("raise ValueError('boom')\n", encoding="utf-8")
    result = run_target(target, timeout=2)
    assert not result.passed
    assert "ValueError" in result.stderr


def test_run_target_success(tmp_path: Path):
    target = tmp_path / "ok.py"
    target.write_text("print('ok')\n", encoding="utf-8")
    result = run_target(target, timeout=2)
    assert result.passed
    assert result.stdout.strip() == "ok"


def test_format_failure_contains_streams():
    result = ExecutionResult(False, 1, "out", "err")
    text = format_failure(result)
    assert "STDOUT" in text and "STDERR" in text and "err" in text


def test_refactor_loop_applies_only_after_success(tmp_path: Path, capsys):
    target = tmp_path / "buggy.py"
    target.write_text("print(missing_name)\n", encoding="utf-8")

    class FakeClient:
        def complete(self, system_prompt: str, user_prompt: str) -> str:
            assert "NameError" in user_prompt
            return "<fixed_code>print('repaired')\n</fixed_code>"

    assert refactor_file(target, FakeClient(), apply=True, timeout=2) == 0
    assert target.read_text(encoding="utf-8") == "print('repaired')\n"
    assert target.with_suffix(".py.bak").read_text(encoding="utf-8") == "print(missing_name)\n"
    assert "Success" in capsys.readouterr().out
