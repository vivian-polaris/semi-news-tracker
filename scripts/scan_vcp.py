#!/usr/bin/env python3
"""
VCP Scanner — reads stocks_tse.json + stocks_otc.json
Algorithm is a 1:1 port of calcVCP() in VCPfinder.html.
Results will match what VCPfinder shows in the browser.
"""

import json, math, datetime, sys, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

MIN_SCORE_SAVE = 40

# ── Helper functions (ported from VCPfinder.html) ────────────────────────────

def sma(arr, n):
    if not arr or len(arr) < n:
        return None
    v = [x for x in arr[:n] if x is not None and math.isfinite(x)]
    if len(v) < math.ceil(n * 0.8):
        return None
    return sum(v) / n

def calc_atr(data, n):
    if not data or len(data) < n + 1:
        return None
    s, c = 0, 0
    for i in range(n):
        if i + 1 >= len(data):
            break
        h, l, pc = data[i].get('high'), data[i].get('low'), data[i+1].get('close')
        if h is None or l is None or pc is None:
            continue
        s += max(h - l, abs(h - pc), abs(l - pc))
        c += 1
    return s / c if c > 0 else None

def calc_rsi(closes, period=14):
    if not closes or len(closes) < period + 1:
        return None
    arr = list(reversed(closes))  # oldest → newest
    g = l = 0
    for i in range(1, period + 1):
        d = arr[i] - arr[i-1]
        if d > 0: g += d
        else:     l -= d
    ag, al = g / period, l / period
    for i in range(period + 1, len(arr)):
        d = arr[i] - arr[i-1]
        ag = (ag * (period - 1) + max(0, d)) / period
        al = (al * (period - 1) + max(0, -d)) / period
    if al == 0:
        return 100
    return round(100 - (100 / (1 + ag / al)))

def count_contractions(highs, lows):
    if not highs or len(highs) < 30:
        return 0
    arr = list(reversed(highs))[:120]   # oldest → newest
    la  = list(reversed(lows))[:120] if lows else None
    peaks = [
        {'i': i, 'v': arr[i]}
        for i in range(2, len(arr) - 2)
        if arr[i] > arr[i-1] and arr[i] > arr[i-2] and arr[i] > arr[i+1] and arr[i] > arr[i+2]
    ]
    if len(peaks) < 2:
        return 0
    count, last_d = 0, float('inf')
    for i in range(len(peaks) - 1):
        seg = (la if la else arr)[peaks[i]['i']:peaks[i+1]['i']]
        if not seg:
            continue
        lo = min(seg)
        d  = (peaks[i]['v'] - lo) / peaks[i]['v'] * 100
        if d < last_d and d > 0.5 and peaks[i+1]['v'] <= peaks[i]['v'] * 1.03:
            count += 1
            last_d = d
    return min(count, 5)

def detect_rising_fin(data):
    if not data or len(data) < 12:
        return None
    best = None
    for end_day in range(1, 9):
        count = total_gain = 0
        has_limit = False
        run_vols = []
        for j in range(end_day, end_day + 8):
            if j >= len(data) - 1:
                break
            dc = data[j].get('close')
            pc = data[j+1].get('close')
            if dc is None or pc is None:
                break
            gain = (dc - pc) / pc * 100
            if gain >= 3:
                count += 1; total_gain += gain
                if gain >= 8: has_limit = True
                if data[j].get('volume'): run_vols.append(data[j]['volume'])
            else:
                break
        if count >= 3:
            best = {'end_day': end_day, 'count': count, 'total_gain': round(total_gain, 1),
                    'has_limit': has_limit, 'run_vols': run_vols}
            break
    if not best:
        return None
    streak_high = data[best['end_day']].get('close') if best['end_day'] < len(data) else None
    price = data[0].get('close')
    if not streak_high or not price:
        return None
    pullback = (streak_high - price) / streak_high * 100
    if not (1 <= pullback <= 25):
        return None
    avg_run_vol = sum(best['run_vols']) / len(best['run_vols']) if best['run_vols'] else None
    recent_vol  = data[0].get('volume') or (data[1].get('volume') if len(data) > 1 else None)
    vol_ratio   = round(recent_vol / avg_run_vol, 2) if avg_run_vol and recent_vol else None
    is_exh  = vol_ratio is not None and vol_ratio < 0.4
    is_shrk = vol_ratio is not None and vol_ratio < 0.6
    sc = (40 if best['count'] >= 5 else 30) + (20 if best['has_limit'] else 10) \
       + (30 if is_exh else 15 if is_shrk else 0) + (10 if 3 <= pullback <= 10 else 0)
    return {
        'streakDays': best['count'], 'hasLimit': best['has_limit'],
        'totalGain': best['total_gain'], 'pullback': round(pullback, 1),
        'volRatio': vol_ratio, 'isExhaustion': is_exh, 'isShrink': is_shrk, 'finScore': sc,
    }

