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

import json, sys, time, datetime, re, xml.etree.ElementTree as ET
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

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
def fetch_tse_stocks():
    """Returns [{code, name}] for all TSE stocks with Chinese names."""
    tse = []
    data = get('https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL')
    if data:
        for r in data:
            code = str(r.get('Code') or r.get('code') or '').strip()
            name = str(r.get('Name') or r.get('name') or code).strip()
            if re.match(r'^\d{4}$', code):
                tse.append({'code': code, 'name': name})
        print(f'  TSE stocks: {len(tse)}')
    else:
        print('  TSE stocks: API failed')
    return tse

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

    # OTC PE/PB: TPEX openapi 目前無可靠的 PE/PB 欄位，跳過以節省時間
    print(f'  OTC BWIBBU: skipped (TPEX API has no PE/PB fields)')

    print(f'  Total BWIBBU: {len(bwibbu)}')
    return bwibbu

# ── 3. Chips (institutional net buy/sell) ─────────────────────────────────────
def fetch_chips():
    chips = {}
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))

    # TSE T86 – try last 5 trading days
    for delta in range(15):
        dt = today - datetime.timedelta(days=delta)
        if dt.weekday() >= 5:
            continue
        yyyymmdd = dt.strftime('%Y%m%d')
        url = f'https://www.twse.com.tw/rwd/zh/fund/T86?date={yyyymmdd}&selectType=ALL&response=json'
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
        if len(chips) > 100:
            break
        else:
            chips = {}  # 資料不足，嘗試前一交易日

    # OTC chips – TPEX equivalent
    for delta in range(15):
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
        if added > 50:
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
                cur    = _flt(r.get('營業收入-當月營收') or r.get('當月營收') or r.get('revenue'))
                yoy    = _flt(r.get('營業收入-去年同月增減(%)') or r.get('上年同月增減(%)') or r.get('yoy'))
                if code and cur is not None:
                    rev[code] = {'current': int(cur) if cur else 0, 'yoy': round(yoy, 2) if yoy is not None else None}
                    added += 1
            print(f'  TSE revenue ({year_tw}/{month:02d}): {added}')
            if added > 100:
                break

    # OTC monthly revenue: TPEX openapi 無法使用（返回 HTML），TWSE t187ap05_L ?market=OTC 參數無效
    # t187ap05_L 無參數版本已包含少數高市值上櫃公司，其餘上櫃股票基本面由前端 Yahoo Finance 補抓
    print(f'  OTC revenue: skipped (TPEX API unavailable)')

    print(f'  Total revenue: {len(rev)}')
    return rev

# ── 5. Income (Quarterly) ─────────────────────────────────────────────────────
def parse_income_rows(data):
    result = {}
    if not data or not isinstance(data, list):
        return result
    for r in data:
        code = str(r.get('公司代號') or '').strip()
        if not code or not re.match(r'^\d{4,6}$', code):
            continue
        eps = _flt(r.get('基本每股盈餘(元)'))
        rev = _int2(r.get('營業收入'))
        oi  = _int2(r.get('營業利益'))
        ni  = _int2(r.get('稅後淨利'))
        result[code] = {
            'eps': eps, 'revenue': rev,
            'operatingIncome': oi, 'netIncome': ni,
            'year': str(r.get('年度','')),
            'quarter': str(r.get('季別',''))
        }
    return result

