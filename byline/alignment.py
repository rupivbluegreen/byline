"""Documentation-implementation alignment checks. See spec §5.3.

A README is a promise. Code is the delivery. When a project's documentation
claims a flag, environment variable, helper script, or dependency that the
code never actually references, that gap is worth surfacing — not as a
verdict on authorship, but as a comparative signal a reviewer can act on.

This module implements the *deterministic* half of the alignment check:
parse the README for documented artefacts (CLI flags, env vars, commands,
dependencies), then walk the repo to confirm each one is plausibly present.
The semantic / LLM-driven half lands in a later task; the public entry
point already accepts an ``anthropic_client`` parameter so that the
deterministic checks can be composed with semantic checks without changing
callers later.

None of the individual checks is a verdict. They feed a coarse rollup
(``aligned`` / ``minor_gaps`` / ``significant_gaps``) that exists to
prioritise reviewer attention, nothing more.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from byline.models import AlignmentCheck, AlignmentFindings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# CLI flags conventionally provided by Typer/Click without authors having to
# wire them up. Documenting these in the README is not a gap signal.
_BUILTIN_CLI_FLAGS: frozenset[str] = frozenset({"--help", "--version"})

# Tokens that look like env vars by shape (all-caps with underscores or digits)
# but are common prose abbreviations. These are filtered out before we even
# try to confirm contextual evidence.
_ENV_VAR_STOPLIST: frozenset[str] = frozenset(
    {
        "TODO",
        "FIXME",
        "WARNING",
        "ERROR",
        "NOTE",
        "BEFORE",
        "AFTER",
        "API",
        "JSON",
        "XML",
        "HTML",
        "CSS",
        "URL",
        "SQL",
        "GET",
        "POST",
        "PUT",
        "DELETE",
        "HEAD",
        "MIT",
        "BSD",
        "GPL",
        "OS",
        "CI",
        "CD",
        "README",
        "FAQ",
        "TLS",
        "SSL",
        "TCP",
        "UDP",
        "HTTP",
        "HTTPS",
        "YAML",
        "TOML",
        "MD",
    }
)

# Shell commands worth recognising as the leading token of a documented
# command line. Anything starting with ``./`` is treated separately as a
# project-local script reference.
_KNOWN_SHELL_COMMANDS: frozenset[str] = frozenset(
    {
        "docker",
        "docker-compose",
        "pip",
        "pip3",
        "npm",
        "yarn",
        "pnpm",
        "python",
        "python3",
        "node",
        "go",
        "cargo",
        "make",
        "sh",
        "bash",
    }
)

# Files in which we are willing to look for CLI flag references.
_CLI_FLAG_SEARCH_EXTS: frozenset[str] = frozenset({".py"})

# Files in which env var references plausibly live.
_ENV_VAR_SEARCH_EXTS: frozenset[str] = frozenset(
    {".py", ".sh", ".yml", ".yaml", ".toml", ".cfg", ".ini"}
)

# Manifest files we know how to read for dependency declarations, in
# priority order. We read whichever one is present; multiple matches are
# fine — we OR their contents.
_DEPENDENCY_MANIFESTS: tuple[str, ...] = (
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "go.mod",
    "Cargo.toml",
)

# Directories that should never be walked when looking for code that
# references documented artefacts. Mirrors the boilerplate module in spirit.
_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "vendor",
        "__pycache__",
        "dist",
        "build",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
    }
)

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Match CLI flags like ``--port`` or ``--legacy-mode``. The negative
# lookbehind rules out matches embedded inside identifiers.
_CLI_FLAG_RE = re.compile(r"(?<!\w)--[a-z][a-z0-9-]*")

# Match plausibly env-var-shaped tokens.
_ENV_VAR_CANDIDATE_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")

# Fenced code block (``` ... ```), capturing the inner body.
_FENCED_CODE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

# Pip/npm/yarn/pnpm install lines inside code blocks (or anywhere we find them).
_INSTALL_RE = re.compile(
    r"(?:pip|pip3|npm|yarn|pnpm)\s+(?:install|add)\s+([\w@/.\-]+)",
    re.IGNORECASE,
)

# A heading line. Markdown headings start with one or more ``#``.
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+(.+?)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iter_repo_files(repo_path: Path, extensions: frozenset[str]) -> list[Path]:
    """Walk ``repo_path`` and return files whose suffix is in ``extensions``."""

    if not repo_path.exists() or not repo_path.is_dir():
        return []
    out: list[Path] = []
    for candidate in repo_path.rglob("*"):
        try:
            rel_parts = candidate.relative_to(repo_path).parts
        except ValueError:
            continue
        if any(part in _EXCLUDE_DIRS for part in rel_parts[:-1]):
            continue
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in extensions:
            continue
        out.append(candidate)
    return out


def _iter_env_var_search_files(repo_path: Path) -> list[Path]:
    """Files to scan when looking for env-var references.

    The extension-based filter above misses files like ``.env``,
    ``.env.example``, and ``docker-compose.yml`` (the latter is covered by
    the ``.yml`` suffix but the former does not have a suffix). We pick those
    up by name as well.
    """

    files = _iter_repo_files(repo_path, _ENV_VAR_SEARCH_EXTS)
    if repo_path.exists() and repo_path.is_dir():
        for candidate in repo_path.rglob("*"):
            try:
                rel_parts = candidate.relative_to(repo_path).parts
            except ValueError:
                continue
            if any(part in _EXCLUDE_DIRS for part in rel_parts[:-1]):
                continue
            if not candidate.is_file():
                continue
            name = candidate.name
            if name.startswith(".env") or name.startswith("docker-compose"):
                if candidate not in files:
                    files.append(candidate)
    return files


def _read_text(path: Path) -> str:
    """Read a file as UTF-8 with replacement; return empty string on error."""

    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _strip_fenced_code(text: str) -> tuple[str, list[str]]:
    """Return ``(text_without_code_blocks, list_of_code_block_bodies)``."""

    blocks: list[str] = []

    def _grab(match: re.Match[str]) -> str:
        blocks.append(match.group(1))
        return " "

    stripped = _FENCED_CODE_RE.sub(_grab, text)
    return stripped, blocks


def _env_var_has_context(token: str, readme_text: str) -> bool:
    """Return True if the token plausibly refers to an env var.

    A token earns env-var context if any of its occurrences in the README:

    * is preceded by ``$`` or ``${`` (shell expansion);
    * appears within 30 characters of the words ``env``, ``environ``,
      ``getenv``, or ``export``;
    * sits inside a fenced code block (where it is almost certainly a
      shell or config token, not prose).
    """

    if not token:
        return False
    # Strip and capture code blocks; treat all tokens inside any code block
    # as having context.
    _stripped, code_blocks = _strip_fenced_code(readme_text)
    pattern = re.compile(rf"\b{re.escape(token)}\b")
    for block in code_blocks:
        if pattern.search(block):
            return True

    # Now look at prose occurrences for nearby context cues.
    for match in pattern.finditer(readme_text):
        start, end = match.start(), match.end()
        # ``$`` / ``${`` immediately before the token.
        prefix_window = readme_text[max(0, start - 2) : start]
        if prefix_window.endswith("$") or prefix_window.endswith("${"):
            return True
        # Nearby context cues within 30 characters.
        ctx_start = max(0, start - 30)
        ctx_end = min(len(readme_text), end + 30)
        ctx = readme_text[ctx_start:ctx_end].lower()
        if any(cue in ctx for cue in ("env", "environ", "getenv", "export")):
            return True
    return False


def _split_dependency_token(token: str) -> str:
    """Strip version specifiers and extras from a dependency token.

    ``httpx>=0.27`` -> ``httpx``; ``requests[security]`` -> ``requests``;
    ``@scope/pkg@1.0`` -> ``@scope/pkg``.
    """

    if not token:
        return token
    # Trim extras like ``[security]``.
    token = re.split(r"\[", token, maxsplit=1)[0]
    # Trim version specifiers — anything from the first version operator on.
    token = re.split(r"[<>=!~ ]", token, maxsplit=1)[0]
    # For npm-style ``pkg@1.0`` (but keep leading ``@`` of scoped packages).
    if token.startswith("@"):
        # ``@scope/pkg@1.0`` -> split on the second ``@``.
        rest = token[1:]
        if "@" in rest:
            rest = rest.split("@", 1)[0]
        token = "@" + rest
    else:
        token = token.split("@", 1)[0]
    return token.strip()


# ---------------------------------------------------------------------------
# Public extraction API
# ---------------------------------------------------------------------------


def extract_documented_artifacts(readme_text: str) -> dict[str, list[str]]:
    """Extract artefacts a README claims the project supports.

    Returns a dict with keys ``cli_flags``, ``env_vars``, ``commands``,
    ``dependencies``. Each value is a sorted, deduplicated list.

    The extraction is intentionally lenient — false positives here become
    extra alignment checks that may or may not fire, while false negatives
    silently drop signal. Callers should expect a noisy but useful set.
    """

    if not readme_text:
        return {"cli_flags": [], "env_vars": [], "commands": [], "dependencies": []}

    # CLI flags --------------------------------------------------------------
    cli_flags = sorted({m.group(0) for m in _CLI_FLAG_RE.finditer(readme_text)})

    # Env vars ---------------------------------------------------------------
    env_candidates: set[str] = {m.group(0) for m in _ENV_VAR_CANDIDATE_RE.finditer(readme_text)}
    env_vars_filtered: list[str] = []
    for token in env_candidates:
        if token in _ENV_VAR_STOPLIST:
            continue
        if not _env_var_has_context(token, readme_text):
            continue
        env_vars_filtered.append(token)
    env_vars = sorted(set(env_vars_filtered))

    # Commands ---------------------------------------------------------------
    commands: set[str] = set()
    for match in _FENCED_CODE_RE.finditer(readme_text):
        body = match.group(1)
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            # Trim optional shell prompt prefix.
            if line.startswith("$ "):
                line = line[2:].strip()
            first_token = line.split()[0] if line.split() else ""
            if not first_token:
                continue
            if first_token.startswith("./"):
                commands.add(first_token)
            elif first_token in _KNOWN_SHELL_COMMANDS:
                commands.add(first_token)

    # Dependencies -----------------------------------------------------------
    deps: set[str] = set()
    # 1. ``pip install X`` / ``npm install X`` / ``yarn add X`` anywhere.
    for match in _INSTALL_RE.finditer(readme_text):
        deps.add(_split_dependency_token(match.group(1)))
    # 2. Lines under Install / Requirements / Dependencies headings that look
    #    like bare package names. We only consume the section until the next
    #    heading.
    headings = list(_HEADING_RE.finditer(readme_text))
    for i, heading in enumerate(headings):
        title = heading.group(1).lower()
        if not any(key in title for key in ("install", "requirement", "dependenc")):
            continue
        section_start = heading.end()
        section_end = headings[i + 1].start() if i + 1 < len(headings) else len(readme_text)
        section = readme_text[section_start:section_end]
        # Capture bare-token lines that look like package names.
        for raw_line in section.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # Skip code-fence delimiters; the install regex above already
            # handled their contents.
            if line.startswith("```"):
                continue
            # Skip prose-looking lines.
            if " " in line and not line.startswith(("- ", "* ")):
                continue
            # List bullets like ``- httpx``.
            if line.startswith(("- ", "* ")):
                line = line[2:].strip()
            # Accept tokens that look like package identifiers.
            if re.fullmatch(r"[\w@/.\-]+", line):
                token = _split_dependency_token(line)
                if token and not token.startswith("-"):
                    deps.add(token)

    dependencies = sorted({d for d in deps if d})

    return {
        "cli_flags": cli_flags,
        "env_vars": env_vars,
        "commands": sorted(commands),
        "dependencies": dependencies,
    }


# ---------------------------------------------------------------------------
# Individual checkers
# ---------------------------------------------------------------------------


def check_cli_flags_exist(flags: list[str], repo_path: Path) -> list[AlignmentCheck]:
    """For each documented flag, confirm at least one ``.py`` file references it.

    Skips Typer/Click defaults (``--help``, ``--version``). Uses a literal
    substring search — Typer's parameter-name-to-flag synthesis is not
    modelled here; if a project documents ``--port`` and the code only ever
    declares ``port: int = typer.Option(...)`` without mentioning ``--port``
    in any comment, decorator, or string, the flag will be reported as a
    gap. That is acceptable noise: a real CLI almost always mentions its
    flags in tests, help strings, or comments.
    """

    repo_path = Path(repo_path)
    py_files = _iter_repo_files(repo_path, _CLI_FLAG_SEARCH_EXTS)
    file_texts: list[str] = [_read_text(f) for f in py_files]

    checks: list[AlignmentCheck] = []
    for flag in flags:
        if flag in _BUILTIN_CLI_FLAGS:
            continue
        if any(flag in text for text in file_texts):
            continue
        checks.append(
            AlignmentCheck(
                kind="cli_flag_documented_missing_in_code",
                source="deterministic",
                description=f"README documents `{flag}` but no .py file references it",
                doc_location="README.md",
                code_location=None,
                severity="notable",
            )
        )
    return checks


def check_env_vars_referenced(env_vars: list[str], repo_path: Path) -> list[AlignmentCheck]:
    """For each documented env var, confirm the code or config references it.

    Looks across ``.py``, ``.sh``, ``.yml``, ``.yaml``, ``.toml``, ``.cfg``,
    ``.ini``, and ``.env*`` / ``docker-compose*`` files for any of the usual
    forms: ``os.environ``/``os.getenv``/``getenv``, shell expansion
    ``$VAR`` / ``${VAR}``, or a literal ``VAR=`` assignment.
    """

    repo_path = Path(repo_path)
    files = _iter_env_var_search_files(repo_path)
    file_texts: list[str] = [_read_text(f) for f in files]

    checks: list[AlignmentCheck] = []
    for env_var in env_vars:
        patterns = (
            f'environ["{env_var}"]',
            f"environ['{env_var}']",
            f"environ.get({env_var!r})",
            f'environ.get("{env_var}")',
            f"environ.get('{env_var}')",
            f"getenv({env_var!r})",
            f'getenv("{env_var}")',
            f"getenv('{env_var}')",
            f"${env_var}",
            f"${{{env_var}}}",
            f"{env_var}=",
        )
        found = False
        for text in file_texts:
            if any(p in text for p in patterns):
                found = True
                break
        if found:
            continue
        checks.append(
            AlignmentCheck(
                kind="env_var_documented_missing_in_code",
                source="deterministic",
                description=(
                    f"README documents env var `{env_var}` but no code or config file references it"
                ),
                doc_location="README.md",
                code_location=None,
                severity="notable",
            )
        )
    return checks


def check_commands_exist(commands: list[str], repo_path: Path) -> list[AlignmentCheck]:
    """Confirm that ``./script.sh``-style commands point at real files.

    System tools (``docker``, ``pip``, etc.) cannot be checked from the
    repo alone and are skipped silently.
    """

    repo_path = Path(repo_path)
    checks: list[AlignmentCheck] = []
    for command in commands:
        if not command.startswith("./"):
            continue
        rel = command[2:]
        if not rel:
            continue
        target = repo_path / rel
        if target.exists():
            continue
        checks.append(
            AlignmentCheck(
                kind="command_documented_missing_file",
                source="deterministic",
                description=(
                    f"README documents `{command}` but `{rel}` does not exist in the repo"
                ),
                doc_location="README.md",
                code_location=None,
                severity="significant",
            )
        )
    return checks


def check_dependencies_declared(deps: list[str], repo_path: Path) -> list[AlignmentCheck]:
    """Confirm each documented dependency appears in a manifest file.

    We OR the text of every manifest we find — a project that has both a
    ``pyproject.toml`` and a ``requirements.txt`` only needs the dep in
    one of them for the check to pass.
    """

    repo_path = Path(repo_path)
    manifest_texts: list[str] = []
    for name in _DEPENDENCY_MANIFESTS:
        candidate = repo_path / name
        if candidate.is_file():
            manifest_texts.append(_read_text(candidate))

    checks: list[AlignmentCheck] = []
    haystack = "\n".join(manifest_texts)
    for dep in deps:
        token = _split_dependency_token(dep)
        if not token:
            continue
        if token in haystack:
            continue
        checks.append(
            AlignmentCheck(
                kind="dependency_documented_missing_in_manifest",
                source="deterministic",
                description=(
                    f"README documents dependency `{dep}` but it is not declared "
                    f"in any project manifest"
                ),
                doc_location="README.md",
                code_location=None,
                severity="notable",
            )
        )
    return checks


# ---------------------------------------------------------------------------
# Aggregators
# ---------------------------------------------------------------------------


def run_deterministic_alignment(repo_path: Path) -> list[AlignmentCheck]:
    """Run every deterministic check against ``repo_path``.

    Reads ``README.md`` from the repo root (case-sensitive). If absent,
    returns an empty list — there is nothing to compare the code against.
    """

    repo_path = Path(repo_path)
    readme = repo_path / "README.md"
    if not readme.is_file():
        # Fall back to a case-insensitive scan of the repo root.
        if repo_path.is_dir():
            for entry in repo_path.iterdir():
                if entry.is_file() and entry.name.lower() == "readme.md":
                    readme = entry
                    break
            else:
                return []
        else:
            return []
    readme_text = _read_text(readme)
    if not readme_text:
        return []

    artefacts = extract_documented_artifacts(readme_text)
    checks: list[AlignmentCheck] = []
    checks.extend(check_cli_flags_exist(artefacts["cli_flags"], repo_path))
    checks.extend(check_env_vars_referenced(artefacts["env_vars"], repo_path))
    checks.extend(check_commands_exist(artefacts["commands"], repo_path))
    checks.extend(check_dependencies_declared(artefacts["dependencies"], repo_path))
    return checks


# ---------------------------------------------------------------------------
# Semantic alignment (LLM-driven)
# ---------------------------------------------------------------------------


# Source-code extensions we are willing to sample for the LLM. Kept small so
# the model sees representative project code, not generated/build artefacts.
_CODE_SAMPLE_EXTS: frozenset[str] = frozenset(
    {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".rb", ".java"}
)

# Common entry-point filenames, in priority order.
_ENTRY_POINT_NAMES: tuple[str, ...] = ("app.py", "main.py", "cli.py", "server.py", "index.js")

# Per-file line cap when assembling the code-excerpt payload for the LLM.
_MAX_LINES_PER_FILE = 200

# Maximum number of files to include in the sample.
_MAX_SAMPLED_FILES = 5


def _read_readme(repo_path: Path) -> str:
    """Read ``README.md`` from ``repo_path`` (case-insensitive); empty if absent."""

    readme = repo_path / "README.md"
    if not readme.is_file() and repo_path.is_dir():
        for entry in repo_path.iterdir():
            if entry.is_file() and entry.name.lower() == "readme.md":
                readme = entry
                break
    if not readme.is_file():
        return ""
    return _read_text(readme)


def _truncate_text(text: str, max_lines: int) -> str:
    """Cap ``text`` at ``max_lines`` lines, appending an ellipsis marker if cut."""

    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    truncated = lines[:max_lines]
    truncated.append(f"... [truncated; {len(lines) - max_lines} more lines]")
    return "\n".join(truncated)


def _sample_code_files(repo_path: Path, readme_text: str) -> dict[str, str]:
    """Pick up to ``_MAX_SAMPLED_FILES`` source files for LLM context.

    Priority order:

    1. Known entry points (``app.py``, ``main.py``, ``cli.py``, ...).
    2. Files referenced by name in the README.
    3. Largest remaining source files in the repo.

    Each file's contents are capped at ``_MAX_LINES_PER_FILE`` lines.
    """

    if not repo_path.is_dir():
        return {}

    code_files = _iter_repo_files(repo_path, _CODE_SAMPLE_EXTS)
    if not code_files:
        return {}

    # Index by relative path (string) and by name for quick lookups.
    by_relpath: dict[str, Path] = {}
    by_name: dict[str, list[Path]] = {}
    for f in code_files:
        rel = f.relative_to(repo_path).as_posix()
        by_relpath[rel] = f
        by_name.setdefault(f.name, []).append(f)

    picked: list[Path] = []

    # 1. Entry points by canonical name.
    for entry_name in _ENTRY_POINT_NAMES:
        for candidate in by_name.get(entry_name, []):
            if candidate not in picked:
                picked.append(candidate)
                break
        if len(picked) >= _MAX_SAMPLED_FILES:
            break

    # 2. Files referenced by name in the README.
    if readme_text and len(picked) < _MAX_SAMPLED_FILES:
        for name, candidates in by_name.items():
            if name in readme_text:
                for candidate in candidates:
                    if candidate not in picked:
                        picked.append(candidate)
                        break
                if len(picked) >= _MAX_SAMPLED_FILES:
                    break

    # 3. Largest remaining files by byte size.
    if len(picked) < _MAX_SAMPLED_FILES:
        remaining = [f for f in code_files if f not in picked]
        remaining.sort(
            key=lambda f: f.stat().st_size if f.exists() else 0,
            reverse=True,
        )
        for candidate in remaining:
            picked.append(candidate)
            if len(picked) >= _MAX_SAMPLED_FILES:
                break

    sampled: dict[str, str] = {}
    for f in picked[:_MAX_SAMPLED_FILES]:
        rel = f.relative_to(repo_path).as_posix()
        text = _read_text(f)
        if not text:
            continue
        sampled[rel] = _truncate_text(text, _MAX_LINES_PER_FILE)
    return sampled


def run_semantic_alignment(
    repo_path: Path,
    anthropic_client: Any,
) -> tuple[list[AlignmentCheck], str]:
    """Run the semantic alignment pass (spec §5.3) via Claude.

    Reads the README, picks a small representative code sample, and delegates
    to :func:`byline.llm.run_alignment_semantic` for the actual model call.
    Returns ``([], "")`` rather than raising when the LLM is unavailable or
    returns malformed output — semantic alignment is a best-effort overlay on
    top of the deterministic checks and should never fail the audit.
    """

    # Local import keeps the optional ``anthropic`` dependency out of the
    # alignment module's import graph.
    from byline.llm import (
        LLMResponseError,
        LLMUnavailableError,
        run_alignment_semantic,
    )

    repo_path = Path(repo_path)
    readme_text = _read_readme(repo_path)
    documented = extract_documented_artifacts(readme_text)
    audit_summary: dict[str, Any] = {
        "repo_path": str(repo_path),
        "documented_artifacts": documented,
    }
    sampled_code = _sample_code_files(repo_path, readme_text)

    try:
        check_dicts, summary_text = run_alignment_semantic(
            audit_summary, readme_text, sampled_code, anthropic_client
        )
    except (LLMUnavailableError, LLMResponseError) as exc:
        logger.warning("Semantic alignment skipped: %s", exc)
        return ([], "")
    except Exception as exc:  # pragma: no cover — defensive against SDK quirks
        logger.warning("Semantic alignment failed unexpectedly: %s", exc)
        return ([], "")

    checks: list[AlignmentCheck] = []
    for d in check_dicts:
        if not isinstance(d, dict):
            logger.warning("Skipping non-dict semantic-alignment entry: %r", d)
            continue
        try:
            checks.append(
                AlignmentCheck(
                    kind=d["kind"],
                    source="llm",
                    description=d["description"],
                    doc_location=d.get("doc_location"),
                    code_location=d.get("code_location"),
                    severity=d["severity"],
                )
            )
        except Exception as exc:
            logger.warning("Skipping malformed semantic-alignment entry: %s", exc)
    return checks, summary_text


def _classify_overall(checks: list[AlignmentCheck]) -> str:
    """Map per-check severities to a coarse rollup label.

    * 0 significant and at most 1 notable -> ``aligned``
    * 1 significant OR 2-4 notable        -> ``minor_gaps``
    * 2+ significant OR 5+ notable        -> ``significant_gaps``
    """

    significant = sum(1 for c in checks if c.severity == "significant")
    notable = sum(1 for c in checks if c.severity == "notable")
    if significant >= 2 or notable >= 5:
        return "significant_gaps"
    if significant == 1 or notable >= 2:
        return "minor_gaps"
    return "aligned"


def check_alignment(
    repo_path: Path,
    *,
    with_llm: bool = False,
    anthropic_client: Any = None,
) -> AlignmentFindings:
    """Top-level alignment entry point.

    Always runs the deterministic checks. When ``with_llm=True`` *and* an
    ``anthropic_client`` is supplied, also runs the semantic pass. The
    ``deterministic_only`` field on the returned :class:`AlignmentFindings`
    reflects whether the LLM path actually ran, not just whether it was
    requested.
    """

    repo_path = Path(repo_path)
    det_checks = run_deterministic_alignment(repo_path)

    sem_checks: list[AlignmentCheck] = []
    sem_summary = ""
    ran_llm = False
    if with_llm and anthropic_client is not None:
        sem_checks, sem_summary = run_semantic_alignment(repo_path, anthropic_client)
        ran_llm = True

    all_checks = det_checks + sem_checks
    return AlignmentFindings(
        checks=all_checks,
        deterministic_only=not ran_llm,
        overall_alignment=_classify_overall(all_checks),  # type: ignore[arg-type]
        llm_summary=sem_summary or None,
    )
