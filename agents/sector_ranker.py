from __future__ import annotations

import json
import logging
from pathlib import Path

from config.settings import SECTOR_MAP_PATH, SECTOR_RANKINGS_PATH, SWING_UNIVERSE_PATH
from utils.finnhub import build_sector_map
from utils.market_data import fetch_weekly_returns_sync

_log = logging.getLogger(__name__)

SECTOR_ETFS = ("XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLB", "XLU", "XLRE", "XLC")


def rank_sector_returns(returns_by_etf: dict[str, float]) -> list[dict]:
    ranked = sorted(
        (
            {"etf": etf, "weekly_return": float(returns_by_etf.get(etf, 0.0))}
            for etf in SECTOR_ETFS
        ),
        key=lambda item: item["weekly_return"],
        reverse=True,
    )
    return [{**item, "rank": index} for index, item in enumerate(ranked, start=1)]


def save_sector_rankings(rankings: list[dict], path: str | Path = SECTOR_RANKINGS_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(rankings, indent=2))
    return target


def load_universe(path: str | Path = SWING_UNIVERSE_PATH) -> list[str]:
    source = Path(path)
    if not source.exists() or source.stat().st_size == 0:
        return []
    return json.loads(source.read_text())


def save_sector_map(mapping: dict[str, str], path: str | Path = SECTOR_MAP_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(mapping, indent=2))
    return target


def run_sector_map_build(path: str | Path = SECTOR_MAP_PATH) -> dict[str, str]:
    symbols = load_universe()
    if not symbols:
        _log.warning("SectorRanker | universe empty; skipping Finnhub sector map")
        mapping: dict[str, str] = {}
    else:
        try:
            mapping = build_sector_map(symbols)
        except RuntimeError as exc:
            _log.warning("SectorRanker | Finnhub sector map skipped: %s", exc)
            mapping = {}
        except Exception as exc:
            _log.error("SectorRanker | Finnhub sector map failed: %s", exc)
            mapping = {}
    save_sector_map(mapping, path)
    _log.info("SectorRanker | saved %d sector mapping(s)", len(mapping))
    return mapping


def run_sector_ranking(returns_by_etf: dict[str, float] | None = None) -> list[dict]:
    returns = returns_by_etf if returns_by_etf is not None else fetch_weekly_returns_sync(list(SECTOR_ETFS))
    rankings = rank_sector_returns(returns)
    save_sector_rankings(rankings)
    _log.info("SectorRanker | saved %d ranking(s)", len(rankings))
    return rankings
