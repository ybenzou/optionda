from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from optionda.asof import apply_asof, parse_asof_date, session_close_at, split_asof_prefix
from optionda.batch import add_batch
from optionda.journal import log_path
from optionda.models import Position
from optionda.store import AccountStore
from optionda.undo import last_operation_times
from optionda.analytics import read_events


_ET = ZoneInfo("America/New_York")


def test_parse_asof_iso_and_slash() -> None:
    today = date(2026, 8, 24)
    assert parse_asof_date("2026-08-20", today=today) == date(2026, 8, 20)
    assert parse_asof_date("8/20", today=today) == date(2026, 8, 20)
    assert parse_asof_date("8/21/", today=today) is None
    assert parse_asof_date("8/21", today=today) == date(2026, 8, 21)


def test_future_slash_date_rolls_to_previous_year() -> None:
    today = date(2026, 1, 5)
    assert parse_asof_date("12/20", today=today) == date(2025, 12, 20)


def test_split_asof_prefix_and_date_only() -> None:
    today = date(2026, 8, 24)
    day, rest = split_asof_prefix("8/20 SPCX 261218 205 C x1 @ 4.65", today=today)
    assert day == date(2026, 8, 20)
    assert rest == "SPCX 261218 205 C x1 @ 4.65"
    day, rest = split_asof_prefix("8/21:", today=today)
    assert day == date(2026, 8, 21)
    assert rest == ""
    day, rest = split_asof_prefix("sell HOOD 261218 150 C x2 @ 5.7", today=today)
    assert day is None
    assert rest.startswith("sell ")


def test_apply_asof_splits_semicolons_and_sticky_dates() -> None:
    today = date(2026, 8, 24)
    dated = apply_asof(
        [
            "8/20 SPCX 261218 205 C x1 @ 4.65; "
            "RDDT 261218 200 C x1 @ 7.7; "
            "8/21 sell HOOD 261218 150 C x2 @ 5.7; "
            "CRWV 261218 130 C x1 @ 6.3"
        ],
        today=today,
    )
    assert [item[0] for item in dated] == [
        "SPCX 261218 205 C x1 @ 4.65",
        "RDDT 261218 200 C x1 @ 7.7",
        "sell HOOD 261218 150 C x2 @ 5.7",
        "CRWV 261218 130 C x1 @ 6.3",
    ]
    assert dated[0][1] == session_close_at(date(2026, 8, 20))
    assert dated[1][1] == session_close_at(date(2026, 8, 20))
    assert dated[2][1] == session_close_at(date(2026, 8, 21))
    assert dated[3][1] == session_close_at(date(2026, 8, 21))


def test_apply_asof_sticky_across_segments() -> None:
    today = date(2026, 8, 24)
    dated = apply_asof(
        [
            "8/20 SPCX 261218 205 C x1 @ 4.65",
            "RDDT 261218 200 C x1 @ 7.7",
            "8/21",
            "sell HOOD 261218 150 C x2 @ 5.7",
            "CRWV 261218 130 C x1 @ 6.3",
        ],
        today=today,
    )
    assert [item[0] for item in dated] == [
        "SPCX 261218 205 C x1 @ 4.65",
        "RDDT 261218 200 C x1 @ 7.7",
        "sell HOOD 261218 150 C x2 @ 5.7",
        "CRWV 261218 130 C x1 @ 6.3",
    ]
    assert dated[0][1] == session_close_at(date(2026, 8, 20))
    assert dated[1][1] == session_close_at(date(2026, 8, 20))
    assert dated[2][1] == session_close_at(date(2026, 8, 21))
    assert dated[3][1] == session_close_at(date(2026, 8, 21))


def test_add_batch_writes_asof_journal_ts(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    store.add_position(
        None,
        Position(
            occ_symbol="HOOD261218C00150000",
            underlying="HOOD",
            expiry=date(2026, 12, 18),
            strike=150,
            option_type="call",
            qty=4,
            iv_frozen=0.6,
            iv_as_of=datetime(2026, 8, 1, 20, tzinfo=_ET),
            entry_premium=3.0,
        ),
    )
    add_batch(
        store,
        [
            "8/20 SPCX 261218 205 C x1 @ 4.65",
            "8/21 sell HOOD 261218 150 C x2 @ 5.7",
        ],
        iv=0.6,
        home=tmp_path,
    )
    events = read_events(log_path("demo", tmp_path))
    merge = next(item for item in events if item.get("occ") == "SPCX261218C00205000")
    sell = next(item for item in events if item.get("event") == "sell")
    assert merge["ts"].startswith("2026-08-20T20:00:00")
    assert sell["ts"].startswith("2026-08-21T20:00:00")
    times = last_operation_times(events)
    spcx = next(
        pos for pos in store.require_current().positions if pos.underlying == "SPCX"
    )
    assert times[spcx.id].astimezone(_ET).date() == date(2026, 8, 20)
    hood = next(
        pos for pos in store.require_current().positions if pos.underlying == "HOOD"
    )
    assert times[hood.id].astimezone(_ET).date() == date(2026, 8, 21)


def test_add_batch_default_asof_for_undated_line(tmp_path, monkeypatch) -> None:
    store = AccountStore(tmp_path)
    store.create("demo")
    monkeypatch.setenv("OPTIONDA_ACTIVE", "demo")
    add_batch(
        store,
        ["SPCX 261218 205 C x1 @ 4.65"],
        iv=0.6,
        home=tmp_path,
        asof=date(2026, 8, 20),
    )
    events = read_events(log_path("demo", tmp_path))
    add = next(item for item in events if item.get("occ") == "SPCX261218C00205000")
    assert add["ts"].startswith("2026-08-20T20:00:00")
