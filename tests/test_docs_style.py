"""The user-facing docs follow the writing rules in `docs/contributing.md` ("Writing docs").

Mechanical rules only: no em or en dash, no filler word, no AI or session framing. Page length,
sentence length and bold lead-ins are judged by a reader, not by this test.

Before: nothing checked the docs; filler words ("may", "should", "above", "just") and a few
dashes could land unnoticed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"

# Pages outside the rules: developer docs and the style guide itself (it quotes the banned words).
_EXCLUDED_DIRS = ("dev",)
_EXCLUDED_FILES = ("contributing.md",)

# Pages not cleaned yet: the filler test is an expected failure for them (strict, so a page that
# gets cleaned must be removed from this set).
PENDING: set[str] = set()

FILLER = (
    "simply",
    "just",
    "easy",
    "easily",
    "please",
    "note that",
    "usually",
    "in order to",
    "may",
    "should",
    "above",
    "below",
    "the following",
    "this page",
    "it is recommended",
    "additionally",
    "importantly",
    "it is worth noting",
    "seamless",
    "robust",
    "leverage",
    "ensures",
    "serves as",
    "typically",
    "currently",
)
_FILLER_RE = re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in FILLER) + r")(?![\w-])", re.IGNORECASE)

# Case matters for "AI" (the word) and not for the phrases.
_FRAMING_RES = (
    re.compile(r"\bAI\b"),
    re.compile(r"\bLLMs?\b"),
    re.compile(
        r"Claude|Anthropic|ChatGPT|Copilot|subagent|implementer|worklog|\.crab-web-dev|\.claude"
        r"|plan \d{3}|the owner|this session",
        re.IGNORECASE,
    ),
)

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(`+)(?:(?!\1).)+?\1")
_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")


def user_pages() -> list[Path]:
    """Every user-facing page under `docs/`, computed so a new page is covered at once."""
    pages = []
    for path in sorted(DOCS.rglob("*.md")):
        rel = path.relative_to(DOCS)
        if rel.parts[0] in _EXCLUDED_DIRS or rel.name in _EXCLUDED_FILES:
            continue
        pages.append(path)
    return pages


def _rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def prose_lines(text: str) -> list[tuple[int, str]]:
    """Numbered lines outside code fences, with inline code and link targets blanked out."""
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        match = _FENCE_RE.match(line)
        if fence is None and match:
            fence = match.group(1)
            continue
        if fence is not None:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                fence = None
            continue
        line = _INLINE_CODE_RE.sub(" ", line)
        line = _LINK_TARGET_RE.sub("]", line)
        out.append((number, line))
    return out


def find_dashes(text: str) -> list[int]:
    """Lines holding an em dash or an en dash, fenced code included."""
    return [n for n, line in enumerate(text.splitlines(), start=1) if "—" in line or "–" in line]


def find_filler(text: str) -> list[tuple[int, str]]:
    """(line, word) for each filler word in the prose."""
    return [(n, m.group(1).lower()) for n, line in prose_lines(text) for m in _FILLER_RE.finditer(line)]


def find_framing(text: str) -> list[tuple[int, str]]:
    """(line, text) for each AI or session framing term anywhere in the page."""
    hits = []
    for n, line in enumerate(text.splitlines(), start=1):
        for pattern in _FRAMING_RES:
            hits.extend((n, m.group(0)) for m in pattern.finditer(line))
    return hits


PAGES = user_pages()
_IDS = [_rel(p) for p in PAGES]


def test_the_page_list_covers_the_user_docs() -> None:
    names = {p.relative_to(DOCS).as_posix() for p in PAGES}
    assert {"index.md", "tutorial.md", "using/installation.md", "reference/cli.md"} <= names
    assert not any(n.startswith("dev/") or n == "contributing.md" for n in names)


def test_pending_names_real_pages() -> None:
    assert PENDING <= {_rel(p) for p in PAGES}


@pytest.mark.parametrize("page", PAGES, ids=_IDS)
def test_no_em_or_en_dash(page: Path) -> None:
    assert find_dashes(page.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("page", PAGES, ids=_IDS)
def test_no_ai_or_session_framing(page: Path) -> None:
    assert find_framing(page.read_text(encoding="utf-8")) == []


def _filler_param(page: Path):
    marks = [pytest.mark.xfail(strict=True, reason="page not cleaned yet")] if _rel(page) in PENDING else []
    return pytest.param(page, id=_rel(page), marks=marks)


@pytest.mark.parametrize("page", [_filler_param(p) for p in PAGES])
def test_no_filler_words(page: Path) -> None:
    assert find_filler(page.read_text(encoding="utf-8")) == []


def test_the_writing_rule_checks_catch_what_they_should() -> None:
    assert find_dashes("a — b") == [1]
    assert find_dashes("a – b") == [1]
    assert find_dashes("```\nx — y\n```") == [2]
    assert find_dashes("a - b, a-b") == []

    assert find_filler("You may run it.\nNote that it is just fine.") == [(1, "may"), (2, "note that"), (2, "just")]
    assert find_filler("See the list below.") == [(1, "below")]
    assert find_filler("Run `just may` now.\n```\nshould above\n```\n[x](https://a/below/may)") == []
    assert find_filler("Use `crab run`, then check the results.") == []
    assert find_filler("It is mayonnaise and justice.") == []
    assert find_filler("````text\n```\nmay\n```\nshould\n````\nPlain.") == []

    assert find_framing("The AI wrote it.") == [(1, "AI")]
    assert find_framing("Ask Claude about plan 138 in this session.") == [
        (1, "Claude"),
        (1, "plan 138"),
        (1, "this session"),
    ]
    assert find_framing("The orchestrator runs an AIR job and a said word.") == []
