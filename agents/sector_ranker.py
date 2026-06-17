from __future__ import annotations

import json
import logging
from pathlib import Path

from config.settings import SECTOR_RANKINGS_PATH

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


def run_sector_ranking(returns_by_etf: dict[str, float] | None = None) -> list[dict]:
    rankings = rank_sector_returns(returns_by_etf or {})
    save_sector_rankings(rankings)
    _log.info("SectorRanker | saved %d ranking(s)", len(rankings))
    return rankings