def fetch_income():
    income = {}
    # 抓當季資料
    url = 'https://openapi.twse.com.tw/v1/opendata/t187ap14_L'
    income = parse_income_rows(get(url))
    print(f'  TSE income current: {len(income)}')

    # 抓前一年同季資料（計算 YoY EPS 成長）
    if income:
        sample = next(iter(income.values()))
        cur_year = int(sample.get('year', 0) or 0)
        cur_qtr  = sample.get('quarter', '')
        prev_year = cur_year - 1
        if prev_year > 100 and cur_qtr:
            # TWSE 有提供歷史季報的另一個端點格式
            prev_url = f'https://openapi.twse.com.tw/v1/opendata/t187ap14_L?year={prev_year}&season={cur_qtr}'
            prev_data = get(prev_url)
            if not prev_data:
                # 嘗試 MOPS API（公開資訊觀測站）
                tw_year = prev_year  # 民國年
                season_map = {'1':'Q1','2':'Q2','3':'Q3','4':'Q4'}
                mops_url = f'https://mops.twse.com.tw/mops/web/ajax_t05st09?encodeURIComponent=1&step=1&firstin=1&off=1&keyword4=&code1=&TYPEK=sii&co_id=&year={tw_year}&season={cur_qtr}'
                prev_data = get(mops_url)
            prev_income = parse_income_rows(prev_data) if prev_data else {}
            # 驗證 prev_income 確實是去年資料（API 可能忽略 year 參數回傳今年資料）
            if prev_income:
                first_prev = next(iter(prev_income.values()))
                if first_prev.get('year') == str(cur_year):
                    print(f'  ⚠️ API 回傳當年度資料（{cur_year}），非去年（{prev_year}），跳過 YoY 計算')
                    prev_income = {}
            # 計算 YoY EPS 成長率
            yoy_count = 0
            for code, cur in income.items():
                prev = prev_income.get(code)
                if prev and prev.get('eps') and cur.get('eps'):
                    p_eps, c_eps = prev['eps'], cur['eps']
                    if p_eps != 0:
                        cur['earningsGrowth'] = round((c_eps - p_eps) / abs(p_eps), 4)
                        yoy_count += 1
            print(f'  prev year income ({prev_year}Q{cur_qtr}): {len(prev_income)}, YoY computed: {yoy_count}')
    else:
        print('  ⚠️ t187ap14_L failed or empty')

    print(f'  Total income: {len(income)}')
    return income


# ── 6. Financial News (Google News RSS) ──────────────────────────────────────
def fetch_margin():
    """融資融券餘額 MI_MARGN → {code: {today, prev, change}}"""
    margin = {}
    data = get('https://openapi.twse.com.tw/v1/marginTrading/MI_MARGN')
    if data:
        for row in data:
            code = str(row.get('股票代號') or '').strip()
            if not code:
                continue
            def parse_num(v):
                try:
                    return int(str(v or '').replace(',', ''))
                except:
                    return 0
            today = parse_num(row.get('融資今日餘額'))
            prev  = parse_num(row.get('融資前日餘額'))
            margin[code] = {'today': today, 'prev': prev, 'change': today - prev}
        print(f'  margin: {len(margin)} stocks from MI_MARGN')
    else:
        print('  [WARN] MI_MARGN fetch failed')
    return margin


def fetch_news():
    # Google News RSS 查詢（宏觀事件，不抓個股漲跌）
    queries = [
        'Israel Iran war ceasefire attack strike military',
        'Trump Iran Israel diplomacy sanctions statement',
        'Strait of Hormuz oil blockade tanker',
        'Trump tariff trade war sanctions policy',
        'Federal Reserve interest rate inflation CPI decision',
        'US China trade technology export ban',
        'Taiwan economy geopolitics supply chain',
        'oil price energy OPEC war impact',
    ]
    # 直接 RSS 來源（伺服器端無 CORS 問題）
    direct_feeds = [
        'https://feeds.bbci.co.uk/news/world/rss.xml',
        'https://feeds.bbci.co.uk/news/business/rss.xml',
        'https://feeds.apnews.com/rss/apf-topnews',
        'https://feeds.apnews.com/rss/apf-business',
    ]
    all_items = []
    seen = set()

    def parse_feed(content, source_name=''):
        items = []
        try:
            root = ET.fromstring(content)
            for item in root.findall('.//item')[:10]:
                title = (item.findtext('title') or '').strip()
                link  = (item.findtext('link') or '').strip()
                pub   = (item.findtext('pubDate') or '').strip()
                src_el = item.find('{https://news.google.com/rss}source') or item.find('source')
                src = (src_el.text if src_el is not None else '') or source_name
                if title and title not in seen:
                    seen.add(title)
                    items.append({'title': title, 'link': link, 'pubDate': pub, 'source': src})
        except Exception as e:
            print(f'  [WARN] parse failed: {e}')
        return items

    # 直接 RSS
    for feed_url in direct_feeds:
        try:
            r = SESSION.get(feed_url, timeout=20, headers={'Accept': 'application/rss+xml,application/xml,text/xml'})
            r.raise_for_status()
            name = 'BBC' if 'bbc' in feed_url else 'AP'
            all_items.extend(parse_feed(r.content, name))
            print(f'  {name} RSS: +{len(all_items)} items')
        except Exception as e:
            print(f'  [WARN] direct RSS failed {feed_url[:50]}: {e}')

    # Google News 查詢
    for q in queries:
        url = f'https://news.google.com/rss/search?q={requests.utils.quote(q)}&hl=en-US&gl=US&ceid=US:en'
        try:
            r = SESSION.get(url, timeout=20, headers={'Accept': 'application/rss+xml,application/xml,text/xml'})
            r.raise_for_status()
            new_items = parse_feed(r.content)
            all_items.extend(new_items)
        except Exception as e:
            print(f'  [WARN] news RSS failed "{q[:35]}": {e}')

    print(f'  Total news items: {len(all_items)}')
    return all_items

