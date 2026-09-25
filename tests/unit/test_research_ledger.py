"""Trial ledger: recorded returns load back exactly, tampering is detected, and only
counted trials of the same period and data are returned for a family."""

from __future__ import annotations

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from quanta.research.ledger import Ledger  # noqa: E402

BASE = {"hypothesis": "HA", "version": 1, "exploratory": False, "period": "p", "data_sha": "d"}


def test_returns_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    days = np.arange(18_000, 18_010, dtype=np.int64)
    ret = np.linspace(-0.02, 0.03, 10)
    assert ledger.record_trial(BASE, "t1", {"a": 1}, days, ret, {"sr_annual": 1.0})
    (entry,) = ledger.entries()
    d, r = ledger.load_returns(entry)
    assert np.array_equal(d, days) and np.allclose(r, ret, rtol=1e-9, atol=0)
    (tmp_path / entry["returns_file"]).write_bytes(b"x")
    with pytest.raises(ValueError, match="sha256"):
        ledger.load_returns(entry)


def test_family_selection(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path)
    days, ret = np.arange(3, dtype=np.int64), np.zeros(3)
    ledger.record_trial(BASE, "a", {}, days, ret, {})
    ledger.record_trial(BASE | {"exploratory": True}, "b", {}, days, ret, {})
    ledger.record_trial(BASE | {"period": "other"}, "c", {}, days, ret, {})
    ledger.record_trial(BASE | {"data_sha": "other"}, "d", {}, days, ret, {})
    ledger.record_trial(BASE | {"hypothesis": "HB"}, "e", {}, days, ret, {})
    assert [e["trial_id"] for e in ledger.trials_of(["HA"], "p", "d")] == ["a"]
    assert [e["trial_id"] for e in ledger.trials_of(["HA", "HB"], "p", "d")] == ["a", "e"]
