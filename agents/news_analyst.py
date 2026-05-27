"""
News Analyst — LangGraph MAS node with LLM catalyst evaluation.

Pipeline (per incoming news tick from Track-B)
───────────────────────────────────────────────
  parse → keyword_screen ──[dilution hit]──→ decide
                          ──[clean]────────→ llm_evaluate → decide

  decide: sets state["greenlight"] bool
  evaluate_news(): calls position_manager.block_ticker() when greenlight=False

The LLM node uses gpt-4o-mini to classify catalyst type and validity.
A concurrency semaphore caps simultaneous OpenAI calls to avoid rate-limit
429s during high-frequency news windows.
"""

import asyncio
import json
import logging
from typing import Any, Optional, TypedDict

_log = logging.getLogger(__name__)

# ── optional imports — graceful degradation if packages absent ────────────────

try:
    from langgraph.graph import END, StateGraph
    _LANGGRAPH_OK = True
except ImportError:
    END, StateGraph = "__end__", None
    _LANGGRAPH_OK = False

try:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage
    _LANGCHAIN_OK = True
except ImportError:
    _LANGCHAIN_OK = False

from config.settings import OPENAI_API_KEY, OPENAI_MODEL
from config.rejection_tracker import rejection_tracker
import config
import execution.position_manager as position_manager  # late import avoids cycle

# ── LLM + semaphore ───────────────────────────────────────────────────────────

_llm: Optional[Any] = None
if _LANGCHAIN_OK and OPENAI_API_KEY:
    _llm = ChatOpenAI(api_key=OPENAI_API_KEY, model=OPENAI_MODEL,
                      temperature=0, max_tokens=200)
else:
    _log.warning("NewsAnalyst | LLM disabled (OPENAI_API_KEY not set or langchain-openai missing)")

_llm_sem = asyncio.Semaphore(3)   # max 3 concurrent OpenAI calls

# ── keyword tables ────────────────────────────────────────────────────────────

_DILUTION_KEYWORDS: frozenset[str] = frozenset({
    "offering", "warrants", "shelf registration", "s-3",
    "prospectus supplement", "at-the-market", "atm offering",
    "registered direct", "convertible note", "equity line",
    "priced offering", "underwritten offering", "follow-on offering",
    "secondary offering", "private placement", "dilution", "dilutive",
})

_CATALYST_TYPES: dict[str, frozenset[str]] = {
    "fda":      frozenset({"fda", "approval", "approved", "pdufa", "nda ", "bla ",
                            "510k", "breakthrough designation", "inda"}),
    "earnings": frozenset({"earnings", "eps", "revenue", "quarterly", "guidance",
                            "beat estimates", "q1 ", "q2 ", "q3 ", "q4 "}),
    "merger":   frozenset({"acqui", "merger", "buyout", "takeover", "tender offer"}),
    "contract": frozenset({"contract award", "collaboration agreement",
                            "deal worth", "million contract", "partnership"}),
    "clinical": frozenset({"phase 1", "phase 2", "phase 3", "phase i ", "phase ii",
                            "phase iii", "clinical trial", "data readout", "interim"}),
}

_SYSTEM_PROMPT = (
    "You are a catalyst evaluator for a momentum day-trading bot targeting low-float small-cap stocks.\n\n"
    "Return ONLY a JSON object:\n"
    '{"catalyst_type":"fda|earnings|merger|contract|clinical_trial|other|none",'
    '"is_valid_catalyst":true,"reasoning":"max 20 words"}\n\n'
    "Valid catalysts cause immediate binary price moves: FDA approval/rejection, "
    "EPS beat/miss, definitive M&A agreement, named contract award with financial "
    "terms, Phase II/III data readout.\n"
    "NOT valid: analyst price targets, vague partnerships, SEC routine filings, "
    "promotional articles.\n"
    "Reply ONLY with the JSON object — no markdown."
)

# ── state schema ──────────────────────────────────────────────────────────────

class NewsState(TypedDict):
    payload:            dict
    symbols:            list[str]
    headline:           str
    summary:            str
    dilution_hits:      list[str]
    catalyst_type_hint: str
    llm_catalyst_type:  str
    llm_is_valid:       bool
    llm_reasoning:      str
    greenlight:         bool
    block_reason:       str

# ── graph nodes ───────────────────────────────────────────────────────────────

def _node_parse(state: NewsState) -> NewsState:
    p = state["payload"]
    state["symbols"]  = p.get("symbols") or []
    state["headline"] = (p.get("headline") or "").strip()
    state["summary"]  = (p.get("summary")  or "").strip()
    return state


def _node_keyword_screen(state: NewsState) -> NewsState:
    text = (state["headline"] + " " + state["summary"]).lower()
    state["dilution_hits"] = [kw for kw in _DILUTION_KEYWORDS if kw in text]
    hint = "none"
    for cat, kws in _CATALYST_TYPES.items():
        if any(kw in text for kw in kws):
            hint = cat
            break
    state["catalyst_type_hint"] = hint
    return state