# ── Main VCP scoring (1:1 port of VCPfinder.html calcVCP) ───────────────────

def calc_vcp(data):
    if not data or len(data) < 55:
        return None
    fin    = detect_rising_fin(data)
    closes  = [d.get('close')  for d in data]
    highs   = [d.get('high')   for d in data]
    lows    = [d.get('low')    for d in data]
    volumes = [d.get('volume') or 0 for d in data]
    price   = closes[0]
    if not price or price <= 0:
        return None

    ma5   = sma(closes,  5)
    ma10  = sma(closes, 10)
    ma20  = sma(closes, 20)
    ma50  = sma(closes, 50)
    ma150 = sma(closes, 150) if len(closes) >= 150 else None
    ma200 = sma(closes, 200) if len(closes) >= 200 else None

    valid_h = [h for h in highs[:min(252, len(highs))] if h is not None and math.isfinite(h)]
    valid_l = [l for l in lows[:min(252, len(lows))]   if l is not None and math.isfinite(l)]
    h52  = max(valid_h) if valid_h else price
    lo52 = min(valid_l) if valid_l else price
    pct_h = ((h52 - price) / h52 * 100) if h52 > 0 else 999

    atr10 = calc_atr(data, 10)
    atr20 = calc_atr(data, 20)
    atr_rat = atr10 / atr20 if (atr10 and atr20 and atr20 > 0) else None

    v5  = sma(volumes[:5],  5)  if len(volumes) >= 5  else None
    v10 = sma(volumes[:10], 10) if len(volumes) >= 10 else None
    v20 = sma(volumes[:20], 20) if len(volumes) >= 20 else None
    v_rat = v5 / v20 if (v5 and v20 and v20 > 0) else None

    score = 0
    ab200 = ma200 is not None and price > ma200
    ab150 = ma150 is not None and price > ma150
    ab50  = ma50  is not None and price > ma50
    perf  = ma50 is not None and ma150 is not None and ma200 is not None \
            and ma50 > ma150 and ma150 > ma200

    if ab200: score += 10
    if ab150: score += 5
    if ab50:  score += 5
    if perf:  score += 10

    if   pct_h < 8:  score += 30
    elif pct_h < 15: score += 20
    elif pct_h < 25: score += 10

    atr_l = '—'; atr_s = 'n'
    if atr_rat is not None:
        if   atr_rat < 0.75: score += 15; atr_l = '強收縮'; atr_s = 'g'
        elif atr_rat < 0.90: score += 8;  atr_l = '收縮中'; atr_s = 'o'
        else:                              atr_l = '未收縮'; atr_s = 'r'

    vol_l = '—'; vol_s = 'n'
    if v_rat is not None:
        if   v_rat < 0.70: score += 20; vol_l = '強縮量'; vol_s = 'g'
        elif v_rat < 0.85: score += 10; vol_l = '縮量';   vol_s = 'o'
        else:                            vol_l = '量持平'; vol_s = 'r'

    d0 = data[0]
    close_ratio = None
    if d0.get('high') and d0.get('low') and d0['high'] > d0['low']:
        close_ratio = round((price - d0['low']) / (d0['high'] - d0['low']), 2)

    if ma20 and ma50 and ma20 > ma50:
        score += 5

    rsi = calc_rsi(closes, 14)
    if rsi is not None:
        if 55 <= rsi <= 68: score += 10
        elif 50 <= rsi < 55: score += 5

    contractions = count_contractions(highs, lows) if ab50 else 0
    if   contractions >= 3: score += 8
    elif contractions >= 2: score += 4

    ma_flat_l = '—'; ma_flat_s = 'n'
    if ma5 and ma10 and ma20 and ma20 > 0:
        ma_spread = (max(ma5, ma10, ma20) - min(ma5, ma10, ma20)) / ma20 * 100
        if   ma_spread < 3: score += 20; ma_flat_l = '強糾纏'; ma_flat_s = 'g'
        elif ma_spread < 6: score += 10; ma_flat_l = '糾纏中'; ma_flat_s = 'o'
        else:                            ma_flat_l = '未糾纏'; ma_flat_s = 'r'

    left_mountain = False
    for d in data[15:min(60, len(data))]:
        op, cl, hi, lo = d.get('open'), d.get('close'), d.get('high'), d.get('low')
        if not all([op, cl, hi, lo]):
            continue
        if (hi + lo) / 2 > 0 and abs(cl - op) / ((hi + lo) / 2) * 100 > 10 and cl > op:
            left_mountain = True; break
    if left_mountain:
        score += 5

    # Pivot
    prev = data[1] if len(data) > 1 else None
    pp = r1 = r2 = s1 = s2 = None
    if prev and prev.get('high') and prev.get('low') and prev.get('close'):
        pp = round((prev['high'] + prev['low'] + prev['close']) / 3, 2)
        r1 = round(2*pp - prev['low'], 2)
        r2 = round(pp + (prev['high'] - prev['low']), 2)
        s1 = round(2*pp - prev['high'], 2)
        s2 = round(pp - (prev['high'] - prev['low']), 2)

    def _maxh(start, end):
        arr = [d.get('high') for d in data[start:min(end, len(data))]]
        arr = [v for v in arr if v is not None and math.isfinite(v)]
        return max(arr) if arr else None

    m30 = _maxh(1, 31); m20 = _maxh(1, 21); m10 = _maxh(1, 11)
    vcp_pivot = round(m10, 2) if (m30 and m10 and m30 > m10 * 1.08 and m10) \
                else (round(m20, 2) if m20 else None)
    dist_vcp  = round((price - vcp_pivot) / vcp_pivot * 100, 1) if vcp_pivot and price > 0 else None

    pivot_status = '—'; pivot_cls = 'n'
    if dist_vcp is not None:
        vol0 = volumes[0] if volumes else 0
        has_vol = bool(vol0 and v20 and vol0 > v20 * 1.3)
        if   0  < dist_vcp <= 3: pivot_status = '突破放量→等回踩' if has_vol else '突破→等縮量回踩'; pivot_cls = 'g' if has_vol else 'o'
        elif 3  < dist_vcp <= 8: pivot_status = '超買→不買等踩';   pivot_cls = 'o'
        elif dist_vcp > 8:       pivot_status = '過熱→不買';        pivot_cls = 'r'
        elif -2 <= dist_vcp < 0: pivot_status = '即將突破→待觀察'; pivot_cls = 'o'
        else:                    pivot_status = '整理中→不買';       pivot_cls = 'n'

    gain52 = (vcp_pivot - lo52) / lo52 * 100 if (lo52 > 0 and vcp_pivot and vcp_pivot > 0) else 0
    prior_run_l = '先前強勢' if gain52 >= 60 else f'+{gain52:.0f}%'
    if gain52 >= 60: score += 5

    price_1y = closes[252] if len(closes) >= 253 else None
    gain_1y  = round((price - price_1y) / price_1y * 100, 1) if price_1y and price_1y > 0 else None

    ma20_old   = sma(closes[5:], 20) if len(closes) >= 25 else None
    ma20_slope = round((ma20 - ma20_old) / ma20_old * 100, 2) if (ma20 and ma20_old and ma20_old > 0) else None
    dist_ma20  = round((price - ma20) / ma20 * 100, 1) if ma20 else None

    pb_buy = None
    if vcp_pivot and dist_vcp is not None and -8 <= dist_vcp < 3:
        early = [d.get('high') for d in data[11:min(31, len(data))]]
        early = [v for v in early if v is not None and math.isfinite(v)]
        ep = max(early) if early else None
        if ep and any(d.get('close') and d['close'] > ep for d in data[1:11]):
            tv = volumes[0] if volumes else None
            tr = round(tv / v20, 2) if (tv and v20 and v20 > 0) else None
            if tr is not None and tr < 0.5:
                pb_buy = {'volRatio': tr, 'isFloor': tr < 0.3, 'dist': dist_vcp, 'strong': tr < 0.3 and dist_vcp >= -7}

    return {
        'score':    max(0, round(score)),
        'price':    round(float(price), 2),
        'ma50':     round(float(ma50),  2) if ma50  else None,
        'ma150':    round(float(ma150), 2) if ma150 else None,
        'ma200':    round(float(ma200), 2) if ma200 else None,
        'h52':      round(float(h52),   2),
        'pctH':     round(float(pct_h), 1),
        'abM50':    bool(ab50), 'abM150': bool(ab150), 'abM200': bool(ab200),
        'stage2':   bool(ab50 and ab150 and ab200 and perf),
        'perfOrd':  bool(perf),
        'atrL': atr_l, 'atrS': atr_s,
        'atrRat':   round(float(atr_rat), 3) if atr_rat else None,
        'volL': vol_l, 'volS': vol_s,
        'vRat':     round(float(v_rat), 2) if v_rat else None,
        'v10':      round(float(v10)) if v10 else None,
        'rsi': rsi, 'contractions': contractions,
        'gainFrom52Low': round(float(gain52), 1),
        'gain1Y': gain_1y, 'priorRunL': prior_run_l,
        'ma5':  round(float(ma5),  2) if ma5  else None,
        'ma10': round(float(ma10), 2) if ma10 else None,
        'ma20': round(float(ma20), 2) if ma20 else None,
        'ma20Slope': ma20_slope, 'distMA20': dist_ma20,
        'maFlatL': ma_flat_l, 'maFlatS': ma_flat_s,
        'leftMountain': left_mountain, 'fin': fin,
        'closeRatio': close_ratio,
        'pp': pp, 'ppR1': r1, 'ppR2': r2, 'ppS1': s1, 'ppS2': s2,
        'vcpPivot': vcp_pivot, 'distVcpPivot': dist_vcp,
        'pivotStatus': pivot_status, 'pivotStatusCls': pivot_cls,
        'pbBuy': pb_buy,
    }

