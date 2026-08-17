from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from optionda.engine import sync_completed_session
from optionda.market.session import (
    DailyClose,
    MarketClock,
    MarketSession,
    SessionReference,
    load_session_reference,
    save_session_reference,
)
from optionda.models import Account, Position, SpotQuote
from optionda.pricing.surface import (
    ExpirySmile,
    IvSurface,
    SurfaceNode,
    load_surface,
    save_surface,
)

_ET = ZoneInfo("America/New_York")
FRIDAY = date(2026, 8, 14)
THURSDAY = date(2026, 8, 13)


def _session(day: date, hour: int = 16) -> MarketSession:
    return MarketSession(
        session_date=day,
        open_at=datetime(day.year, day.month, day.day, 9, 30, tzinfo=_ET),
        close_at=datetime(day.year, day.month, day.day, hour, 0, tzinfo=_ET),
    )


def _clock(ts: datetime) -> MarketClock:
    return MarketClock(
        timestamp=ts,
        is_open=False,
        next_open=None,
        next_close=None,
    )


def _position(underlying: str = "AAPL") -> Position:
    return Position(
        occ_symbol=f"{underlying}261120C00350000",
        underlying=underlying,
        expiry=date(2026, 11, 20),
        strike=350.0,
        option_type="call",
        iv_frozen=0.28,
        iv_as_of=datetime(2026, 8, 13, 20, 0, tzinfo=timezone.utc),
        entry_premium=5.0,
    )


def _surface(underlying: str, day: date) -> IvSurface:
    close = datetime(day.year, day.month, day.day, 16, 0, tzinfo=_ET)
    return IvSurface(
        underlying=underlying,
        spot=200.0,
        as_of=close,
        source="alpaca/chain",
        smiles=[
            ExpirySmile(
                expiry=date(2026, 11, 20),
                nodes=[SurfaceNode(strike=350.0, delta=0.25, iv=0.28)],
            )
        ],
        quality={"accepted": 1, "rejected": 0},
        session_date=day,
        session_close_at=close,
        legacy=False,
    )


def _reference(underlying: str, day: date, close: float = 210.0) -> SessionReference:
    return SessionReference(
        underlying=underlying,
        session_date=day,
        session_close_at=datetime(day.year, day.month, day.day, 16, 0, tzinfo=_ET),
        close_spot=close,
        source="alpaca/sip/1Day",
        updated_at=datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc),
    )


class FakeRouter:
    feed_name = "alpaca"

    def __init__(
        self,
        *,
        clock: MarketClock,
        sessions: list[MarketSession],
        closes: dict[str, DailyClose] | None = None,
        snapshots: dict[str, dict] | None = None,
        fail_chain: set[str] | None = None,
    ) -> None:
        self.clock = clock
        self.sessions = sessions
        self.closes = closes or {}
        self.snapshots = snapshots or {}
        self.fail_chain = fail_chain or set()
        self.daily_calls: list[tuple[tuple[str, ...], date]] = []
        self.chain_calls: list[str] = []
        self.spot_at_calls: list[str] = []

    def get_market_clock(self) -> MarketClock:
        return self.clock

    def get_market_calendar(self, start, end):
        return self.sessions

    def get_daily_closes(self, symbols, session_date):
        self.daily_calls.append((tuple(symbols), session_date))
        return {
            symbol: self.closes[symbol]
            for symbol in symbols
            if symbol in self.closes
        }

    def get_option_chain_snapshots(self, underlying):
        self.chain_calls.append(underlying)
        if underlying in self.fail_chain:
            raise RuntimeError(f"{underlying} chain failed")
        return self.snapshots.get(underlying, {})

    def get_spot_at(self, symbol, as_of):
        self.spot_at_calls.append(symbol)
        return SpotQuote(symbol=symbol, price=200.0, as_of=as_of, source="hist")

    def get_spots(self, symbols):
        return {
            symbol: SpotQuote(symbol=symbol, price=200.0, source="live")
            for symbol in symbols
        }


