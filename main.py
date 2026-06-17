from __future__ import annotations

import asyncio
import logging
import sys
import threading
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from agents.earnings_calendar import run_earnings_calendar_fetch
from agents.premarket_news import run_news_rest_scan
from agents.sector_ranker import run_sector_ranking
from agents.sector_ranker import run_sector_map_build
from config.rejection_tracker import rejection_tracker
from config.settings import LOG_LEVEL
from execution.position_manager import (
    audit_untracked_alpaca_positions,
    eod_close_check,
    fetch_daily_exit_data_for_open_positions,
    load_position_state,
    run_trade_updates,
)
from execution.strategy_analytics import send_strategy_daily_summary
from execution.swing_entry import run_swing_entry
from strategies.eod_scanner import run_eod_scan
from strategies.premarket_filter import run_premarket_filter
from strategies.tier1_universe_sweep import run_universe_sweep
from strategies.tier2_baseline_calc import run_baseline_calc
from strategies.watchlist_pruner import run_watchlist_pruner

_ET = ZoneInfo("America/New_York")
ENTRY_CUTOFF = time(9, 45)


async def _wait_until(target: time, logger: logging.Logger) -> None:
    now = datetime.now(tz=_ET)
    target_dt = datetime.combine(now.date(), target, tzinfo=_ET)
    if target_dt <= now:
        return
    seconds = (target_dt - now).total_seconds()
    logger.info("Main | waiting %.1f min until %s ET", seconds / 60, target)
    await asyncio.sleep(seconds)


def _stage_status(target: time, now: datetime, cutoff: time | None = None) -> str:
    target_dt = datetime.combine(now.date(), target, tzinfo=now.tzinfo)
    if now < target_dt:
        return "pending"
    if cutoff is not None:
        cutoff_dt = datetime.combine(now.date(), cutoff, tzinfo=now.tzinfo)
        if now > cutoff_dt:
            return "missed"
    return "due"


async def _wait_for_stage(target: time, logger: logging.Logger, cutoff: time | None = None) -> bool:
    now = datetime.now(tz=_ET)
    status = _stage_status(target, now, cutoff)
    if status == "missed":
        logger.warning("Main | missed %s ET stage; skipping", target)
        return False
    if status == "pending":
        await _wait_until(target, logger)
    return True


def _is_stale(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return True
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=_ET)
    return mtime.date() < datetime.now(tz=_ET).date()


def _wake_lock_worker(stop: threading.Event) -> None:
    interval = 55
    try:
        import ctypes

        state = 0x80000000 | 0x00000001 | 0x00000002
        kernel = ctypes.windll.kernel32
        kernel.SetThreadExecutionState(state)
        while not stop.wait(interval):
            kernel.SetThreadExecutionState(state)
        kernel.SetThreadExecutionState(0x80000000)
    except Exception:
        logging.getLogger("wake-lock").warning("sleep prevention unavailable")


async def _weekend_prep(logger: logging.Logger) -> None:
    await _wait_until(time(8, 0), logger)
    logger.info("Main | Saturday prep start")
    await asyncio.to_thread(run_universe_sweep)
    await asyncio.to_thread(run_earnings_calendar_fetch)
    await asyncio.to_thread(run_sector_ranking)
    await asyncio.to_thread(run_sector_map_build)
    logger.info("Main | Saturday prep complete")


async def _sunday_prune(logger: logging.Logger) -> None:
    await _wait_until(time(18, 0), logger)
    await asyncio.to_thread(run_watchlist_pruner)
    logger.info("Main | Sunday watchlist prune complete")


async def _weekday_pipeline(logger: logging.Logger) -> None:
    trade_updates = asyncio.create_task(run_trade_updates(), name="trade-updates")
    try:
        if await _wait_for_stage(time(7, 0), logger):
            await run_news_rest_scan()
            await run_premarket_filter()

        if await _wait_for_stage(time(9, 30), logger, cutoff=ENTRY_CUTOFF):
            await run_swing_entry()

        if await _wait_for_stage(time(15, 45), logger):
            await run_baseline_calc()
            await run_eod_scan()

        if await _wait_for_stage(time(15, 50), logger):
            await eod_close_check(await fetch_daily_exit_data_for_open_positions())

        if await _wait_for_stage(time(16, 5), logger):
            await send_strategy_daily_summary()
    finally:
        trade_updates.cancel()
        await asyncio.gather(trade_updates, return_exceptions=True)


async def _run_daily_cycle(now: datetime, logger: logging.Logger) -> None:
    load_position_state()
    audit_untracked_alpaca_positions()
    if now.weekday() == 5:
        await _weekend_prep(logger)
    elif now.weekday() == 6:
        await _sunday_prune(logger)
    else:
        await _weekday_pipeline(logger)


def _seconds_until_next_day(now: datetime) -> float:
    tomorrow = (now + timedelta(days=1)).date()
    next_run = datetime.combine(tomorrow, time(0, 5), tzinfo=now.tzinfo)
    return max(1.0, (next_run - now).total_seconds())


async def _main() -> None:
    logger = logging.getLogger("main")
    logger.info("=== Swing Trading Bot starting ===")

    stop_wake = threading.Event()
    wake_thread = threading.Thread(target=_wake_lock_worker, args=(stop_wake,), name="wake-lock", daemon=True)
    wake_thread.start()

    try:
        while True:
            now = datetime.now(tz=_ET)
            await _run_daily_cycle(now, logger)
            report_path = rejection_tracker.save_report()
            logger.info("Main | rejection report saved -> %s", report_path)
            sleep_seconds = _seconds_until_next_day(datetime.now(tz=_ET))
            logger.info("Main | daily cycle complete; sleeping %.1f hours", sleep_seconds / 3600)
            await asyncio.sleep(sleep_seconds)
    finally:
        stop_wake.set()
        wake_thread.join(timeout=3)
        report_path = rejection_tracker.save_report()
        logger.info("Main | rejection report saved -> %s", report_path)
        logger.info("=== Swing Trading Bot shutdown complete ===")


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        stream=sys.stdout,
    )
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logging.info("Main | KeyboardInterrupt")
