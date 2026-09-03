import yaml
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).parent / "templates"

def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter from a markdown file if present."""
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return content.strip()

def _get_trade_context_block() -> str:
    return """
================================================================================
CURRENT TRADE CONTEXT:
================================================================================
Security       : {symbol} (NSE/BSE)
Hold Duration  : {hold}
Entry Price    : ₹{entry}   |  Current Price : ₹{current_price}
Day High       : ₹{day_high} |  Day Low       : ₹{day_low}  |  VWAP: ₹{vwap}
Target 1       : ₹{t1}       |  Target 2      : ₹{t2}       |  Target 3: ₹{t3}
Stop-Loss      : ₹{sl}
User Notes     : {notes}
"""

def get_v1_prompt() -> str:
    """Load Shared Core + V1 Compact."""
    try:
        core_raw = (_TEMPLATES_DIR / "qmaf_shared_core.md").read_text(encoding="utf-8")
        v1_raw = (_TEMPLATES_DIR / "qmaf_v1_compact.md").read_text(encoding="utf-8")
    except Exception as e:
        return f"[Error loading V1 templates: {e}]"

    core = _strip_frontmatter(core_raw)
    v1 = _strip_frontmatter(v1_raw)
    
    return f"{core}\n{_get_trade_context_block()}\n{v1}"

def get_v2_prompt() -> str:
    """Load Shared Core + V2 Personalized Institutional."""
    try:
        core_raw = (_TEMPLATES_DIR / "qmaf_shared_core.md").read_text(encoding="utf-8")
        v2_raw = (_TEMPLATES_DIR / "qmaf_v2_personalized.md").read_text(encoding="utf-8")
    except Exception as e:
        return f"[Error loading V2 templates: {e}]"

    core = _strip_frontmatter(core_raw)
    v2 = _strip_frontmatter(v2_raw)

    return (
        core
        + "\n\n"
        + v2
        + _get_trade_context_block()
        + """
================================================================================
YOUR TASK — V2 PERSONALIZED INSTITUTIONAL TRADE UPDATE:
================================================================================
Apply ALL 28 sections of the QMAF framework above AND consider personal portfolio
context. Use REAL-TIME search (news + filings + live prices). Cover:

1. 📊 Current Market Snapshot (CMP, daily change, volume, data classification)
2. 📰 News & Filings Audit (NSE/BSE filings, ET/MC/BS news, bulk/block deals)
3. 🔍 Technical Analysis (trend, VWAP, RSI, MACD, Wyckoff/VSA, key levels)
4. 🏛 Institutional & Derivatives (FII/DII flows, OI, PCR, IV rank if applicable)
5. 🧠 QMAF Entry Gate Evaluation (valuation, structural, liquidity, flow gates)
6. ⚖ Conflicting Signals — resolve explicitly, do not blindly average
7. 🎯 Actionable Verdict (HOLD / TRAIL SL / PARTIAL EXIT / EXIT) with exact levels
8. 🔴 Invalidation: price-based + event-based conditions
9. 📊 Probabilistic Scenarios: Bullish X% / Base Y% / Bearish Z% (= 100%)
10. 💼 Personal SIP Note (if GOLDBEES or MON100: dip-buying timing assessment)
11. ⚠ Risk Considerations (liquidity, gap risk, leverage, concentration)
12. SEBI Compliance Disclaimer

OUTPUT FORMATTING RULES: same as shared core — short bullets, plain text, no markdown.
"""
    )

# Expose V1 as a string property just like before so imports in service.py still work
class _PromptModule:
    @property
    def PROMPT_V1_COMPACT(self):
        return get_v1_prompt()

import sys
sys.modules[__name__] = _PromptModule()
# Restore functions to module dict
sys.modules[__name__].get_v2_prompt = get_v2_prompt
sys.modules[__name__].get_v1_prompt = get_v1_prompt