def _account(*names: str) -> Account:
    return Account(name="demo", positions=[_position(name) for name in names])


def test_same_session_makes_zero_network_calls(tmp_path) -> None:
    save_session_reference(_reference("AAPL", FRIDAY), tmp_path)
    save_surface(_surface("AAPL", FRIDAY), tmp_path)
    router = FakeRouter(
        clock=_clock(datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)),
        sessions=[_session(THURSDAY), _session(FRIDAY)],
    )
    events: list[tuple[str, int, int]] = []
    result = sync_completed_session(
        _account("AAPL"),
        home=tmp_path,
        router=router,
        on_progress=lambda label, done, total: events.append((label, done, total)),
    )
    assert result.completed_session is not None
    assert result.completed_session.session_date == FRIDAY
    assert router.daily_calls == []
    assert router.chain_calls == []
    assert result.surfaces_saved == {}
    fetch = [item for item in events if item[0].startswith("1/2 fetch")]
    chain = [item for item in events if item[0].startswith("2/2 chain")]
    assert fetch
    assert all(total == 2 for _, _, total in fetch)
    assert chain[-1] == ("2/2 chain  ready", 1, 1)


def test_new_session_fetches_close_and_chain_once(tmp_path) -> None:
    save_session_reference(_reference("AAPL", THURSDAY), tmp_path)
    save_surface(_surface("AAPL", THURSDAY), tmp_path)
    quote = datetime(2026, 8, 14, 15, 59, 59, tzinfo=_ET)
    router = FakeRouter(
        clock=_clock(datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)),
        sessions=[_session(THURSDAY), _session(FRIDAY)],
        closes={
            "AAPL": DailyClose(
                symbol="AAPL",
                session_date=FRIDAY,
                close=230.0,
                source="alpaca/sip/1Day",
            )
        },
        snapshots={
            "AAPL": {
                "AAPL261120C00350000": {
                    "impliedVolatility": 0.28,
                    "latestQuote": {"bp": 4.9, "ap": 5.1, "t": quote.isoformat()},
                }
            }
        },
    )
    result = sync_completed_session(
        _account("AAPL"),
        home=tmp_path,
        router=router,
    )
    assert router.daily_calls == [(("AAPL",), FRIDAY)]
    assert router.chain_calls == ["AAPL"]
    assert router.spot_at_calls == ["AAPL"]
    assert "AAPL" in result.references_saved
    assert "AAPL" in result.surfaces_saved
    loaded = load_surface("AAPL", tmp_path)
    assert loaded is not None
    assert loaded.session_date == FRIDAY
    ref = load_session_reference("AAPL", tmp_path)
    assert ref is not None
    assert ref.close_spot == 230.0


def test_daily_bar_success_keeps_old_surface_when_chain_fails(tmp_path) -> None:
    save_surface(_surface("AAPL", THURSDAY), tmp_path)
    router = FakeRouter(
        clock=_clock(datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)),
        sessions=[_session(THURSDAY), _session(FRIDAY)],
        closes={
            "AAPL": DailyClose(
                symbol="AAPL",
                session_date=FRIDAY,
                close=230.0,
                source="alpaca/sip/1Day",
            )
        },
        fail_chain={"AAPL"},
    )
    result = sync_completed_session(
        _account("AAPL"),
        home=tmp_path,
        router=router,
    )
    assert "AAPL" in result.references_saved
    assert "AAPL" in result.pending_surfaces
    old = load_surface("AAPL", tmp_path)
    assert old is not None
    assert old.session_date == THURSDAY


