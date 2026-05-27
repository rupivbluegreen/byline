"""Tests for byline.disproportion — structural ratio signals per spec §7.3."""

from __future__ import annotations

from pathlib import Path

from byline.disproportion import (
    analyze,
    comment_density,
    diagram_count,
    doc_to_code_ratio,
    numbered_diagram_pattern,
)
from byline.models import DisproportionFinding
from tests.conftest import FIXTURES_DIR

SYNTHETIC_REPO = FIXTURES_DIR / "synthetic_ai_repo"


# ---------------------------------------------------------------------------
# numbered_diagram_pattern
# ---------------------------------------------------------------------------


def test_numbered_diagram_pattern_on_synthetic_repo() -> None:
    finding = numbered_diagram_pattern(SYNTHETIC_REPO)
    assert isinstance(finding, DisproportionFinding)
    assert finding.name == "numbered_diagram_pattern"
    # synthetic_ai_repo has diagram-01..06; the consecutive run is at least 5.
    assert finding.observed >= 5
    assert finding.severity == "significant"


def test_numbered_diagram_pattern_empty_repo(tmp_path: Path) -> None:
    finding = numbered_diagram_pattern(tmp_path)
    assert isinstance(finding, DisproportionFinding)
    assert finding.observed == 0.0
    assert finding.severity == "info"


# ---------------------------------------------------------------------------
# doc_to_code_ratio
# ---------------------------------------------------------------------------


def test_doc_to_code_ratio_significant(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("\n".join(f"line {i}" for i in range(500)) + "\n")
    app = tmp_path / "app.py"
    app.write_text("\n".join(f"x = {i}" for i in range(100)) + "\n")
    finding = doc_to_code_ratio(tmp_path)
    assert isinstance(finding, DisproportionFinding)
    assert finding.name == "doc_to_code_ratio"
    assert finding.observed == 5.0
    assert finding.severity == "significant"


def test_doc_to_code_ratio_no_code(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("only docs here\n")
    finding = doc_to_code_ratio(tmp_path)
    assert finding.observed == 0.0
    assert finding.severity == "info"


def test_doc_to_code_ratio_excludes_lockfiles_and_venv(tmp_path: Path) -> None:
    # Lockfile contents should NOT count as code.
    (tmp_path / "package-lock.json").write_text("\n".join("x" for _ in range(1000)) + "\n")
    # node_modules should be skipped wholesale.
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("\n".join("y" for _ in range(1000)) + "\n")
    # Real source — 10 lines.
    (tmp_path / "app.py").write_text("\n".join(f"x = {i}" for i in range(10)) + "\n")
    # Docs — 5 lines.
    (tmp_path / "README.md").write_text("a\nb\nc\nd\ne\n")
    finding = doc_to_code_ratio(tmp_path)
    # 5 doc lines / 10 code lines = 0.5; "> 0.5" is not satisfied, so info.
    assert finding.observed == 0.5
    assert finding.severity == "info"


# ---------------------------------------------------------------------------
# comment_density
# ---------------------------------------------------------------------------


def test_comment_density_significant(tmp_path: Path) -> None:
    src = tmp_path / "script.py"
    src.write_text("# c1\n# c2\n# c3\n# c4\n# c5\nx = 1\ny = 2\nz = 3\na = 4\nb = 5\n")
    finding = comment_density(tmp_path)
    assert isinstance(finding, DisproportionFinding)
    assert finding.name == "comment_density"
    assert finding.observed == 0.5
    assert finding.severity == "significant"


def test_comment_density_no_code_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# heading\nbody\n")
    finding = comment_density(tmp_path)
    assert finding.observed == 0.0
    assert finding.severity == "info"


def test_comment_density_block_comment(tmp_path: Path) -> None:
    src = tmp_path / "script.js"
    src.write_text(
        "/* block start\n * inside block\n * still inside\n */\nconst x = 1;\nconst y = 2;\n"
    )
    finding = comment_density(tmp_path)
    # 4 lines inside the block are comments, plus 0 line comments; 2 code lines.
    # Total lines = 6; comments = 4; observed ~ 0.666... -> significant.
    assert finding.observed > 0.4
    assert finding.severity == "significant"


# ---------------------------------------------------------------------------
# diagram_count
# ---------------------------------------------------------------------------


def test_diagram_count_on_synthetic_repo() -> None:
    finding = diagram_count(SYNTHETIC_REPO)
    assert isinstance(finding, DisproportionFinding)
    assert finding.name == "diagram_count"
    assert finding.observed >= 6
    assert finding.severity == "significant"


def test_diagram_count_empty_repo(tmp_path: Path) -> None:
    finding = diagram_count(tmp_path)
    assert finding.observed == 0.0
    assert finding.severity == "info"


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


def test_analyze_returns_four_findings_on_synthetic_repo() -> None:
    findings = analyze(SYNTHETIC_REPO)
    assert isinstance(findings, list)
    assert len(findings) == 4
    assert all(isinstance(f, DisproportionFinding) for f in findings)
    names = {f.name for f in findings}
    assert names == {
        "doc_to_code_ratio",
        "diagram_count",
        "numbered_diagram_pattern",
        "comment_density",
    }