async def _node_llm_evaluate(state: NewsState) -> NewsState:
    """GPT-4o-mini classifies catalyst type. Conservative defaults on failure."""
    if _llm is None:
        state["llm_catalyst_type"] = "none"
        state["llm_is_valid"]      = False
        state["llm_reasoning"]     = "LLM not configured"
        return state

    human = (
        f"Symbol(s): {', '.join(state['symbols'])}\n"
        f"Headline: {state['headline']}\n"
        f"Summary: {state['summary'][:400]}"
    )
    try:
        async with _llm_sem:
            resp = await _llm.ainvoke([
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=human),
            ])
        data = json.loads(resp.content.strip())
        state["llm_catalyst_type"] = data.get("catalyst_type", "other")
        state["llm_is_valid"]      = bool(data.get("is_valid_catalyst", False))
        state["llm_reasoning"]     = str(data.get("reasoning", ""))
    except json.JSONDecodeError:
        state["llm_catalyst_type"] = "other"
        state["llm_is_valid"]      = False
        state["llm_reasoning"]     = "JSON parse error"
    except Exception as exc:
        _log.warning("NewsAnalyst | LLM call failed: %s", exc)
        state["llm_catalyst_type"] = "other"
        state["llm_is_valid"]      = False
        state["llm_reasoning"]     = f"{type(exc).__name__}"
    return state


def _node_decide(state: NewsState) -> NewsState:
    if state["dilution_hits"]:
        state["greenlight"]   = False
        state["block_reason"] = f"dilution keywords: {state['dilution_hits']}"
    elif not state["llm_is_valid"]:
        state["greenlight"]   = False
        state["block_reason"] = (
            f"invalid catalyst ({state['llm_catalyst_type']}): {state['llm_reasoning']}"
        )
    else:
        state["greenlight"]   = True
        state["block_reason"] = ""
    return state

# ── graph compilation ─────────────────────────────────────────────────────────

def _build_graph() -> Optional[Any]:
    if not _LANGGRAPH_OK or StateGraph is None:
        return None
    g = StateGraph(NewsState)
    g.add_node("parse",          _node_parse)
    g.add_node("keyword_screen", _node_keyword_screen)
    g.add_node("llm_evaluate",   _node_llm_evaluate)
    g.add_node("decide",         _node_decide)
    g.set_entry_point("parse")
    g.add_edge("parse", "keyword_screen")
    g.add_conditional_edges(
        "keyword_screen",
        lambda s: "decide" if s["dilution_hits"] else "llm_evaluate",
        {"decide": "decide", "llm_evaluate": "llm_evaluate"},
    )
    g.add_edge("llm_evaluate", "decide")
    g.add_edge("decide", END)
    return g.compile()


_graph = _build_graph()

# ── public entry point ────────────────────────────────────────────────────────

async def evaluate_news(payload: dict) -> dict:
    """
    Evaluate one Alpaca news payload.  Called via asyncio.create_task from
    Track-B — must swallow all exceptions to avoid silent task cancellation.

    Side-effects:
      greenlight=True  → symbols added to config.greenlighted_tickers.
      greenlight=False → position_manager.block_ticker() called per symbol.
    """
    if _graph is None:
        # LangGraph unavailable — keyword-only fallback
        text = ((payload.get("headline") or "") + " " + (payload.get("summary") or "")).lower()
        hits = [kw for kw in _DILUTION_KEYWORDS if kw in text]
        greenlight = not hits
        symbols = payload.get("symbols") or []
        if greenlight:
            async with config._greenlight_lock:
                config.greenlighted_tickers.update(symbols)
        else:
            headline = (payload.get("headline") or "")[:120]
            for sym in symbols:
                await position_manager.block_ticker(sym)
                rejection_tracker.record(sym, "news", "dilution_keyword",
                                         keywords=hits, headline=headline)
        return {"greenlight": greenlight}

    _initial: NewsState = {
        "payload":            payload,
        "symbols":            [],
        "headline":           "",
        "summary":            "",
        "dilution_hits":      [],
        "catalyst_type_hint": "none",
        "llm_catalyst_type":  "none",
        "llm_is_valid":       False,
        "llm_reasoning":      "",
        "greenlight":         False,
        "block_reason":       "",
    }
    try:
        result: NewsState = await _graph.ainvoke(_initial)

        if result["greenlight"]:
            _log.info(
                "NewsAnalyst | GREENLIGHT %s  type=%s  %s",
                result["symbols"], result["llm_catalyst_type"], result["llm_reasoning"],
            )
            async with config._greenlight_lock:
                config.greenlighted_tickers.update(result["symbols"])
        else:
            _log.warning(
                "NewsAnalyst | BLOCKED %s  reason: %s",
                result["symbols"], result["block_reason"],
            )
            headline = result.get("headline", "")[:120]
            for sym in result["symbols"]:
                await position_manager.block_ticker(sym)
                rejection_tracker.record(sym, "news", result["block_reason"][:100],
                                         headline=headline)

        return dict(result)
    except Exception as exc:
        _log.error("NewsAnalyst | pipeline error: %s", exc)
        return {}