def test_one_ticker_failure_does_not_block_others(tmp_path) -> None:
    quote = datetime(2026, 8, 14, 15, 59, 59, tzinfo=_ET)
    router = FakeRouter(
        clock=_clock(datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)),
        sessions=[_session(FRIDAY)],
        closes={
            "AAPL": DailyClose("AAPL", FRIDAY, 230.0, "alpaca/sip/1Day"),
            "GOOG": DailyClose("GOOG", FRIDAY, 180.0, "alpaca/sip/1Day"),
        },
        snapshots={
            "GOOG": {
                "GOOG261120C00350000": {
                    "impliedVolatility": 0.30,
                    "latestQuote": {"bp": 8.9, "ap": 9.1, "t": quote.isoformat()},
                }
            }
        },
        fail_chain={"AAPL"},
    )
    result = sync_completed_session(
        _account("AAPL", "GOOG"),
        home=tmp_path,
        router=router,
    )
    assert "GOOG" in result.surfaces_saved
    assert "AAPL" in result.pending_surfaces


def test_retry_window_skips_repeat_chain(tmp_path) -> None:
    router = FakeRouter(
        clock=_clock(datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)),
        sessions=[_session(FRIDAY)],
        closes={"AAPL": DailyClose("AAPL", FRIDAY, 230.0, "alpaca/sip/1Day")},
        fail_chain={"AAPL"},
    )
    first = sync_completed_session(_account("AAPL"), home=tmp_path, router=router)
    assert router.chain_calls == ["AAPL"]
    assert first.next_retry_at is not None
    second = sync_completed_session(_account("AAPL"), home=tmp_path, router=router)
    assert router.chain_calls == ["AAPL"]
    assert "AAPL" in second.pending_surfaces


def test_retry_window_arrival_retries_only_pending(tmp_path) -> None:
    save_surface(_surface("GOOG", FRIDAY), tmp_path)
    save_session_reference(_reference("GOOG", FRIDAY), tmp_path)
    first_clock = _clock(datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc))
    router = FakeRouter(
        clock=first_clock,
        sessions=[_session(FRIDAY)],
        closes={"AAPL": DailyClose("AAPL", FRIDAY, 230.0, "alpaca/sip/1Day")},
        fail_chain={"AAPL"},
    )
    first = sync_completed_session(
        _account("AAPL", "GOOG"), home=tmp_path, router=router
    )
    assert router.chain_calls == ["AAPL"]
    assert first.next_retry_at is not None
    quote = datetime(2026, 8, 14, 15, 59, 59, tzinfo=_ET)
    later = FakeRouter(
        clock=_clock(first.next_retry_at + timedelta(seconds=1)),
        sessions=[_session(FRIDAY)],
        closes={"AAPL": DailyClose("AAPL", FRIDAY, 230.0, "alpaca/sip/1Day")},
        snapshots={
            "AAPL": {
                "AAPL261120C00350000": {
                    "impliedVolatility": 0.28,
                    "latestQuote": {"bp": 4.9, "ap": 5.1, "t": quote.isoformat()},
                }
            }
        },
    )
    later.daily_calls = []
    result = sync_completed_session(
        _account("AAPL", "GOOG"), home=tmp_path, router=later
    )
    assert later.chain_calls == ["AAPL"]
    assert "AAPL" in result.surfaces_saved


def test_force_rebuilds_existing_session_surface(tmp_path) -> None:
    save_surface(_surface("AAPL", FRIDAY), tmp_path)
    save_session_reference(_reference("AAPL", FRIDAY), tmp_path)
    quote = datetime(2026, 8, 14, 15, 59, 59, tzinfo=_ET)
    router = FakeRouter(
        clock=_clock(datetime(2026, 8, 14, 21, 0, tzinfo=timezone.utc)),
        sessions=[_session(FRIDAY)],
        snapshots={
            "AAPL": {
                "AAPL261120C00350000": {
                    "impliedVolatility": 0.33,
                    "latestQuote": {"bp": 4.9, "ap": 5.1, "t": quote.isoformat()},
                }
            }
        },
    )
    result = sync_completed_session(
        _account("AAPL"),
        home=tmp_path,
        router=router,
        force=True,
    )
    assert router.chain_calls == ["AAPL"]
    assert "AAPL" in result.surfaces_saved
