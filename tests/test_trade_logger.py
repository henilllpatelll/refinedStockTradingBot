"""Unit tests for _TradeLogger — full trade lifecycle, summary stats, thread safety."""
import threading

import pytest

from config.trade_logger import _TradeLogger


@pytest.fixture
def logger():
    return _TradeLogger()


class TestEntryLifecycle:
    def test_submitted_creates_pending(self, logger):
        logger.on_entry_submitted("AAPL", 50, 10.05, cvd=200.0, chg_pct=5.5, volume=10_000)
        assert "AAPL" in logger._pending
        r = logger._pending["AAPL"]
        assert r["shares_requested"] == 50
        assert r["entry_limit_px"] == 10.05
        assert r["cvd_at_signal"] == 200.0

    def test_filled_updates_pending(self, logger):
        logger.on_entry_submitted("AAPL", 50, 10.05, 200.0, 5.5, 10_000)
        logger.on_entry_filled("AAPL", fill_px=10.02, fill_qty=50,
                               t1_shares=25, runner_shares=25,
                               stop_price=9.62, rvol=3.5)
        r = logger._pending["AAPL"]
        assert r["entry_price"] == 10.02
        assert r["shares_filled"] == 50
        assert r["t1_shares"] == 25
        assert r["runner_shares"] == 25
        assert r["rvol_20"] == 3.5
        assert abs(r["t1_target"] - round(10.02 * 1.04, 2)) < 1e-6

    def test_t1_filled_records_price_and_time(self, logger):
        logger.on_entry_submitted("AAPL", 50, 10.05, 200.0, 5.5, 10_000)
        logger.on_entry_filled("AAPL", 10.02, 50, 25, 25, 9.62, rvol=3.5)
        logger.on_t1_filled("AAPL", fill_px=10.42)
        r = logger._pending["AAPL"]
        assert r["t1_fill_price"] == 10.42
        assert "t1_fill_time" in r

    def test_t1_filled_noop_when_not_pending(self, logger):
        logger.on_t1_filled("UNKNOWN", 10.0)   # should not raise


class TestClosedOutcome:
    def test_win_outcome(self, logger):
        logger.on_entry_submitted("AAPL", 50, 10.0, 0.0, 5.0, 10_000)
        logger.on_closed("AAPL", exit_px=10.50, reason="TRAIL_STOP", pnl=25.0)
        assert "AAPL" not in logger._pending
        assert len(logger._completed) == 1
        assert logger._completed[0]["outcome"] == "win"
        assert logger._completed[0]["pnl"] == 25.0

    def test_loss_outcome(self, logger):
        logger.on_entry_submitted("AAPL", 50, 10.0, 0.0, 5.0, 10_000)
        logger.on_closed("AAPL", exit_px=9.50, reason="MAX_LOSS", pnl=-40.0)
        assert logger._completed[0]["outcome"] == "loss"

    def test_breakeven_outcome(self, logger):
        logger.on_entry_submitted("AAPL", 50, 10.0, 0.0, 0.0, 0)
        logger.on_closed("AAPL", 10.0, "EOD", pnl=0.0)
        assert logger._completed[0]["outcome"] == "breakeven"

    def test_closed_without_prior_submitted(self, logger):
        logger.on_closed("GHOST", 9.0, "EOD", pnl=-5.0)
        assert len(logger._completed) == 1
        assert logger._completed[0]["symbol"] == "GHOST"


class TestEntryTimeout:
    def test_timeout_moves_to_completed(self, logger):
        logger.on_entry_submitted("MSFT", 30, 12.0, 0.0, 3.0, 5_000)
        logger.on_entry_timeout("MSFT")
        assert "MSFT" not in logger._pending
        assert logger._completed[0]["exit_reason"] == "entry_timeout"

    def test_timeout_noop_when_already_cleared(self, logger):
        logger.on_entry_timeout("UNKNOWN")  # should not raise
        assert len(logger._completed) == 0


class TestGetReport:
    def test_summary_counts(self, logger):
        logger.on_entry_submitted("AAPL", 50, 10.0, 0.0, 5.0, 10_000)
        logger.on_closed("AAPL", 10.5, "T1", pnl=10.0)
        logger.on_entry_submitted("MSFT", 30, 12.0, 0.0, 3.0, 5_000)
        logger.on_closed("MSFT", 11.5, "MAX_LOSS", pnl=-15.0)

        report = logger.get_report()
        s = report["summary"]
        assert s["total_trades"] == 2
        assert s["wins"]   == 1
        assert s["losses"] == 1
        assert abs(s["total_pnl"] - (-5.0)) < 1e-6

    def test_open_at_shutdown_captures_pending(self, logger):
        logger.on_entry_submitted("AAPL", 50, 10.0, 0.0, 5.0, 10_000)
        report = logger.get_report()
        assert len(report["open_at_shutdown"]) == 1
        assert report["open_at_shutdown"][0]["symbol"] == "AAPL"

    def test_empty_logger(self, logger):
        report = logger.get_report()
        assert report["summary"]["total_trades"] == 0
        assert report["summary"]["total_pnl"] == 0.0


class TestThreadSafety:
    def test_concurrent_writes(self, logger):
        errors: list[Exception] = []

        def worker(sym: str) -> None:
            try:
                logger.on_entry_submitted(sym, 10, 5.0, 0.0, 1.0, 100)
                logger.on_closed(sym, 5.1, "T1", pnl=1.0)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(f"SYM{i}",)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(logger._completed) == 20
