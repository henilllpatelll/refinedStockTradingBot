"""
Async master coordinator.

Start-up sequence
─────────────────
  3:30 AM ET  Tier-1  Finviz universe sweep  (run_universe_sweep)
  3:45 AM ET  Tier-2  Alpaca 20-day baselines (run_baseline_calc)
  9:30 AM ET  Track-A REST scout + Track-B WebSocket engine launched
  4:00 PM ET  Session guardian cancels all trading tasks; graceful shutdown

If the process starts after any scheduled time, that step is skipped when its
output file already exists and is dated today.  Starting mid-session goes
straight to the live tracks.

Wake-lock
─────────
  A daemon thread calls the Windows SetThreadExecutionState API every 55 s to
  prevent OS sleep.  pyautogui micro-jiggle is used as a cross-platform fallback.

Logging
───────
  All orchestration uses aiologger (async, non-blocking stdout).  Third-party
  libraries (LangChain, alpaca-trade-api) fall through to the stdlib root logger
  pointed at stdout — never at a file — so disk I/O never stalls the event loop.
"""

import asyncio
import logging
import sys
import threading
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from config.market_hours import SessionState, current_session
from config.rejection_tracker import rejection_tracker
from config.trade_logger import trade_logger
from config.settings import LOG_LEVEL
from strategies.tier1_universe_sweep import run_universe_sweep
from strategies.tier2_baseline_calc import run_baseline_calc
from strategies.trackA_volume_scout import run_volume_scout
from execution.trackB_realtime import run_realtime_engine
from execution.position_manager import run_trade_updates, run_eod_guardian
from agents.premarket_news import run_news_rest_scan

_ET             = ZoneInfo("America/New_York")
_UNIVERSE_PATH  = Path("config/low_float_universe.json")
_BASELINES_PATH = Path("config/active_baselines.json")


# ── scheduling helpers ────────────────────────────────────────────────────────

async def _wait_until(target: time, logger: logging.Logger) -> None:
    """Sleep until target wall-clock time ET today. No-op if already past."""
    now       = datetime.now(tz=_ET)
    target_dt = datetime.combine(now.date(), target, tzinfo=_ET)
    if target_dt <= now:
        return
    secs = (target_dt - now).total_seconds()
    logger.info(f"Main | waiting {secs/60:.1f} min until {target} ET")
    await asyncio.sleep(secs)


def _is_stale(path: Path) -> bool:
    """True if file is missing, empty, or was written before today (ET)."""
    if not path.exists() or path.stat().st_size == 0:
        return True
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=_ET)
    return mtime.date() < datetime.now(tz=_ET).date()


# ── wake-lock (daemon thread) ─────────────────────────────────────────────────

def _wake_lock_worker(stop: threading.Event) -> None:
    """
    Prevent OS sleep during market hours.
    Primary  : Windows SetThreadExecutionState (ctypes, zero dependencies).
    Fallback : pyautogui 1-pixel mouse jiggle (cross-platform).
    """
    _INTERVAL = 55   # seconds — safely under every OS idle timeout

    # ── Windows API (primary) ────────────────────────────────────────────────
    try:
        import ctypes
        _ES = 0x80000000 | 0x00000001 | 0x00000002  # ES_CONTINUOUS|SYSTEM|DISPLAY
        k32 = ctypes.windll.kernel32
        k32.SetThreadExecutionState(_ES)
        logging.getLogger("wake-lock").info("active via Windows API")
        while not stop.wait(_INTERVAL):
            k32.SetThreadExecutionState(_ES)
        k32.SetThreadExecutionState(0x80000000)      # release on exit
        return
    except Exception:
        pass

    # ── pyautogui fallback ───────────────────────────────────────────────────
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        logging.getLogger("wake-lock").info("active via pyautogui")
        while not stop.wait(_INTERVAL):
            pyautogui.moveRel(1, 0, duration=0.05)
            pyautogui.moveRel(-1, 0, duration=0.05)
    except Exception as exc:
        logging.getLogger("wake-lock").warning("unavailable (%s) — sleep prevention disabled", exc)


# ── morning pipeline ──────────────────────────────────────────────────────────

