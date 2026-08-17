from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from optionda.config import dividend_for_symbol, load_config, rate_for_days
from optionda.market.router import MarketDataError, MarketRouter
from optionda.market.iv_mid import quote_mid
from optionda.market.session import (
    CLOSE_GRACE,
    ClosePremiums,
    CompletedSessionState,
    MarketSession,
    SessionError,
    SessionReference,
    SessionSyncResult,
    load_close_premiums,
    load_pending_state,
    load_session_reference,
    merge_close_premiums,
    next_retry_at,
    resolve_completed_session,
    save_close_premiums,
    save_pending_state,
    save_session_reference,
)
from optionda.models import Account, Position, RowMark
from optionda.pricing.bs import price_option, years_to_expiry
from optionda.pricing.surface import (
    FRESH_CALIBRATION_QUOTE_AGE,
    MAX_CALIBRATION_QUOTE_AGE,
    IvSurface,
    build_surface,
    close_premium_from_surface,
    is_surface_fresh,
    load_surface,
    save_surface,
    estimate_overnight_iv,
    sticky_delta_iv,
    sticky_strike_iv,
    surface_matches_session,
    surface_session_date,
)

ProgressCallback = Callable[[str, int, int], None]


@dataclass
class CalibrationResult:
    surfaces: dict[str, IvSurface] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def fetch_completed_session(router: MarketRouter) -> CompletedSessionState:
    clock = router.get_market_clock()
    start = clock.timestamp.date() - timedelta(days=7)
    end = clock.timestamp.date() + timedelta(days=7)
    sessions = router.get_market_calendar(start, end)
    return resolve_completed_session(clock, sessions)


def calibrate_surfaces(
    account: Account,
    *,
    home: Path | None = None,
    router: MarketRouter | None = None,
    now: datetime | None = None,
    max_quote_age: timedelta | None = None,
    on_progress: ProgressCallback | None = None,
    only: set[str] | list[str] | None = None,
    target_session: MarketSession | None = None,
) -> CalibrationResult:
    """Build and persist one vendor-IV smile surface per held underlying.

    Failures are per-underlying: one dead ticker does not abort the rest.
    Chain fetches are slow; ``on_progress(label, done, total)`` reports each step.
    Pass ``only`` to restrict to new names (e.g. after add).
    """
    market = router or MarketRouter(home)
    cfg = load_config(home)
    current = now or datetime.now(timezone.utc)
    age = max_quote_age if max_quote_age is not None else MAX_CALIBRATION_QUOTE_AGE
    underlyings = sorted({position.underlying for position in account.positions})
    if only is not None:
        want = {name.strip().upper() for name in only}
        underlyings = [name for name in underlyings if name in want]
    total = max(len(underlyings), 1)

    def report(label: str, done: int) -> None:
        if on_progress is not None:
            on_progress(label, done, total)

    # Compatibility for lightweight test/dummy routers. The production router
    # implements get_spot_at and must never pair stale option quotes with this
    # later live/overnight snapshot.
    fallback_spots = (
        market.get_spots(underlyings)
        if not hasattr(market, "get_spot_at")
        else {}
    )
    report("spots…", 0)
    result = CalibrationResult()
    for index, underlying in enumerate(underlyings):
        report(f"{underlying} chain…", index)
        try:
            snapshots = market.get_option_chain_snapshots(underlying)
            if not snapshots:
                raise ValueError(
                    f"no usable surface nodes for {underlying}"
                )
            quote_time = _representative_option_quote_time(snapshots)
            if quote_time is None:
                raise ValueError("option chain has no timestamped quotes")
            if hasattr(market, "get_spot_at"):
                spot_q = market.get_spot_at(underlying, quote_time)
            else:
                spot_q = fallback_spots.get(underlying)
            if spot_q is None:
                raise ValueError(
                    f"no underlying spot at option quote time {quote_time.isoformat()}"
                )
            surface = build_surface(
                underlying,
                spot=spot_q.price,
                snapshots=snapshots,
                as_of=current,
                quote_as_of=quote_time,
                target_session=target_session,
                source=f"{market.feed_name}/chain",
                max_quote_age=age,
                rate=lambda days: rate_for_days(cfg, days),
                dividend=lambda symbol: dividend_for_symbol(cfg, symbol),
                style=cfg.option_style,
            )
            save_surface(surface, home)
            result.surfaces[underlying] = surface
            if surface.session_date is not None:
                persist_close_premiums(
                    underlying,
                    session_date=surface.session_date,
                    snapshots=snapshots,
                    positions=account.positions,
                    source=surface.source,
                    home=home,
                    now=current,
                )
            report(f"{underlying} ok", index + 1)
        except Exception as exc:  # noqa: BLE001
            result.errors[underlying] = str(exc)
            report(f"{underlying} skip", index + 1)
    if on_progress is not None:
        on_progress("done", total, total)
    return result


