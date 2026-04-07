#!/usr/bin/env python3
"""
Daily VCP Scanner for Taiwan Stocks
Runs after market close via GitHub Actions.
Fetches all TSE + OTC stocks, calculates VCP score, writes vcp_daily.json.
"""

import json, time, datetime, sys, math
import requests
import yfinance as yf
import pandas as pd

# ── Config ──────────────────────────────────────────────────────────────────
BATCH_SIZE     = 10      # parallel downloads via yfinance
REQUEST_DELAY  = 0.5     # seconds between batches
MIN_SCORE_SAVE = 30      # only save stocks with VCP score >= this
MAX_RETRIES    = 2

# ── Fetch Taiwan stock list ──────────────────────────────────────────────────
def fetch_tse_stocks():
    """Fetch all TSE (上市) stocks from TWSE open API."""
    url = 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'
    try:
        r = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
        data = r.json()
        stocks = []
        for s in data:
            code = str(s.get('Code', '')).strip()
            name = str(s.get('Name', code)).strip()
            if len(code) == 4 and code.isdigit():
                stocks.append({'code': code, 'name': name, 'sector': '上市', 'ex': 'TW'})
        print(f'  TSE: {len(stocks)} stocks')
        return stocks
    except Exception as e:
        print(f'  TSE API error: {e}')
        return []

