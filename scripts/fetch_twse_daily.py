#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
Fetch daily TWSE/TPEX data and write twse_daily.json.
Runs via GitHub Actions after market close.

Data collected:
  - sectors  : {code -> industry} for all TSE+OTC stocks
  - chips    : {code -> foreignNet/trustNet/dealerNet} TSE+OTC
  - bwibbu   : {code -> pe/pb/divYield} TSE+OTC
  - monthRevenue : {code -> yoy/current} TSE+OTC
  - income   : {code -> eps/revenue/operatingIncome/netIncome/year/quarter} TSE+OTC
"""

import json, sys, time, datetime, re
import requests

SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})

def get(url, timeout=30):
    try:
        r = SESSION.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'  [WARN] GET failed {url[:80]}: {e}')
        return None

# ── Industry code → name table (TWSE/TPEX 產業別代碼) ──────────────────────────
IND_CODE = {
    '01':'水泥','02':'食品','03':'塑膠','04':'紡織','05':'電機',
    '06':'電器電纜','07':'化學生技醫療','08':'玻璃陶瓷','09':'造紙','10':'鋼鐵',
    '11':'橡膠','12':'汽車','13':'電子','14':'建材','15':'航運',
    '16':'觀光','17':'金融','18':'貿易百貨','19':'綜合','20':'其他',
    '21':'化學','22':'生技醫療','23':'油電燃氣','24':'半導體','25':'電腦周邊',
    '26':'光電','27':'通信網路','28':'電子零組件','29':'電子通路','30':'資訊服務',
    '31':'其他電子','32':'文化創意','33':'農業科技','34':'電子商務','35':'綠能環保',
    '36':'數位雲端','37':'運動休閒','38':'居家生活',
}

# ── 1. Sectors ────────────────────────────────────────────────────────────────
def fetch_sectors():
    sectors = {}

    # t187ap03_L includes BOTH TSE and OTC companies with 產業別 (numeric code)
    data = get('https://openapi.twse.com.tw/v1/opendata/t187ap03_L')
    if data:
        for r in data:
            code = str(r.get('公司代號') or '').strip()
            ind_code = str(r.get('產業別') or '').strip()
            if code and ind_code:
                sectors[code] = IND_CODE.get(ind_code, ind_code)
        print(f'  sectors from t187ap03_L: {len(sectors)}')

    # Fallback: BWIBBU_d for any remaining TSE codes
    if len(sectors) < 100:
        data2 = get('https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d')
        if data2:
            for r in data2:
                code = str(r.get('Code') or '').strip()
                ind  = str(r.get('產業別') or r.get('IndustryType') or '').strip()
                if code and ind and code not in sectors:
                    sectors[code] = re.sub(r'業$', '', ind)
            print(f'  sectors after BWIBBU_d fallback: {len(sectors)}')

    print(f'  Total sectors: {len(sectors)}')
    return sectors

# ── 1b. OTC stock list (code + name) for browser use ─────────────────────────
def fetch_otc_stocks():
    """Returns [{code, name}] for all mainboard OTC stocks."""
    otc = []
    data = get('https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes')
    if data:
        for r in data:
            code = str(r.get('SecuritiesCompanyCode') or '').strip()
            name = str(r.get('CompanyName') or code).strip()
            if re.match(r'^\d{4}$', code):
                otc.append({'code': code, 'name': name})
        print(f'  OTC stocks: {len(otc)}')
    else:
        print('  OTC stocks: API failed')
    return otc

# ── 2. BWIBBU (PE / PB / DividendYield) ──────────────────────────────────────
def fetch_bwibbu():
    bwibbu = {}

    # TSE
    data = get('https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_d')
    if data:
        for r in data:
            code = str(r.get('Code') or r.get('證券代號') or r.get('股票代號') or '').strip()
            pe   = _flt(r.get('PEratio') or r.get('本益比'))
            pb   = _flt(r.get('PBratio') or r.get('股價淨值比'))
            dy   = _flt(r.get('DividendYield') or r.get('殖利率'))
            if code and any(v is not None for v in [pe, pb, dy]):
                bwibbu[code] = {'pe': pe, 'pb': pb, 'divYield': dy}
        print(f'  TSE BWIBBU: {len(bwibbu)}')

    # OTC: tpex_mainboard_daily_close_quotes (no PE/PB fields, skip BWIBBU for OTC)
    # Note: tpex_mainboard_perday_quotation returns HTML, not JSON
    print('  OTC BWIBBU: skipped (TPEX PE/PB API unavailable)')

    print(f'  Total BWIBBU: {len(bwibbu)}')
    return bwibbu

# ── 3. Chips (institutional net buy/sell) ─────────────────────────────────────
def fetch_chips():
    chips = {}
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))

    # TSE T86 – try last 5 trading days
    for delta in range(7):
        dt = today - datetime.timedelta(days=delta)
        if dt.weekday() >= 5:
            continue
        yyyymmdd = dt.strftime('%Y%m%d')
        url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={yyyymmdd}&response=json'
        data = get(url)
        if not data or data.get('stat') not in ('OK', '成功'):
            continue
        fields = data.get('fields', [])
        rows   = data.get('data', [])
        try:
            ci = fields.index('證券代號')
            fi = fields.index('外陸資買賣超股數(不含外資自營商)')
            ti = fields.index('投信買賣超股數')
            di = fields.index('自營商買賣超股數(合計)')
        except ValueError:
            # fallback to positional
            ci, fi, ti, di = 0, 4, 10, 13
        for row in rows:
            if len(row) <= max(ci, fi, ti, di):
                continue
            code = str(row[ci]).strip()
            if not re.match(r'^\d{4,6}$', code):
                continue
            chips[code] = {
                'foreignNet': _int(row[fi]),
                'trustNet':   _int(row[ti]),
                'dealerNet':  _int(row[di]),
            }
        print(f'  TSE chips ({yyyymmdd}): {len(chips)}')
        break

    # OTC chips – TPEX equivalent
    for delta in range(7):
        dt = today - datetime.timedelta(days=delta)
        if dt.weekday() >= 5:
            continue
        yyyymmdd = dt.strftime('%Y%m%d')
        url = f'https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={dt.strftime("%Y/%m/%d")}&s=0,asc'
        data = get(url)
        if not data:
            continue
        rows = data.get('aaData', [])
        added = 0
        for row in rows:
            if len(row) < 6:
                continue
            code = str(row[0]).strip()
            if not re.match(r'^\d{4,6}$', code):
                continue
            if code not in chips:
                chips[code] = {
                    'foreignNet': _int(row[2]) if len(row) > 2 else 0,
                    'trustNet':   _int(row[4]) if len(row) > 4 else 0,
                    'dealerNet':  _int(row[6]) if len(row) > 6 else 0,
                }
                added += 1
        if added > 0:
            print(f'  OTC chips ({yyyymmdd}): +{added}')
            break

    print(f'  Total chips: {len(chips)}')
    return chips

# ── 4. Monthly Revenue ────────────────────────────────────────────────────────
def fetch_month_revenue():
    rev = {}
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    # Revenue is reported by the 10th of the following month
    # Try current and previous month
    for mo_delta in range(3):
        dt = today.replace(day=1) - datetime.timedelta(days=mo_delta * 28)
        year_tw = dt.year - 1911
        month   = dt.month
        # TSE monthly revenue
        url = f'https://mops.twse.com.tw/nas/t21/sii/t21sc03_{year_tw}_{month}_0.html'
        # We use the JSON API instead:
        url_json = f'https://openapi.twse.com.tw/v1/opendata/t187ap05_L?yearmonth={year_tw}{month:02d}'
        data = get(url_json)
        if data and isinstance(data, list) and len(data) > 50:
            added = 0
            for r in data:
                code   = str(r.get('公司代號') or r.get('股票代號') or '').strip()
                cur    = _flt(r.get('當月營收') or r.get('revenue'))
                yoy    = _flt(r.get('上年同月增減(%)') or r.get('yoy'))
                if code and cur is not None:
                    rev[code] = {'current': int(cur) if cur else 0, 'yoy': round(yoy, 2) if yoy is not None else None}
                    added += 1
            print(f'  TSE revenue ({year_tw}/{month:02d}): {added}')
            if added > 100:
                break

    # OTC monthly revenue
    for mo_delta in range(3):
        dt = today.replace(day=1) - datetime.timedelta(days=mo_delta * 28)
        year_tw = dt.year - 1911
        month   = dt.month
        url_json = f'https://openapi.twse.com.tw/v1/opendata/t187ap05_L?yearmonth={year_tw}{month:02d}&market=OTC'
        data = get(url_json)
        if not data:
            # try TPEX
            url2 = f'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_L?yearmonth={year_tw}{month:02d}'
            data = get(url2)
        if data and isinstance(data, list) and len(data) > 20:
            added = 0
            for r in data:
                code = str(r.get('公司代號') or r.get('股票代號') or r.get('SecuritiesCompanyCode') or '').strip()
                cur  = _flt(r.get('當月營收') or r.get('revenue'))
                yoy  = _flt(r.get('上年同月增減(%)') or r.get('yoy'))
                if code and cur is not None and code not in rev:
                    rev[code] = {'current': int(cur) if cur else 0, 'yoy': round(yoy, 2) if yoy is not None else None}
                    added += 1
            print(f'  OTC revenue ({year_tw}/{month:02d}): +{added}')
            if added > 20:
                break

    print(f'  Total revenue: {len(rev)}')
    return rev

# ── 5. Income (Quarterly) ─────────────────────────────────────────────────────
def fetch_income():
    income = {}
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    year_tw = today.year - 1911
    # Latest quarter: Q4 is published ~March, Q1 ~May, Q2 ~Aug, Q3 ~Nov
    quarter_map = {1:4, 2:4, 3:1, 4:1, 5:1, 6:2, 7:2, 8:2, 9:3, 10:3, 11:3, 12:4}
    q = quarter_map[today.month]
    yr = year_tw if not (today.month <= 2 and q == 4) else year_tw - 1

    for (y, qq) in [(yr, q), (yr - 1 if q == 1 else yr, q - 1 if q > 1 else 4)]:
        # TSE income
        url = f'https://openapi.twse.com.tw/v1/opendata/t187ap06_L?yearquarter={y}Q{qq}'
        data = get(url)
        if not data:
            url2 = f'https://openapi.twse.com.tw/v1/opendata/t187ap06_L?year={y}&quarter={qq}'
            data = get(url2)
        if data and isinstance(data, list) and len(data) > 50:
            added = 0
            for r in data:
                code = str(r.get('公司代號') or r.get('股票代號') or '').strip()
                eps  = _flt(r.get('基本每股盈餘（元）') or r.get('EPS') or r.get('eps'))
                rev  = _int2(r.get('營業收入') or r.get('revenue'))
                oi   = _int2(r.get('營業利益（損失）') or r.get('operatingIncome'))
                ni   = _int2(r.get('本期淨利（淨損）') or r.get('netIncome'))
                if code and any(v is not None for v in [eps, rev]):
                    income[code] = {'eps': eps, 'revenue': rev, 'operatingIncome': oi,
                                    'netIncome': ni, 'year': str(y), 'quarter': str(qq)}
                    added += 1
            print(f'  TSE income {y}Q{qq}: {added}')
            if added > 100:
                break

    # OTC income
    for (y, qq) in [(yr, q), (yr - 1 if q == 1 else yr, q - 1 if q > 1 else 4)]:
        url = f'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_L?yearquarter={y}Q{qq}'
        data = get(url)
        if data and isinstance(data, list) and len(data) > 20:
            added = 0
            for r in data:
                code = str(r.get('公司代號') or r.get('SecuritiesCompanyCode') or '').strip()
                eps  = _flt(r.get('基本每股盈餘（元）') or r.get('EPS'))
                rev  = _int2(r.get('營業收入') or r.get('revenue'))
                oi   = _int2(r.get('營業利益（損失）') or r.get('operatingIncome'))
                ni   = _int2(r.get('本期淨利（淨損）') or r.get('netIncome'))
                if code and any(v is not None for v in [eps, rev]) and code not in income:
                    income[code] = {'eps': eps, 'revenue': rev, 'operatingIncome': oi,
                                    'netIncome': ni, 'year': str(y), 'quarter': str(qq)}
                    added += 1
            print(f'  OTC income {y}Q{qq}: +{added}')
            if added > 20:
                break

    print(f'  Total income: {len(income)}')
    return income

# ── Helpers ───────────────────────────────────────────────────────────────────
def _flt(v):
    if v is None: return None
    try:
        f = float(str(v).replace(',', ''))
        return f if f != 0 else None
    except: return None

def _int(v):
    if v is None: return 0
    try: return int(str(v).replace(',', ''))
    except: return 0

def _int2(v):
    if v is None: return None
    try:
        i = int(str(v).replace(',', ''))
        return i if i != 0 else None
    except: return None

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    now_tw = datetime.datetime.now(tz_tw)
    date_str = now_tw.strftime('%Y%m%d')
    print(f'=== fetch_twse_daily.py  {now_tw.strftime("%Y-%m-%d %H:%M")} (Taipei) ===')

    print('\n1. Sectors...')
    sectors = fetch_sectors()

    print('\n1b. OTC stock list...')
    otc_stocks = fetch_otc_stocks()

    print('\n2. BWIBBU (PE/PB/DY)...')
    bwibbu = fetch_bwibbu()

    print('\n3. Chips (T86)...')
    chips = fetch_chips()

    print('\n4. Monthly Revenue...')
    month_revenue = fetch_month_revenue()

    print('\n5. Income (quarterly)...')
    income = fetch_income()

    output = {
        'date':         date_str,
        'sectors':      sectors,
        'otcStocks':    otc_stocks,
        'chips':        chips,
        'bwibbu':       bwibbu,
        'monthRevenue': month_revenue,
        'income':       income,
    }

    with open('twse_daily.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    print(f'\n✅ twse_daily.json written:')
    print(f'   sectors={len(sectors)}, otcStocks={len(otc_stocks)}, chips={len(chips)}, bwibbu={len(bwibbu)}')
    print(f'   revenue={len(month_revenue)}, income={len(income)}')

if __name__ == '__main__':
    main()
