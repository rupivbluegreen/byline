"""Tests for the deterministic half of byline.alignment (spec §5.3)."""

from __future__ import annotations

from pathlib import Path

from byline.alignment import (
    check_alignment,
    check_cli_flags_exist,
    check_commands_exist,
    check_dependencies_declared,
    check_env_vars_referenced,
    extract_documented_artifacts,
    run_deterministic_alignment,
    run_semantic_alignment,
)
from byline.models import AlignmentCheck, AlignmentFindings

FIXTURES = Path(__file__).parent / "fixtures"
ALIGNED = FIXTURES / "aligned_docs_repo"
MISALIGNED = FIXTURES / "misaligned_docs_repo"


# ---------------------------------------------------------------------------
# extract_documented_artifacts
# ---------------------------------------------------------------------------


def test_extract_documented_artifacts_simple() -> None:
    readme = (
        "# Demo\n\n"
        "## Requirements\n\n"
        "```\npip install httpx\n```\n\n"
        "## Configuration\n\n"
        "Set the `API_KEY` environment variable.\n\n"
        "## Usage\n\n"
        "```\n./setup.sh\npython app.py --port 8080\n```\n"
    )
    artefacts = extract_documented_artifacts(readme)
    assert "--port" in artefacts["cli_flags"]
    assert "API_KEY" in artefacts["env_vars"]
    assert "./setup.sh" in artefacts["commands"]
    assert "python" in artefacts["commands"]
    assert "httpx" in artefacts["dependencies"]


def test_extract_documented_artifacts_empty_readme() -> None:
    out = extract_documented_artifacts("")
    assert out == {"cli_flags": [], "env_vars": [], "commands": [], "dependencies": []}


def test_extract_documented_artifacts_filters_env_stoplist() -> None:
    # ``TODO`` and ``JSON`` look env-var-shaped but should be filtered.
    readme = "## Notes\n\nThis returns JSON. TODO: write more tests.\n"
    artefacts = extract_documented_artifacts(readme)
    assert "TODO" not in artefacts["env_vars"]
    assert "JSON" not in artefacts["env_vars"]


def test_extract_documented_artifacts_dedupes_and_sorts() -> None:
    readme = (
        "# Demo\n\n"
        "Use --port. Also --port again. And --alpha.\n\n"
        "```\nexport ZED_TOKEN=x\nexport ABLE_TOKEN=y\n```\n"
    )
    artefacts = extract_documented_artifacts(readme)
    assert artefacts["cli_flags"] == sorted(set(artefacts["cli_flags"]))
    assert artefacts["cli_flags"] == ["--alpha", "--port"]
    assert artefacts["env_vars"] == sorted(set(artefacts["env_vars"]))


# ---------------------------------------------------------------------------
# check_cli_flags_exist
# ---------------------------------------------------------------------------


def test_check_cli_flags_exist_aligned() -> None:
    assert check_cli_flags_exist(["--port"], ALIGNED) == []


def test_check_cli_flags_exist_missing() -> None:
    out = check_cli_flags_exist(["--missing"], ALIGNED)
    assert len(out) == 1
    assert isinstance(out[0], AlignmentCheck)
    assert out[0].kind == "cli_flag_documented_missing_in_code"
    assert out[0].source == "deterministic"
    assert out[0].severity == "notable"


def test_check_cli_flags_exist_skips_help_and_version() -> None:
    # Even though no .py file references --help, this is a Typer/Click default
    # and should not be flagged.
    assert check_cli_flags_exist(["--help", "--version"], ALIGNED) == []


# ---------------------------------------------------------------------------
# check_env_vars_referenced
# ---------------------------------------------------------------------------


def test_check_env_vars_referenced_aligned() -> None:
    assert check_env_vars_referenced(["API_KEY"], ALIGNED) == []


