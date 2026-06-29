import threading

import pytest

from config.rejection_tracker import _RejectionTracker


@pytest.fixture
def tracker():
    return _RejectionTracker()


def test_record_deduplicates_by_symbol_and_stage(tracker):
    tracker.record("ABCD", "eod_scan", "no_strategy_signal")
    tracker.record("ABCD", "eod_scan", "still_no_strategy_signal")

    assert len(tracker._data["ABCD"]) == 1


def test_record_allows_different_swing_stages(tracker):
    tracker.record("ABCD", "eod_scan", "no_strategy_signal")
    tracker.record("ABCD", "premarket", "gap_down")

    assert len(tracker._data["ABCD"]) == 2


def test_report_counts_pipeline_stages(tracker):
    tracker.record("ABCD", "universe", "price_below_min")
    tracker.record("WXYZ", "premarket", "gap_down")
    tracker.record("BLCK", "news", "block_secondary_offering")
    tracker.record_watchlist_entry("ABCD")
    tracker.record_confirmed("ABCD")
    tracker.record_traded("ABCD")

    summary = tracker.get_report()["summary"]

    assert summary["universe_rejected"] == 1
    assert summary["premarket_rejected"] == 1
    assert summary["news_blocked"] == 1
    assert summary["watchlisted"] == 1
    assert summary["confirmed"] == 1
    assert summary["traded"] == 1


def test_extra_none_details_are_omitted(tracker):
    tracker.record("ABCD", "entry", "missing_price", price=None, strategy_id="ISR")

    entry = tracker._data["ABCD"][0]
    assert entry["strategy_id"] == "ISR"
    assert "price" not in entry


def test_concurrent_recording_is_thread_safe(tracker):
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            symbol = f"SYM{i}"
            tracker.record(symbol, "eod_scan", "no_signal")
            tracker.record_watchlist_entry(symbol)
            tracker.record_traded(symbol)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(25)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert tracker.get_report()["summary"]["traded"] == 25
