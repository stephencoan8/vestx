"""
Public market data for listed equities (SpaceX SPCX).

Primary source: Yahoo Finance chart API (widely used for free EOD + last price).
Fallback: Stooq daily CSV.

Prices from PUBLIC_MARKET_START onward are stored in market_prices and merged
with pre-IPO private user valuations in price_utils.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from flask import current_app, has_app_context

logger = logging.getLogger(__name__)

# Process-level throttle so multi-worker gunicorn doesn't hammer the API every request
_last_sync_attempt: Dict[str, datetime] = {}


def public_market_start() -> date:
    raw = '2026-06-12'
    if has_app_context():
        raw = current_app.config.get('PUBLIC_MARKET_START', raw)
    else:
        raw = os.getenv('PUBLIC_MARKET_START', raw)
    return datetime.strptime(raw, '%Y-%m-%d').date()


def stock_ticker() -> str:
    if has_app_context():
        return current_app.config.get('STOCK_TICKER', 'SPCX')
    return os.getenv('STOCK_TICKER', 'SPCX')


def sync_interval_minutes() -> int:
    if has_app_context():
        return int(current_app.config.get('MARKET_SYNC_MINUTES', 15))
    return int(os.getenv('MARKET_SYNC_MINUTES', '15'))


def _http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'Mozilla/5.0 (compatible; VestX/1.0; +https://github.com/stephencoan8/vestx)',
            'Accept': 'application/json,text/csv,*/*',
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_yahoo_daily(ticker: str, start: date, end: date) -> List[Tuple[date, float]]:
    """Fetch daily closes + latest quote from Yahoo chart API."""
    period1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    # period2 exclusive-ish; add a day
    end_dt = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=2)
    period2 = int(end_dt.timestamp())
    url = (
        f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}'
        f'?period1={period1}&period2={period2}&interval=1d&includePrePost=false'
    )
    raw = _http_get(url)
    data = json.loads(raw.decode('utf-8'))
    result = (data.get('chart') or {}).get('result') or []
    if not result:
        err = (data.get('chart') or {}).get('error')
        raise RuntimeError(f'Yahoo chart empty for {ticker}: {err}')

    block = result[0]
    timestamps = block.get('timestamp') or []
    quote = (block.get('indicators') or {}).get('quote') or [{}]
    closes = quote[0].get('close') or []

    out: List[Tuple[date, float]] = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        if d < start:
            continue
        out.append((d, float(close)))

    # Prefer live last price for "today" when market has a current quote
    meta = block.get('meta') or {}
    live = meta.get('regularMarketPrice')
    if live is not None:
        today = date.today()
        # Replace or append today with live quote
        out = [(d, p) for d, p in out if d != today]
        out.append((today, float(live)))

    out.sort(key=lambda x: x[0])
    return out


def fetch_stooq_daily(ticker: str, start: date) -> List[Tuple[date, float]]:
    """Fallback: Stooq daily CSV (e.g. spcx.us)."""
    symbol = f'{ticker.lower()}.us'
    url = f'https://stooq.com/q/d/l/?s={symbol}&i=d'
    raw = _http_get(url)
    text = raw.decode('utf-8', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    out: List[Tuple[date, float]] = []
    for row in reader:
        try:
            d = datetime.strptime(row['Date'], '%Y-%m-%d').date()
            if d < start:
                continue
            close = float(row['Close'])
            out.append((d, close))
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out


def fetch_public_daily_prices(ticker: Optional[str] = None, start: Optional[date] = None) -> Tuple[List[Tuple[date, float]], str]:
    """Return (bars, source_name). Tries Yahoo then Stooq."""
    ticker = ticker or stock_ticker()
    start = start or public_market_start()
    end = date.today()
    try:
        bars = fetch_yahoo_daily(ticker, start, end)
        if bars:
            return bars, 'yahoo'
    except Exception as e:
        logger.warning('Yahoo fetch failed for %s: %s', ticker, e)

    try:
        bars = fetch_stooq_daily(ticker, start)
        if bars:
            return bars, 'stooq'
    except Exception as e:
        logger.warning('Stooq fetch failed for %s: %s', ticker, e)

    raise RuntimeError(f'Could not fetch public market data for {ticker}')


def sync_market_prices(force: bool = False) -> dict:
    """
    Upsert daily public prices into market_prices.
    Throttled by MARKET_SYNC_MINUTES unless force=True.
    """
    from app import db
    from app.models.market_price import MarketPrice

    ticker = stock_ticker()
    start = public_market_start()
    now = datetime.utcnow()
    interval = timedelta(minutes=sync_interval_minutes())

    last = _last_sync_attempt.get(ticker)
    if not force and last and (now - last) < interval:
        # Still ensure we have some data
        count = MarketPrice.query.filter_by(ticker=ticker).count()
        return {'skipped': True, 'reason': 'throttled', 'rows': count, 'ticker': ticker}

    _last_sync_attempt[ticker] = now

    try:
        bars, source = fetch_public_daily_prices(ticker, start)
    except Exception as e:
        logger.error('Market sync failed: %s', e, exc_info=True)
        return {'skipped': False, 'error': str(e), 'ticker': ticker, 'upserted': 0}

    upserted = 0
    for d, price in bars:
        row = MarketPrice.query.filter_by(ticker=ticker, valuation_date=d).first()
        if row:
            if abs(row.price_per_share - price) > 1e-9 or row.source != source:
                row.price_per_share = price
                row.source = source
                upserted += 1
        else:
            db.session.add(
                MarketPrice(
                    ticker=ticker,
                    valuation_date=d,
                    price_per_share=price,
                    source=source,
                )
            )
            upserted += 1

    db.session.commit()
    logger.info(
        'Synced %s market prices for %s from %s (%s bars, %s changed)',
        source, ticker, start, len(bars), upserted,
    )
    return {
        'skipped': False,
        'ticker': ticker,
        'source': source,
        'bars': len(bars),
        'upserted': upserted,
        'start': start.isoformat(),
    }


def load_public_price_history(
    as_of: Optional[date] = None,
    ensure_sync: bool = True,
) -> List[Tuple[date, float]]:
    """Load public market history from DB (optionally sync first)."""
    from app.models.market_price import MarketPrice

    if ensure_sync:
        try:
            sync_market_prices(force=False)
        except Exception as e:
            logger.warning('Background market sync failed: %s', e)

    ticker = stock_ticker()
    start = public_market_start()
    q = MarketPrice.query.filter(
        MarketPrice.ticker == ticker,
        MarketPrice.valuation_date >= start,
    )
    if as_of is not None:
        q = q.filter(MarketPrice.valuation_date <= as_of)
    rows = q.order_by(MarketPrice.valuation_date.asc()).all()
    return [(r.valuation_date, float(r.price_per_share)) for r in rows]


def get_latest_public_price(as_of: Optional[date] = None) -> Optional[float]:
    history = load_public_price_history(as_of=as_of, ensure_sync=True)
    if not history:
        return None
    return history[-1][1]