def ensure_surfaces(
    account: Account,
    underlyings: list[str],
    *,
    home: Path | None = None,
    router: MarketRouter | None = None,
    now: datetime | None = None,
    on_progress: ProgressCallback | None = None,
) -> CalibrationResult:
    """Calibrate close surfaces for names that do not already have a fresh one."""
    current = now or datetime.now(timezone.utc)
    missing = [
        name
        for name in sorted({item.strip().upper() for item in underlyings if item})
        if not _has_fresh_surface(name, home, current)
    ]
    if not missing:
        return CalibrationResult()
    return calibrate_surfaces(
        account,
        home=home,
        router=router,
        now=current,
        on_progress=on_progress,
        only=missing,
    )


def _has_fresh_surface(underlying: str, home: Path | None, now: datetime) -> bool:
    surface = load_surface(underlying, home)
    return surface is not None and is_surface_fresh(surface, now)


def sync_completed_session(
    account: Account,
    *,
    home: Path | None = None,
    router: MarketRouter | None = None,
    now: datetime | None = None,
    force: bool = False,
    fresh: bool = False,
    on_progress: ProgressCallback | None = None,
    only: set[str] | list[str] | None = None,
) -> SessionSyncResult:
    """Align official closes and frozen IV to the latest completed session."""
    market = router or MarketRouter(home)
    result = SessionSyncResult()
    underlyings = sorted({position.underlying for position in account.positions})
    if only is not None:
        want = {name.strip().upper() for name in only}
        underlyings = [name for name in underlyings if name in want]
    total = max(len(underlyings), 1)

    def report(label: str, done: int = 0) -> None:
        if on_progress is not None:
            on_progress(label, done, total)

    report("clock / calendar…", 0)
    try:
        state = fetch_completed_session(market)
    except (MarketDataError, SessionError, Exception) as exc:  # noqa: BLE001
        result.unavailable = str(exc)
        return result

    target = state.completed
    current = state.source_timestamp
    result.completed_session = target
    result.next_close_at = state.next_close_at
    if not underlyings:
        return result
    report(
        f"session {target.session_date.month}/{target.session_date.day}",
        0,
    )

    pending = load_pending_state(home)
    pending_session = pending.get("session_date")
    if pending_session != target.session_date.isoformat():
        pending = {"session_date": target.session_date.isoformat(), "tickers": {}}
    tickers = pending.setdefault("tickers", {})
    if not isinstance(tickers, dict):
        tickers = {}
        pending["tickers"] = tickers

    if current < _utc(target.close_at) + CLOSE_GRACE and not force:
        reason = "close grace"
        result.pending_surfaces = {name: reason for name in underlyings}
        result.next_retry_at = _utc(target.close_at) + CLOSE_GRACE
        for name in underlyings:
            tickers[name] = {
                "reason": reason,
                "attempt": 0,
                "next_retry_at": result.next_retry_at.isoformat(),
            }
        save_pending_state(pending, home)
        return result

    need_close = [
        name
        for name in underlyings
        if _reference_session(name, home) != target.session_date
    ]
    if need_close:
        shown = " ".join(need_close[:6]) + ("…" if len(need_close) > 6 else "")
        report(f"daily close {shown}", 0)
        try:
            closes = market.get_daily_closes(need_close, target.session_date)
        except Exception as exc:  # noqa: BLE001
            for name in need_close:
                result.pending_closes[name] = str(exc)
            closes = {}
        for name in need_close:
            bar = closes.get(name)
            if bar is None:
                result.pending_closes.setdefault(name, "close pending")
                continue
            reference = SessionReference(
                underlying=name,
                session_date=target.session_date,
                session_close_at=target.close_at,
                close_spot=bar.close,
                source=bar.source,
                updated_at=current,
            )
            save_session_reference(reference, home)
            result.references_saved[name] = reference

    need_iv: list[str] = []
    retry_times: list[datetime] = []
    for name in underlyings:
        surface = load_surface(name, home)
        if (
            surface is not None
            and surface_matches_session(surface, target)
            and not force
        ):
            tickers.pop(name, None)
            continue
        record = tickers.get(name) if isinstance(tickers.get(name), dict) else None
        retry_at = _parse_optional_ts((record or {}).get("next_retry_at"))
        if retry_at is not None and current < retry_at and not force:
            result.pending_surfaces[name] = str((record or {}).get("reason") or "IV pending")
            retry_times.append(retry_at)
            continue
        need_iv.append(name)

    if not need_iv:
        report("session ready", total)
    if need_iv:
        age = FRESH_CALIBRATION_QUOTE_AGE if fresh else MAX_CALIBRATION_QUOTE_AGE
        calibrated = calibrate_surfaces(
            account,
            home=home,
            router=market,
            now=now or current,
            max_quote_age=age,
            on_progress=on_progress,
            only=need_iv,
            target_session=target,
        )
        result.surfaces_saved.update(calibrated.surfaces)
        for name, surface in calibrated.surfaces.items():
            tickers.pop(name, None)
        for name, message in calibrated.errors.items():
            attempt = int((tickers.get(name) or {}).get("attempt") or 0) + 1
            retry = next_retry_at(attempt, current)
            tickers[name] = {
                "reason": message,
                "attempt": attempt,
                "next_retry_at": retry.isoformat(),
            }
            result.pending_surfaces[name] = message
            result.errors[name] = message
            retry_times.append(retry)

    if retry_times:
        result.next_retry_at = min(retry_times)
    save_pending_state(pending, home)
    return result