# ── Fundamental score (1:1 port of VCPfinder calcFundScore) ─────────────────

def calc_fund_score(fund):
    if not fund:
        return None
    pts = tot = 0
    roe = fund.get('roe')
    gross = fund.get('grossMargin')
    op    = fund.get('operatingMargin')
    eg    = fund.get('earningsGrowth')
    rg    = fund.get('revenueGrowth')

    if roe is not None:
        tot += 30
        pts += 30 if roe >= 0.15 else (18 if roe >= 0.08 else (8 if roe >= 0 else 0))
    if gross is not None:
        tot += 15
        pts += 15 if gross >= 0.3 else (8 if gross >= 0.15 else (4 if gross > 0 else 0))
    if op is not None:
        tot += 15
        pts += 15 if op >= 0.15 else (8 if op >= 0.08 else (4 if op > 0 else 0))
    if eg is not None and not (isinstance(eg, float) and math.isnan(eg)):
        tot += 20
        pts += 20 if eg >= 0.15 else (10 if eg >= 0 else 0)
    if rg is not None:
        tot += 15
        pts += 15 if rg >= 0.1 else (7 if rg >= 0 else 0)

    return round(pts / tot * 100) if tot > 0 else None

# ── Yahoo Finance fundamental fetch ─────────────────────────────────────────

