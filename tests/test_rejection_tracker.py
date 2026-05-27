"""Unit tests for _RejectionTracker — deduplication, RVOL updates, summary, thread safety."""
import threading

import pytest

from config.rejection_tracker import _RejectionTracker


@pytest.fixture
def tracker():
    return _RejectionTracker()


class TestRecordDeduplication:
    def test_first_record_stored(self, tracker):
        tracker.record("AAPL", "track_a", "rvol_below_threshold")
        assert len(tracker._data["AAPL"]) == 1

    def test_duplicate_key_silently_ignored(self, tracker):
        tracker.record("AAPL", "track_a", "rvol_below_threshold")
        tracker.record("AAPL", "track_a", "rvol_below_threshold")
        assert len(tracker._data["AAPL"]) == 1

    def test_different_stage_same_symbol_allowed(self, tracker):
        tracker.record("AAPL", "track_a", "rvol_below_threshold")
        tracker.record("AAPL", "track_b", "cvd_nonpositive")
        assert len(tracker._data["AAPL"]) == 2

    def test_same_stage_different_symbol_allowed(self, tracker):
        tracker.record("AAPL", "track_a", "rvol_below_threshold")
        tracker.record("MSFT", "track_a", "rvol_below_threshold")
        assert len(tracker._data["AAPL"]) == 1
        assert len(tracker._data["MSFT"]) == 1

    def test_extra_kwargs_stored(self, tracker):
        tracker.record("AAPL", "track_b", "gap_below_threshold", gap_pct=3.2)
        entry = tracker._data["AAPL"][0]
        assert entry["gap_pct"] == 3.2

    def test_none_kwargs_omitted(self, tracker):
        tracker.record("AAPL", "track_a", "rvol_below_threshold", extra=None)
        assert "extra" not in tracker._data["AAPL"][0]


class TestRecordRvol:
    def test_first_rvol_creates_entry(self, tracker):
        tracker.record_rvol("AAPL", 1.5, threshold=2.0)
        assert tracker._best_rvol["AAPL"] == 1.5

    def test_higher_rvol_updates_best(self, tracker):
        tracker.record_rvol("AAPL", 1.5, 2.0)
        tracker.record_rvol("AAPL", 2.0, 2.0)
        assert tracker._best_rvol["AAPL"] == 2.0

    def test_lower_rvol_does_not_update(self, tracker):
        tracker.record_rvol("AAPL", 2.5, 2.0)
        tracker.record_rvol("AAPL", 1.0, 2.0)
        assert tracker._best_rvol["AAPL"] == 2.5

    def test_in_place_update_no_duplicate_entry(self, tracker):
        tracker.record_rvol("AAPL", 1.5, 2.0)
        tracker.record_rvol("AAPL", 2.5, 2.0)
        rvol_entries = [e for e in tracker._data["AAPL"] if e["stage"] == "track_a"]
        assert len(rvol_entries) == 1
        assert rvol_entries[0]["best_rvol"] == pytest.approx(2.5, rel=1e-3)


class TestWatchlistAndTraded:
    def test_record_watchlist_entry_stored(self, tracker):
        tracker.record_watchlist_entry("AAPL")
        assert "AAPL" in tracker._reached_watchlist

    def test_record_watchlist_idempotent(self, tracker):
        tracker.record_watchlist_entry("AAPL")
        tracker.record_watchlist_entry("AAPL")
        assert len(tracker._reached_watchlist) == 1

    def test_record_traded_stored(self, tracker):
        tracker.record_traded("AAPL")
        assert "AAPL" in tracker._traded


class TestGetReportSummary:
    def test_rvol_too_low_count(self, tracker):
        tracker.record_rvol("AAPL", 0.8, 2.0)
        tracker.record_rvol("MSFT", 1.2, 2.0)
        assert tracker.get_report()["summary"]["rvol_too_low"] == 2

    def test_traded_count(self, tracker):
        tracker.record_traded("AAPL")
        tracker.record_traded("MSFT")
        assert tracker.get_report()["summary"]["traded"] == 2

    def test_news_blocked_count(self, tracker):
        tracker.record("AAPL", "news_premarket", "dilution_keyword")
        tracker.record("MSFT", "news_live", "news_block")
        assert tracker.get_report()["summary"]["news_blocked"] == 2

    def test_gap_too_low_count(self, tracker):
        tracker.record("AAPL", "track_b", "gap_below_threshold")
        assert tracker.get_report()["summary"]["gap_too_low"] == 1

    def test_empty_tracker(self, tracker):
        report = tracker.get_report()
        for val in report["summary"].values():
            assert val == 0

    def test_report_includes_traded_list(self, tracker):
        tracker.record_traded("AAPL")
        tracker.record_traded("MSFT")
        report = tracker.get_report()
        assert set(report["traded"]) == {"AAPL", "MSFT"}


class TestThreadSafety:
    def test_concurrent_record_and_rvol(self, tracker):
        errors: list[Exception] = []

        def worker(i: int) -> None:
            sym = f"SYM{i}"
            try:
                tracker.record(sym, "track_a", "rvol_below_threshold")
                tracker.record_rvol(sym, float(i) * 0.1, 2.0)
                tracker.record_traded(sym)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert tracker.get_report()["summary"]["traded"] == 30
