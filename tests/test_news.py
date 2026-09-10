from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from optionda.config import save_config
from optionda.models import Account, AppConfig, Position
from optionda.news import (
    NewsItem,
    holding_tickers,
    items_lookback,
    items_since,
    load_recent,
    mail_news_payload,
    poll_news,
    rank_with_ollama,
)


def _news_on(tmp_path) -> None:
    save_config(AppConfig(news_enabled=True), tmp_path)


def _pos(occ: str, underlying: str) -> Position:
    return Position(
        occ_symbol=occ,
        underlying=underlying,
        expiry=datetime(2026, 12, 18).date(),
        strike=100.0,
        option_type="call",
        qty=1,
        side="long",
        iv_frozen=0.3,
        iv_as_of=datetime(2026, 8, 27, tzinfo=timezone.utc),
        entry_premium=2.0,
    )


def test_holding_tickers_are_unique_and_sorted() -> None:
    account = Account(
        name="main",
        positions=[
            _pos("INTC261218C00140000", "INTC"),
            _pos("INTC261016C00140000", "INTC"),
            _pos("NVDA261218C00250000", "NVDA"),
        ],
    )
    assert holding_tickers(account) == ["INTC", "NVDA"]
    assert holding_tickers(Account(name="empty")) == []


def test_poll_news_dedups_by_id_and_skips_empty_book(tmp_path) -> None:
    _news_on(tmp_path)
    empty = Account(name="main")
    fetched: list[list[str]] = []

    def fetch(symbols: list[str]) -> list[dict]:
        fetched.append(symbols)
        return [
            {
                "id": "1",
                "ts": "2026-08-28T02:00:00Z",
                "symbol": "NVDA",
                "headline": "Nvidia rises",
                "url": "https://example.com/1",
            },
            {
                "id": "1",
                "ts": "2026-08-28T02:01:00Z",
                "symbol": "NVDA",
                "headline": "Nvidia rises again",
                "url": "https://example.com/1b",
            },
            {
                "id": "2",
                "ts": "2026-08-28T02:05:00Z",
                "symbol": "INTC",
                "headline": "Intel note",
                "url": "https://example.com/2",
            },
        ]

    first = poll_news(tmp_path, account=empty, fetch=fetch)
    assert first == []
    assert fetched == []

    book = Account(
        name="main",
        positions=[_pos("NVDA261218C00250000", "NVDA"), _pos("INTC261218C00140000", "INTC")],
    )
    items = poll_news(tmp_path, account=book, fetch=fetch)
    assert [item.id for item in items] == ["1", "2"]
    assert fetched == [["INTC", "NVDA"]]

    again = poll_news(tmp_path, account=book, fetch=fetch)
    assert again == []
    recent = load_recent(tmp_path, limit=10)
    assert [item.id for item in recent] == ["2", "1"]


def test_items_since_filters_by_timestamp(tmp_path) -> None:
    _news_on(tmp_path)
    book = Account(name="main", positions=[_pos("NVDA261218C00250000", "NVDA")])

    def fetch(_symbols: list[str]) -> list[dict]:
        return [
            {
                "id": "old",
                "ts": "2026-08-28T01:00:00Z",
                "symbol": "NVDA",
                "headline": "old",
                "url": "https://example.com/old",
            },
            {
                "id": "new",
                "ts": "2026-08-28T03:00:00Z",
                "symbol": "NVDA",
                "headline": "new",
                "url": "https://example.com/new",
            },
        ]

    poll_news(tmp_path, account=book, fetch=fetch)
    cut = datetime(2026, 8, 28, 2, 0, tzinfo=timezone.utc)
    got = items_since(tmp_path, cut)
    assert [item.id for item in got] == ["new"]


def test_mail_news_payload_is_empty_when_disabled(tmp_path) -> None:
    _news_on(tmp_path)
    book = Account(name="main", positions=[_pos("NVDA261218C00250000", "NVDA")])

    def fetch(_symbols: list[str]) -> list[dict]:
        return [
            {
                "id": "keep",
                "ts": datetime.now(timezone.utc).isoformat(),
                "symbol": "NVDA",
                "headline": "still cached",
                "url": "https://example.com/keep",
            }
        ]

    assert poll_news(tmp_path, account=book, fetch=fetch)
    save_config(AppConfig(news_enabled=False), tmp_path)
    assert mail_news_payload(tmp_path) == []


def test_items_lookback_keeps_last_24h_without_count_cap(tmp_path) -> None:
    _news_on(tmp_path)
    book = Account(name="main", positions=[_pos("NVDA261218C00250000", "NVDA")])
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)

    def fetch(_symbols: list[str]) -> list[dict]:
        rows = [
            {
                "id": "stale",
                "ts": (now - timedelta(hours=25)).isoformat(),
                "symbol": "NVDA",
                "headline": "too old",
                "url": "https://example.com/stale",
            }
        ]
        for i in range(20):
            rows.append(
                {
                    "id": f"keep-{i}",
                    "ts": (now - timedelta(hours=23, minutes=i)).isoformat(),
                    "symbol": "NVDA",
                    "headline": f"keep {i}",
                    "url": f"https://example.com/{i}",
                }
            )
        return rows

    poll_news(tmp_path, account=book, fetch=fetch)
    got = items_lookback(tmp_path, now=now)
    assert "stale" not in [item.id for item in got]
    assert len(got) == 20


def test_rank_with_ollama_skips_when_daemon_down() -> None:
    items = [
        NewsItem(
            id="1",
            ts=datetime(2026, 8, 28, 3, tzinfo=timezone.utc),
            symbol="NVDA",
            headline="Nvidia rises",
            url="https://example.com/1",
        )
    ]
    with patch("optionda.news.httpx.Client") as cls:
        cls.side_effect = OSError("connection refused")
        out = rank_with_ollama(items, ollama_url="http://127.0.0.1:11434")
    assert out == items
    assert out[0].rank is None