def _fetch_one(sym):
    try:
        import yfinance as yf
        info = yf.Ticker(sym).info
        return sym, {
            'roe':             info.get('returnOnEquity'),
            'grossMargin':     info.get('grossMargins'),
            'operatingMargin': info.get('operatingMargins'),
            'earningsGrowth':  info.get('earningsGrowth'),
            'revenueGrowth':   info.get('revenueGrowth'),
        }
    except Exception:
        return sym, None

def fetch_fund_data(symbols, max_workers=8):
    results = {}
    total = len(symbols)
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_one, sym): sym for sym in symbols}
        for future in as_completed(futures):
            sym, data = future.result()
            results[sym] = data
            done += 1
            if done % 50 == 0 or done == total:
                sys.stdout.write(f'\r  fund fetch: {done}/{total}   ')
                sys.stdout.flush()
    print()
    return results

# ── Load JSON → data array ────────────────────────────────────────────────────

def _yahoo_batch_quotes(codes, suffix):
    """Fetch today's OHLCV via yfinance in batches of 200. Returns {code: {c,h,l,o,v}}."""
    import yfinance as yf
    result = {}
    for i in range(0, len(codes), 200):
        batch   = codes[i:i+200]
        tickers = [f'{c}{suffix}' for c in batch]
        try:
            df = yf.download(tickers, period='1d', interval='1d',
                             progress=False, auto_adjust=True, timeout=30)
            if df.empty:
                continue
            # yf.download with multiple tickers returns MultiIndex columns
            closes  = df['Close']  if 'Close'  in df.columns else df.get('Close',  None)
            opens   = df['Open']   if 'Open'   in df.columns else df.get('Open',   None)
            highs   = df['High']   if 'High'   in df.columns else df.get('High',   None)
            lows    = df['Low']    if 'Low'    in df.columns else df.get('Low',    None)
            volumes = df['Volume'] if 'Volume' in df.columns else df.get('Volume', None)
            for sym in tickers:
                code = sym.replace(suffix, '')
                try:
                    c = float(closes[sym].dropna().iloc[-1])  if closes  is not None else None
                    o = float(opens[sym].dropna().iloc[-1])   if opens   is not None else c
                    h = float(highs[sym].dropna().iloc[-1])   if highs   is not None else c
                    l = float(lows[sym].dropna().iloc[-1])    if lows    is not None else c
                    v = int(volumes[sym].dropna().iloc[-1])   if volumes is not None else 0
                    if c:
                        result[code] = {'c': round(c,2), 'o': round(o or c,2),
                                        'h': round(h or c,2), 'l': round(l or c,2), 'v': v}
                except Exception:
                    pass
        except Exception as e:
            print(f'  [yf batch {i//200}] {e}')
    return result