# ── 7. Stock Price OHLCV（TWSE/TPEX 官方 API 批次抓取，取代 Yahoo Finance）──────
def fetch_twse_prices_today():
    """TWSE 官方 API 一次取得所有上市股票今日 OHLCV（不需逐股請求，無 rate-limit 風險）"""
    data = get('https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL')
    result = {}
    if not data or not isinstance(data, list):
        print('  [WARN] TWSE STOCK_DAY_ALL 取得失敗')
        return result
    for r in data:
        code = str(r.get('Code') or r.get('code') or '').strip()
        if not re.match(r'^\d{4,6}$', code):
            continue
        c = _flt(r.get('ClosingPrice') or r.get('收盤價') or r.get('close'))
        if not c or c <= 0:
            continue
        h = _flt(r.get('HighestPrice') or r.get('最高價') or r.get('high')) or c
        l = _flt(r.get('LowestPrice')  or r.get('最低價') or r.get('low'))  or c
        o = _flt(r.get('OpeningPrice') or r.get('開盤價') or r.get('open')) or c
        try:
            raw_vol = str(r.get('TradeVolume') or r.get('成交股數') or r.get('volume') or '0').replace(',', '')
            v = int(float(raw_vol))
        except Exception:
            v = 0
        result[code] = {'c': round(c, 2), 'h': round(h, 2), 'l': round(l, 2), 'o': round(o, 2), 'v': v}
    print(f'  TWSE 官方今日價格：{len(result)} 支')
    return result


def fetch_tpex_prices_today():
    """TPEX 官方 API 一次取得所有上櫃股票今日 OHLCV"""
    data = get('https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes')
    result = {}
    if not data or not isinstance(data, list):
        print('  [WARN] TPEX mainboard_daily_close_quotes 取得失敗')
        return result
    for r in data:
        code = str(r.get('SecuritiesCompanyCode') or '').strip()
        if not re.match(r'^\d{4}$', code):
            continue
        c = _flt(r.get('Close') or r.get('ClosingPrice') or r.get('收盤') or r.get('收盤價'))
        if not c or c <= 0:
            continue
        h = _flt(r.get('High') or r.get('HighestPrice') or r.get('最高')) or c
        l = _flt(r.get('Low')  or r.get('LowestPrice')  or r.get('最低')) or c
        o = _flt(r.get('Open') or r.get('OpeningPrice') or r.get('開盤')) or c
        try:
            raw_vol = str(r.get('TradingShares') or r.get('TradeVolume') or r.get('成交股數') or '0').replace(',', '')
            v = int(float(raw_vol))
        except Exception:
            v = 0
        result[code] = {'c': round(c, 2), 'h': round(h, 2), 'l': round(l, 2), 'o': round(o, 2), 'v': v}
    print(f'  TPEX 官方今日價格：{len(result)} 支')
    return result