def test_check_env_vars_referenced_missing() -> None:
    out = check_env_vars_referenced(["MAGIC_TOKEN"], MISALIGNED)
    assert len(out) == 1
    assert out[0].kind == "env_var_documented_missing_in_code"
    assert out[0].severity == "notable"


# ---------------------------------------------------------------------------
# check_commands_exist
# ---------------------------------------------------------------------------


def test_check_commands_exist_aligned() -> None:
    assert check_commands_exist(["./setup.sh"], ALIGNED) == []


def test_check_commands_exist_missing() -> None:
    out = check_commands_exist(["./run-everything.sh"], MISALIGNED)
    assert len(out) == 1
    assert out[0].kind == "command_documented_missing_file"
    assert out[0].severity == "significant"


def test_check_commands_exist_skips_system_tools() -> None:
    # ``docker``, ``pip``, etc. cannot be verified from the repo and should
    # silently be skipped, not flagged as missing.
    assert check_commands_exist(["docker", "pip", "python"], MISALIGNED) == []


# ---------------------------------------------------------------------------
# check_dependencies_declared
# ---------------------------------------------------------------------------


def test_check_dependencies_declared_aligned() -> None:
    assert check_dependencies_declared(["httpx"], ALIGNED) == []


def test_check_dependencies_declared_missing() -> None:
    out = check_dependencies_declared(["rare-lib-xyz"], MISALIGNED)
    assert len(out) == 1
    assert out[0].kind == "dependency_documented_missing_in_manifest"
    assert out[0].severity == "notable"


# ---------------------------------------------------------------------------
# run_deterministic_alignment & run_semantic_alignment stub
# ---------------------------------------------------------------------------


def test_run_deterministic_alignment_aligned_repo() -> None:
    checks = run_deterministic_alignment(ALIGNED)
    # The aligned fixture is constructed so no deterministic check fires.
    assert checks == []


def test_run_deterministic_alignment_misaligned_repo() -> None:
    checks = run_deterministic_alignment(MISALIGNED)
    kinds = {c.kind for c in checks}
    # We expect to find the missing flag, env var, script, and dep.
    assert "cli_flag_documented_missing_in_code" in kinds
    assert "env_var_documented_missing_in_code" in kinds
    assert "command_documented_missing_file" in kinds
    assert "dependency_documented_missing_in_manifest" in kinds


def test_run_semantic_alignment_stub_returns_empty() -> None:
    # Pass a non-LLMProvider sentinel; run_alignment_semantic will route it
    # through its legacy raw-client path which then fails fast, and
    # run_semantic_alignment swallows the failure to return empty results.
    checks, summary = run_semantic_alignment(ALIGNED, provider=object())
    assert checks == []
    assert summary == ""


def test_run_deterministic_alignment_missing_readme(tmp_path: Path) -> None:
    # An empty directory has no README — the function should return an
    # empty list rather than raising.
    assert run_deterministic_alignment(tmp_path) == []


# ---------------------------------------------------------------------------
# check_alignment composition
# ---------------------------------------------------------------------------


def test_check_alignment_aligned() -> None:
    findings = check_alignment(ALIGNED)
    assert isinstance(findings, AlignmentFindings)
    assert findings.deterministic_only is True
    assert findings.overall_alignment == "aligned"
    assert findings.llm_summary is None


def test_check_alignment_misaligned() -> None:
    findings = check_alignment(MISALIGNED)
    assert isinstance(findings, AlignmentFindings)
    assert findings.deterministic_only is True
    # At least the four documented-but-missing artefacts should fire.
    assert len(findings.checks) >= 3
    assert findings.overall_alignment != "aligned"


def test_check_alignment_with_llm_but_no_client_stays_deterministic() -> None:
    # with_llm=True but anthropic_client=None should NOT run the LLM path.
    findings = check_alignment(MISALIGNED, with_llm=True, anthropic_client=None)
    assert findings.deterministic_only is True
    assert findings.llm_summary is None
