"""Holdings flash news: Alpaca headlines, local cache, optional Ollama rank."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from optionda.config import load_config
from optionda.models import Account
from optionda.paths import ensure_home

NEWS_LIMIT = 30
NEWS_LOOKBACK = timedelta(hours=24)
_DEFAULT_SINCE = timedelta(minutes=30)
_OLLAMA_TIMEOUT = 2.5


@dataclass(frozen=True)
class NewsItem:
    id: str
    ts: datetime
    symbol: str
    headline: str
    url: str
    rank: str | None = None
    note: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts.isoformat(),
            "symbol": self.symbol,
            "headline": self.headline,
            "url": self.url,
            "rank": self.rank,
            "note": self.note,
        }


def news_dir(home: Path | None = None) -> Path:
    path = ensure_home(home) / "news"
    path.mkdir(parents=True, exist_ok=True)
    return path


def items_path(home: Path | None = None) -> Path:
    return news_dir(home) / "items.jsonl"


def holding_tickers(account: Account | None) -> list[str]:
    if account is None:
        return []
    names = {pos.underlying.strip().upper() for pos in account.positions if pos.underlying}
    return sorted(names)


def _parse_ts(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        instant = value
    elif isinstance(value, str) and value.strip():
        try:
            instant = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc)


def normalize_item(raw: dict[str, Any]) -> NewsItem | None:
    nid = str(raw.get("id") or "").strip()
    headline = str(raw.get("headline") or "").strip()
    if not nid or not headline:
        return None
    ts = _parse_ts(raw.get("ts") or raw.get("created_at") or raw.get("updated_at"))
    if ts is None:
        ts = datetime.now(timezone.utc)
    symbol = str(raw.get("symbol") or "").strip().upper()
    return NewsItem(
        id=nid,
        ts=ts,
        symbol=symbol,
        headline=headline,
        url=str(raw.get("url") or "").strip(),
        rank=str(raw["rank"]).strip() if raw.get("rank") else None,
        note=str(raw["note"]).strip() if raw.get("note") else None,
    )


def load_recent(home: Path | None = None, *, limit: int = NEWS_LIMIT) -> list[NewsItem]:
    path = items_path(home)
    if not path.exists():
        return []
    items: list[NewsItem] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        item = normalize_item(raw)
        if item is None or item.id in seen:
            continue
        seen.add(item.id)
        items.append(item)
    items.sort(key=lambda item: item.ts, reverse=True)
    return items[: max(int(limit), 0)]


def items_since(home: Path | None, since: datetime | None) -> list[NewsItem]:
    items = load_recent(home, limit=500)
    if since is None:
        return items
    cut = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
    cut = cut.astimezone(timezone.utc)
    return [item for item in items if item.ts > cut]


def items_lookback(
    home: Path | None = None,
    *,
    lookback: timedelta | None = None,
    now: datetime | None = None,
) -> list[NewsItem]:
    window = NEWS_LOOKBACK if lookback is None else lookback
    instant = now or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return items_since(home, instant.astimezone(timezone.utc) - window)


def last_mail_sent_at(home: Path | None = None) -> datetime | None:
    from optionda.mailer import read_sends

    for row in reversed(read_sends(home, limit=32)):
        if not row.get("ok"):
            continue
        instant = _parse_ts(row.get("ts"))
        if instant is not None:
            return instant
    return None


def _seen_ids(home: Path | None) -> set[str]:
    return {item.id for item in load_recent(home, limit=500)}


def _append_items(home: Path | None, items: list[NewsItem]) -> None:
    if not items:
        return
    path = items_path(home)
    with path.open("a", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item.as_payload(), ensure_ascii=False) + "\n")


def _default_fetch(
    home: Path | None,
    symbols: list[str],
    since: datetime | None,
) -> list[dict[str, Any]]:
    from optionda.credentials import load_alpaca
    from optionda.market.alpaca import AlpacaClient

    creds = load_alpaca(home)
    if creds is None:
        return []
    cfg = load_config(home)
    client = AlpacaClient(
        creds,
        options_feed=cfg.alpaca_options_feed,
        iv_mode=cfg.iv_mode,
        home=home,
    )
    return client.get_news(symbols, since=since)


def poll_news(
    home: Path | None = None,
    *,
    account: Account | None = None,
    fetch: Callable[[list[str]], list[dict[str, Any]]] | None = None,
    since: datetime | None = None,
    mark_seen: bool = True,
) -> list[NewsItem]:
    cfg = load_config(home)
    if not cfg.news_enabled:
        return []
    acc = account
    if acc is None:
        from optionda.store import AccountStore, StoreError

        try:
            acc = AccountStore(home).require_current()
        except StoreError:
            return []
    symbols = holding_tickers(acc)
    if not symbols:
        return []
    raw = fetch(symbols) if fetch is not None else _default_fetch(home, symbols, since)
    known = _seen_ids(home)
    fresh: list[NewsItem] = []
    seen_batch: set[str] = set()
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        item = normalize_item(rec)
        if item is None or item.id in known or item.id in seen_batch:
            continue
        seen_batch.add(item.id)
        fresh.append(item)
    fresh.sort(key=lambda item: item.ts)
    fresh = rank_with_ollama(fresh, home=home)
    if mark_seen:
        _append_items(home, fresh)
    return fresh


def mail_news_payload(home: Path | None = None) -> list[dict[str, Any]]:
    if not load_config(home).news_enabled:
        return []
    poll_news(home)
    since = last_mail_sent_at(home) or datetime.now(timezone.utc) - _DEFAULT_SINCE
    return [item.as_payload() for item in items_since(home, since)]


def rank_with_ollama(
    items: list[NewsItem],
    *,
    home: Path | None = None,
    ollama_url: str | None = None,
    model: str | None = None,
) -> list[NewsItem]:
    if not items:
        return items
    cfg = load_config(home)
    url = (ollama_url if ollama_url is not None else cfg.ollama_url).rstrip("/")
    if not url:
        return items
    chosen = (model if model is not None else cfg.ollama_model).strip()
    try:
        with httpx.Client(timeout=_OLLAMA_TIMEOUT) as client:
            if not chosen:
                tags = client.get(f"{url}/api/tags")
                tags.raise_for_status()
                models = (tags.json() or {}).get("models") or []
                if not models:
                    return items
                chosen = str(models[0].get("name") or "").strip()
            if not chosen:
                return items
            lines = "\n".join(f"{item.id}\t{item.symbol}\t{item.headline}" for item in items)
            prompt = (
                "Rank each headline 重要 or 普通. Reply JSON array only: "
                '[{"id":"...","rank":"重要"}]\n' + lines
            )
            response = client.post(
                f"{url}/api/generate",
                json={"model": chosen, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            text = str((response.json() or {}).get("response") or "")
    except Exception:  # noqa: BLE001 - ollama is optional
        return items
    ranks = _parse_rank_json(text)
    if not ranks:
        return items
    out: list[NewsItem] = []
    for item in items:
        rank = ranks.get(item.id)
        out.append(replace(item, rank=rank) if rank else item)
    return out


def _parse_rank_json(text: str) -> dict[str, str]:
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, list):
        return {}
    out: dict[str, str] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        nid = str(row.get("id") or "").strip()
        rank = str(row.get("rank") or "").strip()
        if nid and rank:
            out[nid] = rank
    return out


def format_news_line(item: NewsItem) -> str:
    stamp = item.ts.astimezone().strftime("%H:%M")
    symbol = item.symbol or "?"
    prefix = f"{stamp}  {symbol}"
    if item.rank:
        prefix = f"{prefix}  {item.rank}"
    return f"{prefix}  {item.headline}"