def update_price_history(existing, today_prices, name_map):
    """
    增量更新歷史 OHLCV：
    - 同日多次執行 → 取代第一根（即時價格更新，不重複 prepend）
    - 新交易日 → prepend 新的一根，保留最多 252 根
    用 _d 欄位記錄最近一根的台灣日期，防止同日重複 append。
    """
    out = dict(existing)
    today_tw = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=8))
    ).strftime('%Y-%m-%d')
    new_day = intraday = new_stock = 0

    for code, today in today_prices.items():
        name = name_map.get(code, code)
        if code in out:
            hist = out[code]
            if hist.get('_d') == today_tw:
                # 同日：取代第一根（即時更新當日高低收量）
                hist['c'] = [today['c']] + (hist.get('c') or [])[1:]
                hist['h'] = [today['h']] + (hist.get('h') or [])[1:]
                hist['l'] = [today['l']] + (hist.get('l') or [])[1:]
                hist['v'] = [today['v']] + (hist.get('v') or [])[1:]
                intraday += 1
            else:
                # 新交易日：prepend
                hist['c'] = [today['c']] + (hist.get('c') or [])[:251]
                hist['h'] = [today['h']] + (hist.get('h') or [])[:251]
                hist['l'] = [today['l']] + (hist.get('l') or [])[:251]
                hist['v'] = [today['v']] + (hist.get('v') or [])[:251]
                hist['_d'] = today_tw
                new_day += 1
            if name and name != code:
                hist['n'] = name
        else:
            out[code] = {
                'n': name, '_d': today_tw,
                'c': [today['c']], 'h': [today['h']],
                'l': [today['l']], 'v': [today['v']],
            }
            new_stock += 1

    print(f'  price history: 新交易日={new_day}，即時更新={intraday}，新股={new_stock}，合計={len(out)}')
    return out

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
        i = int(float(str(v).replace(',', '')))  # handle "149804135.00" style
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
    tse_stocks = fetch_tse_stocks()
    otc_stocks = fetch_otc_stocks()

    print('\n2. BWIBBU (PE/PB/DY)...')
    bwibbu = fetch_bwibbu()

    print('\n3. Chips (T86)...')
    chips = fetch_chips()

    # 讀取舊資料，避免 API 暫時失敗時覆蓋掉好的歷史資料
    existing = {}
    try:
        with open('twse_daily.json', encoding='utf-8') as f:
            existing = json.load(f)
        print(f'  Loaded existing twse_daily.json (date={existing.get("date","")})')
    except Exception:
        pass

    print('\n4. Monthly Revenue...')
    if len(chips) < 100:
        old_chips = existing.get('chips', {})
        if len(old_chips) > len(chips):
            print(f'  ⚠️ 籌碼資料不足（{len(chips)}筆），保留舊資料（{len(old_chips)}筆）')
            chips = old_chips

    month_revenue = fetch_month_revenue()
    if len(month_revenue) < 100:
        old_rev = existing.get('monthRevenue', {})
        if len(old_rev) > len(month_revenue):
            print(f'  ⚠️ 新資料不足（{len(month_revenue)}），保留舊資料（{len(old_rev)}）')
            month_revenue = old_rev

    print('\n5. Income (quarterly)...')
    income = fetch_income()
    if len(income) < 100:
        old_inc = existing.get('income', {})
        if len(old_inc) > len(income):
            print(f'  ⚠️ 新資料不足（{len(income)}），保留舊資料（{len(old_inc)}）')
            income = old_inc

    # 歷史季報輪替：quarter 改變時，把上一期存進 incomePrev，累積 YoY EPS 對比
    existing_income = existing.get('income', {})
    existing_qtr = next(iter(existing_income.values()), {}).get('quarter')
    current_qtr  = next(iter(income.values()), {}).get('quarter')
    if existing_qtr and current_qtr and existing_qtr != current_qtr:
        income_prev = existing_income
        print(f'  季報更新：{existing_qtr} → {current_qtr}，舊資料存入 incomePrev')
    else:
        income_prev = existing.get('incomePrev', {})

    # 計算 YoY EPS 成長率（需同季對比）
    for code, cur in income.items():
        prev = income_prev.get(code)
        if prev and prev.get('quarter') == cur.get('quarter') and prev.get('eps') and cur.get('eps'):
            p_eps, c_eps = prev['eps'], cur['eps']
            if p_eps != 0:
                cur['earningsGrowth'] = round((c_eps - p_eps) / abs(p_eps), 4)
    yoy_count = sum(1 for v in income.values() if v.get('earningsGrowth') is not None)
    print(f'  YoY EPS growth computed: {yoy_count} stocks')

    print('\n6. Stock Prices (OHLCV)...')
    # 讀取舊的價格快取
    existing_tse_price, existing_otc_price = {}, {}
    try:
        with open('stocks_tse.json', encoding='utf-8') as f:
            existing_tse_price = json.load(f).get('stocks', {})
        print(f'  existing TSE price: {len(existing_tse_price)}')
    except Exception: pass
    try:
        with open('stocks_otc.json', encoding='utf-8') as f:
            existing_otc_price = json.load(f).get('stocks', {})
        print(f'  existing OTC price: {len(existing_otc_price)}')
    except Exception: pass

    name_map = {s['code']: s['name'] for s in tse_stocks + otc_stocks if s.get('name') and s['name'] != s['code']}

    # 用 TWSE/TPEX 官方 API 批次抓今日 OHLCV（取代 Yahoo Finance 個股逐一抓取）
    twse_today = fetch_twse_prices_today()
    tpex_today = fetch_tpex_prices_today()

    # 若官方 API 回傳資料不足（市場休假/API 故障），保留舊資料
    tse_prices = update_price_history(existing_tse_price, twse_today, name_map) if len(twse_today) > 100 else existing_tse_price
    otc_prices = update_price_history(existing_otc_price, tpex_today, name_map) if len(tpex_today) > 100 else existing_otc_price
    if len(twse_today) <= 100:
        print(f'  ⚠️ TWSE 今日資料不足（{len(twse_today)}），保留舊資料')
    if len(tpex_today) <= 100:
        print(f'  ⚠️ TPEX 今日資料不足（{len(tpex_today)}），保留舊資料')

    with open('stocks_tse.json', 'w', encoding='utf-8') as f:
        json.dump({'date': date_str, 'stocks': tse_prices}, f, ensure_ascii=False, separators=(',', ':'))
    with open('stocks_otc.json', 'w', encoding='utf-8') as f:
        json.dump({'date': date_str, 'stocks': otc_prices}, f, ensure_ascii=False, separators=(',', ':'))
    print(f'  stocks_tse.json: {len(tse_prices)} stocks')
    print(f'  stocks_otc.json: {len(otc_prices)} stocks')

    print('\n7. Margin Trading (MI_MARGN)...')
    margin = fetch_margin()

    print('\n8. Financial News (RSS)...')
    news = fetch_news()
    if len(news) < 3:
        old_news = existing.get('news', [])
        if len(old_news) > len(news):
            print(f'  ⚠️ 新聞抓取不足（{len(news)}），保留舊資料（{len(old_news)}筆）')
            news = old_news

    output = {
        'date':         date_str,
        'sectors':      sectors,
        'tseStocks':    tse_stocks or [{'code': code, 'name': info['n']} for code, info in tse_prices.items()],
        'otcStocks':    otc_stocks or [{'code': c, 'name': d.get('n', c)} for c, d in otc_prices.items()],
        'chips':        chips,
        'bwibbu':       bwibbu,
        'monthRevenue': month_revenue,
        'income':       income,
        'incomePrev':   income_prev,
        'margin':       margin,
        'news':         news,
    }

    with open('twse_daily.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    print(f'\n✅ twse_daily.json written:')
    print(f'   sectors={len(sectors)}, otcStocks={len(otc_stocks)}, chips={len(chips)}, bwibbu={len(bwibbu)}')
    print(f'   revenue={len(month_revenue)}, income={len(income)}, news={len(news)}')

if __name__ == '__main__':
    main()