def fetch_otc_stocks():
    """Fetch all OTC (上櫃) stocks from TPEX open API."""
    urls = [
        'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_perday_quotation',
        'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes',
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
            data = r.json()
            if not isinstance(data, list) or not data:
                continue
            sample = data[0]
            # Auto-detect field names
            code_key = next((k for k in sample if 'code' in k.lower() or 'securities' in k.lower()), None)
            name_key = next((k for k in sample if 'name' in k.lower() or 'company' in k.lower()), None)
            if not code_key:
                continue
            stocks = []
            for s in data:
                code = str(s.get(code_key, '')).strip()
                name = str(s.get(name_key, code) if name_key else code).strip()
                if len(code) == 4 and code.isdigit():
                    stocks.append({'code': code, 'name': name, 'sector': '上櫃', 'ex': 'TWO'})
            print(f'  OTC: {len(stocks)} stocks (from {url})')
            return stocks
        except Exception as e:
            print(f'  OTC API {url} error: {e}')
    print('  OTC: API failed, using empty list')
    return []

# ── VCP Calculation ──────────────────────────────────────────────────────────
def sma(series, n):
    if len(series) < int(n * 0.8):
        return None
    return series[:n].mean()

def calc_vcp(df):
    """Calculate VCP score from a DataFrame with columns: open/high/low/close/volume."""
    if df is None or len(df) < 55:
        return None
    df = df.dropna(subset=['close'])
    if len(df) < 55:
        return None

    closes = df['close'].values
    highs  = df['high'].values
    vols   = df['volume'].values
    price  = closes[0]
    if not price or price <= 0:
        return None

    ma50  = sma(pd.Series(closes), 50)
    ma150 = sma(pd.Series(closes), 150) if len(closes) >= 150 else None
    ma200 = sma(pd.Series(closes), 200) if len(closes) >= 200 else None

    valid_highs = [h for h in highs[:min(252, len(highs))] if h and not math.isnan(h)]
    h52 = max(valid_highs) if valid_highs else price
    pct_h = ((h52 - price) / h52 * 100) if h52 > 0 else 999

    # ATR contraction
    atr_s, atr_l = None, None
    if len(df) >= 21:
        def atr_n(n):
            tr_list = []
            for i in range(n):
                if i+1 >= len(df): break
                h, l, pc = df['high'].iloc[i], df['low'].iloc[i], df['close'].iloc[i+1]
                if any(v is None or math.isnan(v) for v in [h, l, pc]): continue
                tr_list.append(max(h-l, abs(h-pc), abs(l-pc)))
            return sum(tr_list)/len(tr_list) if tr_list else None
        atr_s = atr_n(10)
        atr_l = atr_n(20)
    atr_ratio = (atr_s / atr_l) if (atr_s and atr_l and atr_l > 0) else None

    # Volume contraction
    v5  = pd.Series(vols[:5]).mean()  if len(vols) >= 5  else None
    v20 = pd.Series(vols[:20]).mean() if len(vols) >= 20 else None
    v_ratio = (v5 / v20) if (v5 and v20 and v20 > 0) else None

    # Score
    score = 0
    ab200 = ma200 is not None and price > ma200
    ab150 = ma150 is not None and price > ma150
    ab50  = ma50  is not None and price > ma50
    perf  = (ma50 and ma150 and ma200 and ma50 > ma150 and ma150 > ma200)

    if ab200: score += 10
    if ab150: score += 5
    if ab50:  score += 5
    if perf:  score += 10
    if pct_h < 25: score += 10
    if pct_h < 15: score += 10

    atr_label, atr_sig = '—', 'n'
    if atr_ratio is not None:
        if atr_ratio < 0.75:   score += 30; atr_label = '強收縮'; atr_sig = 'g'
        elif atr_ratio < 0.90: score += 15; atr_label = '收縮中'; atr_sig = 'o'
        else:                  atr_label = '未收縮'; atr_sig = 'r'

    vol_label, vol_sig = '—', 'n'
    if v_ratio is not None:
        if v_ratio < 0.70:   score += 20; vol_label = '強縮量'; vol_sig = 'g'
        elif v_ratio < 0.85: score += 10; vol_label = '縮量';   vol_sig = 'o'
        else:                vol_label = '量持平'; vol_sig = 'r'

    return {
        'score':   min(round(score), 100),
        'price':   round(float(price), 2),
        'ma50':    round(float(ma50), 2)  if ma50  else None,
        'ma150':   round(float(ma150), 2) if ma150 else None,
        'ma200':   round(float(ma200), 2) if ma200 else None,
        'h52':     round(float(h52), 2),
        'pctH':    round(float(pct_h), 1),
        'abM50':   bool(ab50),
        'abM150':  bool(ab150),
        'abM200':  bool(ab200),
        'stage2':  bool(ab50 and ab150 and ab200),
        'perfOrd': bool(perf),
        'atrL':    atr_label,
        'atrS':    atr_sig,
        'atrRat':  round(float(atr_ratio), 3) if atr_ratio else None,
        'volL':    vol_label,
        'volS':    vol_sig,
        'vRat':    round(float(v_ratio), 2)  if v_ratio  else None,
    }

# ── Batch download via yfinance ──────────────────────────────────────────────
def download_batch(symbols):
    """Download 1-year daily data for a batch of symbols. Returns dict sym->df."""
    results = {}
    try:
        raw = yf.download(
            symbols, period='1y', interval='1d',
            auto_adjust=True, progress=False, threads=True,
            timeout=30
        )
        if raw.empty:
            return results

        if isinstance(raw.columns, pd.MultiIndex):
            # Multi-stock download
            for sym in symbols:
                try:
                    df = raw.xs(sym, axis=1, level=1).copy() if sym in raw.columns.get_level_values(1) else None
                    if df is not None and not df.empty:
                        df.columns = [c.lower() for c in df.columns]
                        df = df.sort_index(ascending=False).reset_index(drop=True)
                        results[sym] = df
                except Exception:
                    pass
        else:
            # Single-stock download
            sym = symbols[0]
            df = raw.copy()
            df.columns = [c.lower() for c in df.columns]
            df = df.sort_index(ascending=False).reset_index(drop=True)
            results[sym] = df
    except Exception as e:
        print(f'    batch download error: {e}')
    return results

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    start_time = time.time()
    print('=== Taiwan VCP Daily Scanner ===')
    print(f'Start: {datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")} (Taipei)')

    print('\n1. Fetching stock list...')
    tse = fetch_tse_stocks()
    otc = fetch_otc_stocks()
    all_stocks = tse + otc

    # Deduplicate by code
    seen = set()
    stocks = []
    for s in all_stocks:
        if s['code'] not in seen:
            seen.add(s['code'])
            stocks.append(s)

    print(f'   Total unique stocks: {len(stocks)}')

    print(f'\n2. Scanning VCP for {len(stocks)} stocks...')
    results = []
    total  = len(stocks)
    done   = 0
    failed = 0

    for i in range(0, total, BATCH_SIZE):
        batch = stocks[i:i+BATCH_SIZE]
        syms  = [f"{s['code']}.{s['ex']}" for s in batch]

        data_map = download_batch(syms)

        for s in batch:
            sym = f"{s['code']}.{s['ex']}"
            df  = data_map.get(sym)
            vcp = calc_vcp(df) if df is not None else None
            if vcp is None:
                failed += 1
            if vcp and vcp['score'] >= MIN_SCORE_SAVE:
                results.append({**s, 'sym': sym, 'vcp': vcp})
            done += 1

        pct = done / total * 100
        vcp_found = len(results)
        sys.stdout.write(f'\r   {done}/{total} ({pct:.0f}%) | VCP candidates: {vcp_found} | No data: {failed}   ')
        sys.stdout.flush()
        time.sleep(REQUEST_DELAY)

    print(f'\n\n   Done! {done-failed} stocks scanned, {len(results)} VCP candidates (score≥{MIN_SCORE_SAVE})')

    # Sort by score descending
    results.sort(key=lambda r: r['vcp']['score'], reverse=True)

    # Write output
    now_tw = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    output = {
        'scanDate':      now_tw.strftime('%Y-%m-%d'),
        'scanTime':      now_tw.strftime('%H:%M'),
        'totalScanned':  done - failed,
        'totalStocks':   total,
        'vcpCandidates': len(results),
        'stocks':        results,
    }

    out_path = 'vcp_daily.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    elapsed = time.time() - start_time
    print(f'   Saved to {out_path} ({len(results)} records)')
    print(f'   Total time: {elapsed/60:.1f} minutes')

if __name__ == '__main__':
    main()
