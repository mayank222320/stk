#!/usr/bin/env python3
"""
Verify the documentation set against the actual codebase.

Run from the repo root:      python implementationdoc/verify_docs.py
Run after ANY of these:      moving the docs · refactoring code · editing a doc

Checks performed
  1. Every relative link resolves to a real file
  2. Every file:line reference is inside the file's actual line count
  3. Claim spot-checks: specific assertions the docs make about the code
  4. Cross-doc contradictions (stale scope decisions)
  5. Work-package coverage (AGENT_GUIDE vs PROGRESS)

Exit code 0 = clean, 1 = problems found.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

DOCS_DIR = Path(__file__).parent
REPO = DOCS_DIR.parent

problems: list[str] = []
warnings: list[str] = []


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def docs() -> list[Path]:
    return sorted(DOCS_DIR.glob("*.md"))


# ── 1 + 2. links and line references ────────────────────────────────────────
def check_links() -> None:
    link = re.compile(r"\]\(([^)\s#]+)(?:#L(\d+)(?:-L(\d+))?)?\)")
    n_links = n_refs = 0
    for d in docs():
        for m in link.finditer(read(d)):
            target, l1, l2 = m.group(1), m.group(2), m.group(3)
            if target.startswith(("http", "mailto:")):
                continue
            n_links += 1
            path = (DOCS_DIR / target).resolve()
            if not path.exists():
                problems.append(f"[{d.name}] broken link -> {target}")
                continue
            if l1 and path.suffix in {".py", ".txt", ".md"}:
                n_refs += 1
                total = len(read(path).splitlines())
                hi = int(l2 or l1)
                if int(l1) > total or hi > total:
                    problems.append(
                        f"[{d.name}] {target}#L{l1}-{l2 or l1} out of range "
                        f"(file has {total} lines)"
                    )
    print(f"  links checked: {n_links}  (with line refs: {n_refs})")


# ── 3. claim spot-checks ────────────────────────────────────────────────────
#
# IMPORTANT: these assert that the BUGS STILL EXIST as the docs describe them.
# As you fix bugs, the matching claim WILL fail — that is expected and correct.
# A failing claim means: "the code changed; go update the doc that cites it."
# When a bug is genuinely fixed, delete its entry from CLAIMS and mark the
# doc section as resolved. Do NOT weaken the check to make it pass.
#
# (file, line, substring that MUST be present) — the claims the docs depend on.
CLAIMS: list[tuple[str, int, str, str]] = [
    ("features/scheduler/service.py", 463, "authoritative CMP",
     "W4: prompt asserts Gemini price authority"),
    ("features/performance/service.py", 74, "entry_low, entry_high",
     "W5: entry zone parsed"),
    ("features/market_data/technical_indicators.py", 49, "pandas_ta",
     "W6: pandas_ta imported"),
    ("features/market_data/technical_indicators.py", 156, "except Exception",
     "W6: weekly trend silently swallowed"),
    ("features/intraday/service.py", 62, "yf.Ticker",
     "Fix1: sync yfinance in async (intraday)"),
    ("features/market_data/router.py", 44, "yf.Ticker",
     "Fix1: sync yfinance in async (UI news path)"),
    ("features/portfolio/service.py", 70, "yf.Ticker",
     "Fix1: sync yfinance in async (portfolio)"),
    ("features/news_scanner/service.py", 30, "_MAX_AI_CALLS_PER_RUN",
     "Fix8: AI cap per run"),
    ("features/news_scanner/service.py", 173, "_MAX_AI_CALLS_PER_RUN",
     "Fix8: only first N analysed"),
    ("features/gemini/service.py", 98, "GeminiKeyManager(",
     "W13: import-time singleton"),
    ("features/knowledge_base/indexer.py", 83, "*.yaml",
     "W14: indexer globs yaml only"),
    ("prompt.txt", 4, "NEVER refuse",
     "W11: prompt.txt BUY bias"),
]


def check_claims() -> None:
    for rel, lineno, needle, label in CLAIMS:
        p = REPO / rel
        if not p.exists():
            problems.append(f"CLAIM FILE MISSING: {rel} ({label})")
            continue
        lines = read(p).splitlines()
        if lineno > len(lines):
            problems.append(f"CLAIM OUT OF RANGE: {rel}:{lineno} ({label})")
            continue
        # allow small drift: search a +/-3 line window
        window = "\n".join(lines[max(0, lineno - 4): lineno + 3])
        if needle not in window:
            problems.append(
                f"CLAIM FAILED: {rel}:{lineno} no longer contains '{needle}' "
                f"({label}) -- code changed, update the doc"
            )
    print(f"  claims checked: {len(CLAIMS)}")


# ── 4. cross-doc contradictions ─────────────────────────────────────────────
# Scope was reversed on 2026-09-03: intraday monitoring and the virtual
# portfolio are KEPT and converted, never deleted. Catch language that says
# otherwise, because it would make an agent delete a live feature.
FORBIDDEN_PHRASES = [
    "virtual portfolio is being dropped",
    "virtual paper portfolio **dropped**",
    "intraday scanner is dropped",
    "delete the intraday",
    "AI virtual paper portfolio (`virtual_portfolio`) | ",
    # superseded prompt design
    "00_core.md",
    "10_swing_analysis.md",
    "20_position_update.md",
    "30_chat.md",
]


def check_contradictions() -> None:
    for d in docs():
        text = read(d)
        low = text.lower()
        for phrase in FORBIDDEN_PHRASES:
            if phrase.lower() in low:
                problems.append(
                    f"[{d.name}] stale/contradictory phrase: '{phrase}' "
                    f"-- scope is KEEP-AND-CONVERT, and prompts are swing.md/general.md"
                )
    print(f"  contradiction patterns checked: {len(FORBIDDEN_PHRASES)}")


# ── 5. work-package coverage ────────────────────────────────────────────────
def check_work_packages() -> None:
    guide = DOCS_DIR / "AGENT_GUIDE.md"
    progress = DOCS_DIR / "PROGRESS.md"
    if not guide.exists() or not progress.exists():
        problems.append("AGENT_GUIDE.md or PROGRESS.md missing")
        return
    g, pr = read(guide), read(progress)
    defined = set(re.findall(r"^### (WP\d+)", g, re.M))
    tracked = set(re.findall(r"\*\*(WP\d+)\*\*", pr))
    for wp in sorted(defined - tracked, key=lambda s: int(s[2:])):
        problems.append(f"{wp} defined in AGENT_GUIDE but not tracked in PROGRESS")
    for wp in sorted(tracked - defined, key=lambda s: int(s[2:])):
        problems.append(f"{wp} tracked in PROGRESS but not defined in AGENT_GUIDE")
    print(f"  work packages: {len(defined)} defined, {len(tracked)} tracked")


# ── 6. sanity: docs exist ───────────────────────────────────────────────────
EXPECTED_DOCS = {
    "AGENT_GUIDE.md", "PROGRESS.md", "PROJECT_BRIEF.md", "WEAKNESSES.md",
    "FEATURES.md", "IMPLEMENTATION.md", "ENGINEERING.md", "ANALYTICS.md",
    "PROMPTS.md", "KNOWLEDGE_AND_PROMPTS.md", "LLM_ORCHESTRATION.md",
    "ALERTS_AND_BOT.md", "NEWS_FAST_LANE.md", "RECOMMENDATION_ENGINE.md",
    "FIXES_TOP_10.md",
}


def check_doc_set() -> None:
    present = {p.name for p in docs()}
    for missing in sorted(EXPECTED_DOCS - present):
        problems.append(f"expected document missing: {missing}")
    for extra in sorted(present - EXPECTED_DOCS):
        warnings.append(f"undocumented file in doc folder: {extra}")
    print(f"  documents present: {len(present)}")


def main() -> int:
    print("Verifying documentation against codebase\n")
    print(f"  repo:  {REPO}")
    print(f"  docs:  {DOCS_DIR}\n")
    check_doc_set()
    check_links()
    check_claims()
    check_contradictions()
    check_work_packages()

    print()
    if warnings:
        print(f"WARNINGS ({len(warnings)}):")
        for w in warnings:
            print(f"  ~ {w}")
        print()
    if problems:
        print(f"PROBLEMS ({len(problems)}):")
        for p in problems:
            print(f"  X {p}")
        print("\nFAILED -- fix the docs (or the claims) before implementing.")
        return 1
    print("ALL CHECKS PASSED -- docs are consistent with the code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
