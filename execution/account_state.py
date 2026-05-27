"""Read-only snapshot of live position state for diagnostics."""

from execution.position_manager import _positions


def open_positions() -> list[dict]:
    return [
        {
            "symbol":          s.symbol,
            "status":          s.status.name,
            "entry_price":     s.entry_price,
            "total_shares":    s.total_shares,
            "t1_shares":       s.t1_shares,
            "runner_shares":   s.runner_shares,
            "t1_filled":       s.t1_filled,
            "runner_exited":   s.runner_exited,
            "highest_seen":    s.highest_price_seen,
            "stop_price":      s.stop_price,
            "break_even_active": s.break_even_active,
        }
        for s in _positions.values()
    ]