def refresh_today_prices(tse_path, otc_path):
    """If price data is from a previous day and it's after 09:00 TW, update latest candle from Yahoo Finance."""
    tz_tw   = datetime.timezone(datetime.timedelta(hours=8))
    now_tw  = datetime.datetime.now(tz_tw)
    today   = now_tw.strftime('%Y-%m-%d')
    t       = now_tw.hour * 60 + now_tw.minute

    if now_tw.weekday() >= 5 or t < 9 * 60:
        return  # weekend or pre-market

    for path, suffix in [(tse_path, '.TW'), (otc_path, '.TWO')]:
        if not path.exists():
            continue
        with open(path, encoding='utf-8') as f:
            data = json.load(f)

        if data.get('_price_date', '') >= today:
            print(f'  {path.name}: already today ({today}), skip refresh')
            continue

        stocks = data.get('stocks', {})
        codes  = list(stocks.keys())
        print(f'  {path.name}: stale ({data.get("_price_date","")}), fetching {len(codes)} from Yahoo...')

        prices = _yahoo_batch_quotes(codes, suffix)
        if len(prices) < len(codes) * 0.4:
            print(f'  [warn] only {len(prices)}/{len(codes)} returned, keeping stale data')
            continue

        updated = 0
        for code, entry in stocks.items():
            p = prices.get(code)
            if not p:
                continue
            cur_d = entry.get('_d', '')
            if cur_d == today:
                if entry.get('c'): entry['c'][0] = p['c']
                if entry.get('h'): entry['h'][0] = max(entry['h'][0], p['h'])
                if entry.get('l'): entry['l'][0] = min(entry['l'][0], p['l'])
                if entry.get('v'): entry['v'][0] = p['v']
            else:
                entry['c'] = [p['c']] + entry.get('c', [])[:251]
                entry['h'] = [p['h']] + entry.get('h', [])[:251]
                entry['l'] = [p['l']] + entry.get('l', [])[:251]
                entry['v'] = [p['v']] + entry.get('v', [])[:251]
                entry['_d'] = today
            updated += 1

        data['_price_date'] = today
        tmp = path.with_suffix('.tmp')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        tmp.replace(path)
        print(f'  {path.name}: refreshed {updated} stocks → _price_date={today}')


def load_stocks(path):
    with open(path, encoding='utf-8') as f:
        d = json.load(f)
    return d.get('stocks', {}), d.get('_price_date', '')