async def _morning_pipeline(logger: logging.Logger) -> None:
    # Tier-1 — 3:30 AM ET
    await _wait_until(time(3, 30), logger)
    if _is_stale(_UNIVERSE_PATH):
        logger.info("Main | Tier-1 start — Finviz universe sweep")
        await asyncio.to_thread(run_universe_sweep)
        logger.info("Main | Tier-1 complete")
    else:
        logger.info("Main | Tier-1 skipped — universe file is current")

    # Tier-2 — 3:45 AM ET
    await _wait_until(time(3, 45), logger)
    if _is_stale(_BASELINES_PATH):
        logger.info("Main | Tier-2 start — Alpaca baseline calc")
        await run_baseline_calc()
        logger.info("Main | Tier-2 complete")
    else:
        logger.info("Main | Tier-2 skipped — baselines file is current")

    # Tier-3 — pre-market news REST scan (pre-blocks bad tickers before 4 AM)
    logger.info("Main | Tier-3 start — pre-market news REST scan")
    await run_news_rest_scan()
    logger.info("Main | Tier-3 complete")

    await _wait_until(time(4, 0), logger)
    logger.info("Main | data prep complete — launching live tracks")


# ── session guardian ──────────────────────────────────────────────────────────

async def _session_guardian(tasks: list[asyncio.Task], logger: logging.Logger) -> None:
    """Cancel all trading tasks at 8:00 PM ET (Alpaca after-market close)."""
    await _wait_until(time(20, 0), logger)
    logger.info("Main | 8:00 PM ET — cancelling trading tasks")
    for t in tasks:
        t.cancel()


# ── main ──────────────────────────────────────────────────────────────────────

async def _main() -> None:
    logger = logging.getLogger("main")
    logger.info("=== Momentum Bot starting ===")

    # Wake-lock daemon thread — runs for the full process lifetime
    stop_wake   = threading.Event()
    wake_thread = threading.Thread(
        target=_wake_lock_worker, args=(stop_wake,),
        name="wake-lock", daemon=True,
    )
    wake_thread.start()

    trading_tasks: list[asyncio.Task] = []

    try:
        session = current_session()

        if session in (SessionState.CLOSED, SessionState.PRE):
            await _morning_pipeline(logger)

        logger.info("Main | launching Track-A, Track-B, trade-updates")
        track_a   = asyncio.create_task(run_volume_scout(),    name="track-A")
        track_b   = asyncio.create_task(run_realtime_engine(), name="track-B")
        trade_upd = asyncio.create_task(run_trade_updates(),   name="trade-updates")
        eod       = asyncio.create_task(run_eod_guardian(),    name="eod-guardian")
        guardian  = asyncio.create_task(
            _session_guardian([track_a, track_b, trade_upd, eod], logger),
            name="session-guardian",
        )
        trading_tasks = [track_a, track_b, trade_upd, eod, guardian]

        results    = await asyncio.gather(*trading_tasks, return_exceptions=True)
        task_names = ("track-A", "track-B", "trade-updates", "eod-guardian", "guardian")
        for name, res in zip(task_names, results):
            if isinstance(res, Exception) and not isinstance(res, asyncio.CancelledError):
                logger.error(f"Main | {name} exited with error: {res}")

        logger.info("Main | session ended — all tasks complete")

    except asyncio.CancelledError:
        pass

    finally:
        for t in trading_tasks:
            if not t.done():
                t.cancel()
        if trading_tasks:
            await asyncio.gather(*trading_tasks, return_exceptions=True)

        stop_wake.set()
        wake_thread.join(timeout=3)

        report_path = rejection_tracker.save_report()
        logger.info("Main | rejection report saved → %s", report_path)
        trades_path = trade_logger.save_report()
        logger.info("Main | trade log saved → %s", trades_path)
        logger.info("=== Momentum Bot shutdown complete ===")


if __name__ == "__main__":
    # Stdlib root logger → stdout only (never a file) so third-party libs
    # (LangChain, alpaca-trade-api) don't stall the event loop with disk I/O.
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        stream=sys.stdout,
    )
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logging.info("Main | KeyboardInterrupt — goodbye")