def _reference_session(underlying: str, home: Path | None) -> date | None:
    reference = load_session_reference(underlying, home)
    return reference.session_date if reference is not None else None


def _parse_optional_ts(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _representative_option_quote_time(
    snapshots: dict[str, dict],
) -> datetime | None:
    """Median chain quote timestamp used to align the underlying anchor spot."""
    times: list[datetime] = []
    for node in snapshots.values():
        quote = node.get("latestQuote") or node.get("quote") or {}
        if not isinstance(quote, dict):
            continue
        raw = quote.get("t") or quote.get("timestamp")
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        times.append(parsed.astimezone(timezone.utc))
    if not times:
        return None
    times.sort()
    return times[len(times) // 2]


def apply_surface_reference_ivs(
    positions: list[Position],
    surfaces: dict[str, IvSurface],
    *,
    spots: dict[str, float],
    rate: float | Callable[[float], float],
    dividend: float | Callable[[str], float],
    now: datetime,
) -> list[Position]:
    """Update fallback IVs from newly calibrated surfaces where possible."""
    refreshed: list[Position] = []
    for position in positions:
        surface = surfaces.get(position.underlying)
        spot = spots.get(position.underlying)
        if surface is None or spot is None:
            refreshed.append(position)
            continue
        years = years_to_expiry(position.expiry, now)
        position_rate = rate(years * 365.0) if callable(rate) else rate
        position_dividend = (
            dividend(position.underlying) if callable(dividend) else dividend
        )
        iv = sticky_delta_iv(
            surface,
            position,
            spot=spot,
            years=years,
            rate=position_rate,
            dividend=position_dividend,
        )
        if iv is None:
            refreshed.append(position)
            continue
        refreshed.append(
            position.model_copy(
                update={
                    "iv_frozen": iv,
                    "iv_as_of": surface.as_of,
                    "iv_source": f"surface/{surface.source}",
                }
            )
        )
    return refreshed


def harvest_close_premiums(
    underlying: str,
    *,
    session_date: date,
    snapshots: dict[str, dict],
    positions: list[Position],
    source: str,
    now: datetime | None = None,
) -> ClosePremiums:
    """Pull held-OCC mids from the same close chain used to freeze IV."""
    premiums: dict[str, float] = {}
    symbol = underlying.strip().upper()
    for position in positions:
        if position.underlying != symbol:
            continue
        node = snapshots.get(position.occ_symbol)
        if node is None:
            continue
        mid = quote_mid(node)
        if mid is not None and mid > 0:
            premiums[position.occ_symbol] = mid
    return ClosePremiums(
        underlying=symbol,
        session_date=session_date,
        premiums=premiums,
        source=source,
        updated_at=now or datetime.now(timezone.utc),
    )


def persist_close_premiums(
    underlying: str,
    *,
    session_date: date,
    snapshots: dict[str, dict],
    positions: list[Position],
    source: str,
    home: Path | None = None,
    now: datetime | None = None,
) -> ClosePremiums:
    incoming = harvest_close_premiums(
        underlying,
        session_date=session_date,
        snapshots=snapshots,
        positions=positions,
        source=source,
        now=now,
    )
    if not incoming.premiums:
        existing = load_close_premiums(underlying, home)
        if existing is not None and existing.session_date == session_date:
            return existing
        return incoming
    merged = merge_close_premiums(load_close_premiums(underlying, home), incoming)
    save_close_premiums(merged, home)
    return merged


def _session_aligned_book(
    book: ClosePremiums | None,
    *,
    reference_session: date | None,
    surface_session: date | None,
) -> ClosePremiums | None:
    if book is None:
        return None
    if reference_session is not None and book.session_date != reference_session:
        return None
    if (
        reference_session is None
        and surface_session is not None
        and book.session_date != surface_session
    ):
        return None
    return book


def resolve_close_premium(
    pos: Position,
    *,
    book: ClosePremiums | None,
    surface: IvSurface | None,
    close_spot: float | None,
    session_close_at: datetime | None,
    rate: float,
    dividend: float,
    style: str,
) -> float | None:
    """Close option price: stored mid, surface node/interp, else close-spot model."""
    if book is not None:
        stored = book.premiums.get(pos.occ_symbol)
        if stored is not None and stored > 0:
            return stored
    if surface is not None:
        from_surface = close_premium_from_surface(surface, pos)
        if from_surface is not None and from_surface > 0:
            return from_surface
    if close_spot is None or close_spot <= 0:
        return None
    as_of = session_close_at
    if as_of is None and surface is not None:
        as_of = surface.session_close_at or surface.as_of
    if as_of is None:
        return None
    close_iv = sticky_strike_iv(surface, pos) if surface is not None else None
    if close_iv is None:
        close_iv = pos.iv_frozen
    try:
        return price_option(
            spot=close_spot,
            strike=pos.strike,
            years=years_to_expiry(pos.expiry, as_of),
            iv=close_iv,
            rate=rate,
            dividend=dividend,
            option_type=pos.option_type,
            style=style,
            greeks=False,
        ).price
    except Exception:  # noqa: BLE001
        return None


def attach_live_option_mids(
    rows: list[RowMark],
    *,
    router: MarketRouter,
    on_progress: ProgressCallback | None = None,
) -> list[RowMark]:
    """Fetch live option mids for an explicit verify/backtest path only."""
    attached: list[RowMark] = []
    total = max(len(rows), 1)
    for index, row in enumerate(rows, start=1):
        if on_progress is not None:
            on_progress(f"live mid {row.position.occ_symbol}", index - 1, total)
        try:
            live = router.get_option_mid(row.position.occ_symbol)
        except Exception:  # noqa: BLE001
            live = None
        attached.append(row.model_copy(update={"live": live}))
    if on_progress is not None:
        on_progress("live mids ready", total, total)
    return attached


def mark_account(
    account: Account,
    *,
    home: Path | None = None,
    router: MarketRouter | None = None,
    now: datetime | None = None,
    on_progress: ProgressCallback | None = None,
    completed_session: MarketSession | None = None,
) -> list[RowMark]:
    cfg = load_config(home)
    market = router or MarketRouter(home)
    underlyings = [p.underlying for p in account.positions]
    n_pos = len(account.positions)
    total_steps = (1 if underlyings else 0) + max(n_pos, 0)
    done = 0

    def report(label: str) -> None:
        if on_progress is not None:
            on_progress(label, done, max(total_steps, 1))

    uniq = sorted(set(underlyings))
    report(
        "fetching spots · " + (" ".join(uniq[:6]) + ("…" if len(uniq) > 6 else ""))
        if uniq
        else "no positions"
    )
    spots = market.get_spots(underlyings) if underlyings else {}
    if underlyings:
        done = 1
        report("spots ready · loading IV surfaces")

    current = now or datetime.now(timezone.utc)
    rows: list[RowMark] = []
    surfaces = {
        underlying: load_surface(underlying, home)
        for underlying in set(underlyings)
    }
    references = {
        underlying: load_session_reference(underlying, home)
        for underlying in set(underlyings)
    }
    close_books = {
        underlying: load_close_premiums(underlying, home)
        for underlying in set(underlyings)
    }

    for index, pos in enumerate(account.positions, start=1):
        report(f"marking {index}/{n_pos}  {pos.occ_symbol}")
        spot_q = spots.get(pos.underlying)
        reference = references.get(pos.underlying)
        close_spot = reference.close_spot if reference is not None else None
        reference_session = (
            reference.session_date if reference is not None else None
        )

        if spot_q is None:
            rows.append(
                RowMark(
                    position=pos,
                    spot=None,
                    theo=None,
                    delta=None,
                    dte=None,
                    notional=None,
                    close_spot=close_spot,
                    reference_session_date=reference_session,
                    error="no spot",
                )
            )
            done += 1
            continue
        try:
            t = years_to_expiry(pos.expiry, current)
            rate = rate_for_days(cfg, t * 365.0)
            dividend = dividend_for_symbol(cfg, pos.underlying)
            surface = surfaces.get(pos.underlying)
            surface_iv = (
                sticky_delta_iv(
                    surface,
                    pos,
                    spot=spot_q.price,
                    years=t,
                    rate=rate,
                    dividend=dividend,
                )
                if surface is not None
                else None
            )
            estimate = (
                estimate_overnight_iv(
                    surface,
                    pos,
                    spot=spot_q.price,
                    years=t,
                    rate=rate,
                    dividend=dividend,
                    sticky_delta_weight=(
                        1.0 if cfg.overnight_iv_mode == "sticky_delta"
                        else 0.0 if cfg.overnight_iv_mode == "sticky_strike"
                        else cfg.sticky_delta_weight
                    ),
                )
                if surface is not None
                else None
            )
            surface_iv = estimate.base if estimate is not None else surface_iv
            model_iv = estimate.base if estimate is not None else pos.iv_frozen
            valuation_mode = "surface" if estimate is not None else "frozen"
            iv_fallback = estimate is None
            surface_session = (
                surface_session_date(surface) if surface is not None else None
            )
            iv_stale = iv_fallback
            if surface is not None:
                if surface.legacy:
                    iv_stale = True
                elif completed_session is not None:
                    iv_stale = not surface_matches_session(surface, completed_session)
            result = price_option(
                spot=spot_q.price,
                strike=pos.strike,
                years=t,
                iv=model_iv,
                rate=rate,
                dividend=dividend,
                option_type=pos.option_type,
                style=cfg.option_style,
            )
            low_result = (
                price_option(
                    spot=spot_q.price,
                    strike=pos.strike,
                    years=t,
                    iv=estimate.low,
                    rate=rate,
                    dividend=dividend,
                    option_type=pos.option_type,
                    style=cfg.option_style,
                    greeks=False,
                )
                if estimate is not None
                else None
            )
            high_result = (
                price_option(
                    spot=spot_q.price,
                    strike=pos.strike,
                    years=t,
                    iv=estimate.high,
                    rate=rate,
                    dividend=dividend,
                    option_type=pos.option_type,
                    style=cfg.option_style,
                    greeks=False,
                )
                if estimate is not None
                else None
            )
            strike_result = (
                price_option(
                    spot=spot_q.price,
                    strike=pos.strike,
                    years=t,
                    iv=estimate.sticky_strike,
                    rate=rate,
                    dividend=dividend,
                    option_type=pos.option_type,
                    style=cfg.option_style,
                    greeks=False,
                )
                if estimate is not None
                else None
            )
            delta_result = (
                price_option(
                    spot=spot_q.price,
                    strike=pos.strike,
                    years=t,
                    iv=estimate.sticky_delta,
                    rate=rate,
                    dividend=dividend,
                    option_type=pos.option_type,
                    style=cfg.option_style,
                    greeks=False,
                )
                if estimate is not None and estimate.sticky_delta is not None
                else None
            )
            sign = 1.0 if pos.side == "long" else -1.0
            notional = result.price * pos.multiplier * pos.qty * sign
            cost = pos.entry_premium
            upnl = None
            if cost is not None:
                upnl = (result.price - cost) * pos.multiplier * pos.qty * sign
            book = _session_aligned_book(
                close_books.get(pos.underlying),
                reference_session=reference_session,
                surface_session=surface_session,
            )
            close_premium = resolve_close_premium(
                pos,
                book=book,
                surface=surface,
                close_spot=close_spot,
                session_close_at=(
                    reference.session_close_at if reference is not None else None
                ),
                rate=rate,
                dividend=dividend,
                style=cfg.option_style,
            )
            theo_chg = (
                result.price - close_premium if close_premium is not None else None
            )
            rows.append(
                RowMark(
                    position=pos,
                    spot=spot_q.price,
                    theo=result.price,
                    delta=result.delta * sign,
                    dte=result.dte,
                    notional=notional,
                    cost=cost,
                    upnl=upnl,
                    valuation_mode=valuation_mode,
                    surface_iv=surface_iv,
                    model_iv=model_iv,
                    surface_as_of=surface.as_of if surface is not None else None,
                    surface_source=surface.source if surface is not None else None,
                    surface_session_date=surface_session,
                    reference_session_date=reference_session,
                    iv_stale=iv_stale,
                    iv_fallback=iv_fallback,
                    model_low=low_result.price if low_result is not None else None,
                    model_high=high_result.price if high_result is not None else None,
                    iv_dynamics=estimate.method if estimate is not None else "frozen",
                    sticky_strike_iv=(
                        estimate.sticky_strike if estimate is not None else None
                    ),
                    sticky_delta_iv=(
                        estimate.sticky_delta if estimate is not None else None
                    ),
                    sticky_strike_model=(
                        strike_result.price if strike_result is not None else None
                    ),
                    sticky_delta_model=(
                        delta_result.price if delta_result is not None else None
                    ),
                    rate_used=rate,
                    dividend_used=dividend,
                    spot_as_of=spot_q.as_of,
                    spot_source=spot_q.source,
                    close_spot=close_spot,
                    close_premium=close_premium,
                    theo_chg=theo_chg,
                )
            )
        except Exception as exc:  # noqa: BLE001
            rows.append(
                RowMark(
                    position=pos,
                    spot=spot_q.price,
                    theo=None,
                    delta=None,
                    dte=None,
                    notional=None,
                    close_spot=close_spot,
                    reference_session_date=reference_session,
                    error=str(exc),
                )
            )
        done += 1
    if on_progress is not None:
        on_progress("done", max(total_steps, 1), max(total_steps, 1))
    return rows


def freeze_iv_for_position(
    position: Position,
    *,
    iv: float | None,
    home: Path | None = None,
    router: MarketRouter | None = None,
) -> Position:
    if iv is not None:
        return position.model_copy(
            update={
                "iv_frozen": iv,
                "iv_as_of": datetime.now(timezone.utc),
                "iv_source": "manual",
            }
        )
    market = router or MarketRouter(home)
    quote = market.get_option_iv(position.occ_symbol)
    return position.model_copy(
        update={
            "iv_frozen": quote.iv,
            "iv_as_of": quote.as_of or datetime.now(timezone.utc),
            "iv_source": quote.source,
        }
    )