def to_data(entry):
    c = entry.get('c', [])
    h = entry.get('h', [])
    l = entry.get('l', [])
    v = entry.get('v', [])
    return [
        {
            'close':  c[i],
            'high':   h[i] if i < len(h) else c[i],
            'low':    l[i] if i < len(l) else c[i],
            'volume': v[i] if i < len(v) else 0,
        }
        for i in range(len(c))
    ]

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    tz_tw  = datetime.timezone(datetime.timedelta(hours=8))
    now_tw = datetime.datetime.now(tz_tw)
    print('=== VCP Scanner (VCPfinder-compatible algorithm) ===')
    print(f'Start: {now_tw.strftime("%Y-%m-%d %H:%M")} (Taipei)')

    base     = Path(__file__).parent.parent
    tse_path = base / 'stocks_tse.json'
    otc_path = base / 'stocks_otc.json'
    daily_path = base / 'twse_daily.json'

    if not tse_path.exists():
        print('ERROR: stocks_tse.json not found'); sys.exit(1)

    refresh_today_prices(tse_path, otc_path)

    tse_stocks, price_date = load_stocks(tse_path)
    otc_stocks, _          = load_stocks(otc_path) if otc_path.exists() else ({}, '')
    sector_map = {}
    if daily_path.exists():
        with open(daily_path, encoding='utf-8') as f:
            sector_map = json.load(f).get('sectors', {}) or {}
    print(f'Data: TSE={len(tse_stocks)}, OTC={len(otc_stocks)}, price_date={price_date}')

    all_stocks = {}
    for code, entry in tse_stocks.items():
        all_stocks[code] = {
            **entry,
            'ex': 'TW',
            'sector': sector_map.get(code, entry.get('sector', '上市')),
        }
    for code, entry in otc_stocks.items():
        if code not in all_stocks:
            all_stocks[code] = {
                **entry,
                'ex': 'TWO',
                'sector': sector_map.get(code, entry.get('sector', '上櫃')),
            }

    results = []
    failed = 0
    total  = len(all_stocks)

    for i, (code, entry) in enumerate(all_stocks.items()):
        data = to_data(entry)
        vcp  = calc_vcp(data)
        if vcp is None:
            failed += 1
        elif vcp['score'] >= MIN_SCORE_SAVE:
            results.append({
                'code':   code,
                'name':   entry.get('n', code),
                'sector': entry.get('sector', ''),
                'ex':     entry.get('ex', 'TW'),
                'sym':    f"{code}.{entry.get('ex', 'TW')}",
                'vcp':    vcp,
            })
        if (i + 1) % 500 == 0 or (i + 1) == total:
            pct = (i + 1) / total * 100
            sys.stdout.write(f'\r  {i+1}/{total} ({pct:.0f}%) | VCP>={MIN_SCORE_SAVE}: {len(results)} | no data: {failed}   ')
            sys.stdout.flush()

    print(f'\nDone: scanned={total-failed}, candidates={len(results)}, failed={failed}')
    results.sort(key=lambda r: r['vcp']['score'], reverse=True)

    # ── Step 2: fetch Yahoo Finance fundamentals + apply fundScore >= 80 ──────
    FUND_MIN = 80
    print(f'\nFetching fundamentals for {len(results)} candidates (Yahoo Finance)...')
    syms = [r['sym'] for r in results]
    fund_map = fetch_fund_data(syms, max_workers=8)

    filtered = []
    fund_pass = fund_null = fund_fail = 0
    for r in results:
        fd   = fund_map.get(r['sym'])
        fs   = calc_fund_score(fd)
        r['fund']      = fd
        r['fundScore'] = fs
        if fs is None:
            fund_null += 1       # no Yahoo data → filtered out (matches VCPfinder)
        elif fs < FUND_MIN:
            fund_fail += 1
        else:
            fund_pass += 1
            filtered.append(r)

    print(f'Fund filter (>={FUND_MIN}): pass={fund_pass}, fail={fund_fail}, no_data={fund_null}')

    scan_date = price_date or now_tw.strftime('%Y-%m-%d')
    output = {
        'scanDate':      scan_date,
        'scanTime':      now_tw.strftime('%H:%M'),
        'totalScanned':  total - failed,
        'totalStocks':   total,
        'vcpCandidates': len(filtered),
        'stocks':        filtered,
    }

    out_path = base / 'vcp_daily.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    print(f'Saved: {out_path}  ({len(filtered)} records after fund filter)')

if __name__ == '__main__':
    main()
