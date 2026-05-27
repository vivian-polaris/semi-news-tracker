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

import json, sys, os, time, datetime, re, xml.etree.ElementTree as ET
import requests

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
    'Referer': 'https://www.twse.com.tw/',
})

def get(url, timeout=30, retries=3):
    delays = [5, 15, 30]
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=timeout)
            if r.status_code == 429:
                wait = delays[min(attempt, len(delays)-1)]
                print(f'  [WARN] 429 rate-limited {url[:60]}, wait {wait}s (attempt {attempt+1}/{retries})')
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries - 1:
                wait = delays[attempt]
                print(f'  [WARN] GET failed (attempt {attempt+1}/{retries}) {url[:60]}: {e}, retry in {wait}s')
                time.sleep(wait)
            else:
                print(f'  [ERROR] GET gave up after {retries} attempts {url[:60]}: {e}')
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
        # 正確月份回溯（避免 28 天計算跨月誤差）
        mo = today.month - mo_delta
        yr = today.year
        while mo <= 0: mo += 12; yr -= 1
        dt = today.replace(year=yr, month=mo, day=1)
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

    # 注意：t187ap05_L 已同時包含上市（TSE）和上櫃（OTC）的月營收資料
    # 不需要額外抓取 OTC 端點

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
    # 注意：t187ap14_L 已同時包含上市（TSE）和上櫃（OTC）的季報資料
    url = 'https://openapi.twse.com.tw/v1/opendata/t187ap14_L'
    income = parse_income_rows(get(url))
    print(f'  Income (TSE+OTC): {len(income)}')
    if not income:
        print('  ⚠️ t187ap14_L failed or empty')
    # YoY EPS 成長率改由 main() 的 incomeHistory 機制計算（逐季累積，跨年比對）
    print(f'  Total income: {len(income)}')
    return income


# ── 6. Financial News (Google News RSS) ──────────────────────────────────────
def fetch_margin():
    """融資融券餘額 MI_MARGN → {code: {today, prev, change}}"""
    def parse_num(v):
        try: return int(str(v or '').replace(',', ''))
        except: return 0
    margin = {}
    data = get('https://openapi.twse.com.tw/v1/marginTrading/MI_MARGN')
    if data:
        for row in data:
            code = str(row.get('股票代號') or '').strip()
            if not code:
                continue
            t = parse_num(row.get('融資今日餘額'))
            p = parse_num(row.get('融資前日餘額'))
            margin[code] = {'today': t, 'prev': p, 'change': t - p}
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
        return result, None
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
    # 從 FMTQIK 取得實際交易日期（STOCK_DAY_ALL 無日期欄位）
    # FMTQIK Date 格式：'1150522'（民國年7位）→ 2026-05-22
    price_date = None
    fmtqik = get('https://openapi.twse.com.tw/v1/exchangeReport/FMTQIK')
    if fmtqik and isinstance(fmtqik, list) and len(fmtqik) > 0:
        raw_date = str(fmtqik[-1].get('Date', '') or '').strip()
        if len(raw_date) == 7 and raw_date.isdigit():
            try:
                price_date = f'{int(raw_date[:3]) + 1911}-{raw_date[3:5]}-{raw_date[5:7]}'
            except Exception:
                pass
        elif re.match(r'\d{4}-\d{2}-\d{2}', raw_date):
            price_date = raw_date
    print(f'  TWSE 官方今日價格：{len(result)} 支（交易日期：{price_date}）')
    return result, price_date


def fetch_tpex_prices_today():
    """TPEX 每日收盤行情（openapi，與 fetch_otc_stocks 同端點，完整 OHLCV）"""
    data = get('https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes')
    if not data or not isinstance(data, list):
        print('  [WARN] TPEX openapi 收盤行情取得失敗')
        return {}
    # 從回傳資料讀取實際日期（民國年格式 '1150521' → 2026-05-21）
    price_date = None
    raw_date = str(data[0].get('Date', '') if data else '').strip()
    if len(raw_date) == 7:
        try:
            roc_y, mo, dy = int(raw_date[:3]), int(raw_date[3:5]), int(raw_date[5:7])
            price_date = f'{roc_y + 1911}-{mo:02d}-{dy:02d}'
        except Exception:
            pass
    result = {}
    for r in data:
        code = str(r.get('SecuritiesCompanyCode') or '').strip()
        if not re.match(r'^\d{4}$', code):
            continue
        c = _flt(r.get('Close'))
        if not c or c <= 0:
            continue
        h = _flt(r.get('High')) or c
        l = _flt(r.get('Low'))  or c
        o = _flt(r.get('Open')) or c
        try:
            v = int(str(r.get('TradingShares') or '0').replace(',', ''))
        except Exception:
            v = 0
        result[code] = {'c': round(c, 2), 'h': round(h, 2), 'l': round(l, 2), 'o': round(o, 2), 'v': v}
    if len(result) > 100:
        print(f'  TPEX openapi 收盤行情（{price_date}）：{len(result)} 支')
        return result, price_date
    print('  [WARN] TPEX openapi 解析失敗（回傳 {len(result)} 支）')
    return {}, None


def update_price_history(existing, today_prices, name_map, trading_date=None):
    """
    增量更新歷史 OHLCV：
    - 同日多次執行 → 取代第一根（即時價格更新，不重複 prepend）
    - 新交易日 → prepend 新的一根，保留最多 252 根
    用 _d 欄位記錄最近一根的台灣日期，防止同日重複 append。
    trading_date: 實際交易日期（來自 FMTQIK 或 intraday today_date），
                  必須傳入，不再靠 last_trading_date_tw() 推算（假日會誤判）。
    """
    out = dict(existing)
    today_tw = trading_date if trading_date else last_trading_date_tw()
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
    s = str(v).strip().replace(',', '')
    if s in ('', '-', '--', 'N/A', 'NA', 'n/a'): return None
    try: return float(s)
    except: return None

def _int(v):
    if v is None: return 0
    try: return int(str(v).replace(',', ''))
    except: return 0

def _int2(v):
    if v is None: return None
    s = str(v).strip().replace(',', '')
    if s in ('', '-', '--', 'N/A', 'NA'): return None
    try: return int(float(s))
    except: return None

def last_trading_date_tw():
    """最近的台灣交易日（週六→週五，週日→週五），避免週末執行產生假日期"""
    tz = datetime.timezone(datetime.timedelta(hours=8))
    dt = datetime.datetime.now(tz)
    if dt.weekday() == 5: dt -= datetime.timedelta(days=1)   # Sat → Fri
    elif dt.weekday() == 6: dt -= datetime.timedelta(days=2)  # Sun → Fri
    return dt.strftime('%Y-%m-%d')

def is_trading_hours_tw():
    """是否在台灣股市交易時間內（週一~週五 09:00–13:30 TW）"""
    tz = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.now(tz)
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return 9 * 60 <= t <= 13 * 60 + 30

def fetch_twse_mis_prices(all_stocks):
    """
    盤中：使用 mis.twse.com.tw 取得即時成交價（z 欄位）。
    all_stocks: [{code, ex}]，ex='TW' for TSE，ex='TWO' for OTC。
    回傳 {code: {c,h,l,o,v}}，只包含 z > 0（已成交）的股票。

    GLM 調查確認：
    - batch_size 必須 ≤ 15（100 支會超過 URL 長度限制導致 414/500）
    - v 欄位已是「股」(shares)，不需乘 1000
    - 需先 warm-up session 取得 Cookie
    - sleep 1.0s（Azure IP 容易被 TWSE rate-limit）
    """
    mis = requests.Session()
    mis.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        'Referer': 'https://mis.twse.com.tw/stock/fibest.jsp',
        'Accept': 'application/json, text/plain, */*',
    })
    # Warm-up session to obtain session cookies (reduces block risk)
    try:
        mis.get('https://mis.twse.com.tw/stock/', timeout=8)
    except Exception:
        pass

    def _fv(v, default):
        try: return float(str(v or '-').strip())
        except: return default

    result = {}
    batch_size = 15  # GLM confirmed: 100 causes 414 URI Too Long on TWSE
    batches = [all_stocks[i:i+batch_size] for i in range(0, len(all_stocks), batch_size)]
    print(f'  mis.twse: {len(all_stocks)} 支 / {len(batches)} 批次（batch_size={batch_size}）')
    for idx, batch in enumerate(batches):
        ex_ch = '|'.join(
            f"{'otc' if s.get('ex') == 'TWO' else 'tse'}_{s['code']}.tw"
            for s in batch
        )
        url = (f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
               f"?ex_ch={requests.utils.quote(ex_ch)}&json=1&delay=0&_={int(time.time()*1000)}")
        success = False
        for attempt in range(2):
            try:
                r = mis.get(url, timeout=10)
                if r.status_code != 200:
                    print(f'  [WARN] mis batch {idx+1} attempt {attempt+1} HTTP {r.status_code}')
                    if attempt == 0: time.sleep(2)
                    continue
                data = r.json()
                for item in (data.get('msgArray') or []):
                    code = str(item.get('c') or '').strip()
                    if not code:
                        continue
                    try:
                        z = float(str(item.get('z') or '-').strip())
                    except (ValueError, TypeError):
                        continue
                    if z <= 0:
                        continue  # z='-' or 0: skip entirely, never use y (yesterday's close)
                    h = _fv(item.get('h'), z)
                    l = _fv(item.get('l'), z)
                    o = _fv(item.get('o'), z)
                    # v is already in shares (股), NOT lots (張) — no *1000 needed
                    try:
                        v = int(float(str(item.get('v') or '0').replace(',', '')))
                    except (ValueError, TypeError):
                        v = 0
                    result[code] = {
                        'c': round(z, 2), 'h': round(h, 2),
                        'l': round(l, 2), 'o': round(o, 2), 'v': v
                    }
                success = True
                break
            except Exception as e:
                print(f'  [WARN] mis batch {idx+1} attempt {attempt+1} exception: {e}')
                if attempt == 0: time.sleep(2)
        if not success:
            print(f'  [ERROR] mis batch {idx+1} failed after 2 attempts')
        if idx < len(batches) - 1:
            time.sleep(1.0)  # 1.0s rate limit (Azure IPs risk TWSE blocking)
    pct = round(len(result)/len(all_stocks)*100) if all_stocks else 0
    print(f'  mis.twse.com.tw 即時成交：{len(result)}/{len(all_stocks)} 支（{pct}%）')
    return result


def fetch_intraday_yahoo(all_stocks):
    """
    盤中：使用 Yahoo Finance v7 quote API 取得即時 OHLCV。
    all_stocks: [{code, ex}]，ex='TW' or 'TWO'。
    回傳 {code: {c,h,l,o,v}}，c = regularMarketPrice（即時成交價）。
    從 GitHub Actions server-side 呼叫（無 CORS 限制）。
    batch_size=50, sleep=2s（GLM 建議，降低 429/999 風險）。
    """
    YAHOO_HEADERS = {
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/120.0.0.0 Safari/537.36'),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
    }
    symbols = [f"{s['code']}.{s['ex']}" for s in all_stocks]
    code_map = {f"{s['code']}.{s['ex']}": s['code'] for s in all_stocks}
    result = {}
    batch_size = 50
    batches = [symbols[i:i+batch_size] for i in range(0, len(symbols), batch_size)]
    print(f'  Yahoo Finance: {len(symbols)} 支 / {len(batches)} 批次')

    for idx, batch in enumerate(batches):
        url = ('https://query1.finance.yahoo.com/v7/finance/quote'
               f'?symbols={",".join(batch)}&_t={int(time.time()*1000)}')
        success = False
        for attempt in range(3):
            try:
                r = requests.get(url, headers=YAHOO_HEADERS, timeout=15)
                if r.status_code in (429, 999):
                    wait = (attempt + 1) * 10
                    print(f'  [WARN] Yahoo batch {idx+1} HTTP {r.status_code}, wait {wait}s')
                    time.sleep(wait)
                    continue
                if not r.ok:
                    print(f'  [WARN] Yahoo batch {idx+1} HTTP {r.status_code}')
                    break
                quotes = r.json().get('quoteResponse', {}).get('result', [])
                for q in quotes:
                    sym = q.get('symbol', '')
                    code = code_map.get(sym)
                    if not code:
                        continue
                    c = q.get('regularMarketPrice')
                    if not c or c <= 0:
                        continue
                    h = q.get('regularMarketDayHigh') or c
                    l = q.get('regularMarketDayLow') or c
                    o = q.get('regularMarketOpen') or c
                    v = q.get('regularMarketVolume') or 0
                    result[code] = {
                        'c': round(float(c), 2), 'h': round(float(h), 2),
                        'l': round(float(l), 2), 'o': round(float(o), 2),
                        'v': int(v)
                    }
                success = True
                break
            except Exception as e:
                print(f'  [WARN] Yahoo batch {idx+1} attempt {attempt+1}: {e}')
                if attempt < 2:
                    time.sleep(5)
        if not success:
            print(f'  [ERROR] Yahoo batch {idx+1} failed after 3 attempts')
        if idx < len(batches) - 1:
            time.sleep(2.0)

    pct = round(len(result)/len(all_stocks)*100) if all_stocks else 0
    print(f'  Yahoo Finance 即時價格：{len(result)}/{len(all_stocks)} 支（{pct}%）')
    return result


def atomic_write(path, data):
    """原子寫入 JSON：先寫暫存檔再 rename，防止中途中斷毀損資料"""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)

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

    # incomeHistory：按 {年}Q{季} 累積最多 8 季，跨年正確比對 YoY EPS（含 OTC）
    income_history = existing.get('incomeHistory', {})
    income_prev    = existing.get('incomePrev', {})  # 保留舊欄位，向後相容
    yoy_count = 0
    if income:
        for code, rec in income.items():
            rec_year = int(rec.get('year', 0) or 0)
            rec_qtr  = str(rec.get('quarter', '') or '')
            if not rec_year or not rec_qtr:
                continue
            cur_q_key = f'{rec_year}Q{rec_qtr}'
            if code not in income_history:
                income_history[code] = {}
            income_history[code][cur_q_key] = {'eps': rec.get('eps'), 'revenue': rec.get('revenue')}
            # 保留最近 8 季
            all_keys = sorted(income_history[code].keys(), reverse=True)
            income_history[code] = {k: income_history[code][k] for k in all_keys[:8]}
            # YoY：去年同季
            prev_q_key = f'{rec_year - 1}Q{rec_qtr}'
            prev = income_history[code].get(prev_q_key)
            if prev and prev.get('eps') is not None and rec.get('eps') is not None:
                p_eps = prev['eps']
                if p_eps is not None and p_eps != 0:
                    rec['earningsGrowth'] = round((rec['eps'] - p_eps) / abs(p_eps), 4)
                    yoy_count += 1
        sample = next(iter(income.values()))
        log_year = int(sample.get('year', 0) or 0)
        log_qtr  = str(sample.get('quarter', '') or '')
        print(f'  incomeHistory YoY computed: {yoy_count} stocks（{log_year}Q{log_qtr} vs {log_year-1}Q{log_qtr}）')

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

    # ── 盤中價格取得策略（三層 fallback，確保永遠取到即時成交價）──────────
    # 層 1：Yahoo Finance server-side（主力，無 CORS，從 GitHub Actions 可靠）
    # 層 2：mis.twse.com.tw（備援，Azure IP 可能被封）
    # 層 3：STOCK_DAY_ALL（最後手段，盤中只有昨收，不理想）
    twse_today, twse_price_date_api = {}, None
    tpex_today, tpex_price_date = {}, None

    if is_trading_hours_tw() or os.environ.get('FORCE_YAHOO') == '1':
        today_date = last_trading_date_tw()
        all_for_yahoo = ([{'code': s['code'], 'ex': 'TW'}  for s in tse_stocks] +
                         [{'code': s['code'], 'ex': 'TWO'} for s in otc_stocks])
        tse_codes = {s['code'] for s in tse_stocks}
        otc_codes = {s['code'] for s in otc_stocks}

        # 層 1：Yahoo Finance
        print('  盤中模式（層1）：Yahoo Finance server-side 即時報價...')
        yahoo_all = fetch_intraday_yahoo(all_for_yahoo)
        yahoo_tse = {k: v for k, v in yahoo_all.items() if k in tse_codes}
        yahoo_otc = {k: v for k, v in yahoo_all.items() if k in otc_codes}
        yahoo_ok_tse = len(yahoo_tse) > 100
        yahoo_ok_otc = len(yahoo_otc) > 100
        if yahoo_ok_tse:
            twse_today, twse_price_date_api = yahoo_tse, today_date
            print(f'  ✅ Yahoo TSE 即時：{len(yahoo_tse)} 支')
        else:
            print(f'  ⚠️ Yahoo TSE 不足（{len(yahoo_tse)}），進入層2')
        if yahoo_ok_otc:
            tpex_today, tpex_price_date = yahoo_otc, today_date
            print(f'  ✅ Yahoo OTC 即時：{len(yahoo_otc)} 支')
        else:
            print(f'  ⚠️ Yahoo OTC 不足（{len(yahoo_otc)}），進入層2')

        # 層 2：mis.twse.com.tw（只補 Yahoo 失敗的市場）
        need_mis = (not yahoo_ok_tse) or (not yahoo_ok_otc)
        if need_mis:
            print('  盤中模式（層2）：mis.twse.com.tw 備援...')
            mis_stocks = ([] if yahoo_ok_tse else [{'code': s['code'], 'ex': 'TW'}  for s in tse_stocks]) + \
                         ([] if yahoo_ok_otc else [{'code': s['code'], 'ex': 'TWO'} for s in otc_stocks])
            mis_all = fetch_twse_mis_prices(mis_stocks)
            if not yahoo_ok_tse:
                mis_tse = {k: v for k, v in mis_all.items() if k in tse_codes}
                if len(mis_tse) > 100:
                    twse_today, twse_price_date_api = mis_tse, today_date
                    print(f'  ✅ MIS TSE 即時：{len(mis_tse)} 支')
                else:
                    print(f'  ⚠️ MIS TSE 不足（{len(mis_tse)}），進入層3（昨收）')
                    twse_today, twse_price_date_api = fetch_twse_prices_today()
            if not yahoo_ok_otc:
                mis_otc = {k: v for k, v in mis_all.items() if k in otc_codes}
                if len(mis_otc) > 100:
                    tpex_today, tpex_price_date = mis_otc, today_date
                    print(f'  ✅ MIS OTC 即時：{len(mis_otc)} 支')
                else:
                    print(f'  ⚠️ MIS OTC 不足（{len(mis_otc)}），進入層3（昨收）')
                    tpex_today, tpex_price_date = fetch_tpex_prices_today()
    else:
        # 盤後：官方每日收盤行情（STOCK_DAY_ALL，正式收盤價）
        twse_today, twse_price_date_api = fetch_twse_prices_today()
        tpex_today, tpex_price_date = fetch_tpex_prices_today()

    # 若官方 API 回傳資料不足（市場休假/API 故障），保留舊資料
    tse_ok = len(twse_today) > 100
    otc_ok = len(tpex_today) > 100

    # _price_date：用 FMTQIK 回傳的實際交易日（最可靠），intraday 用 today_date
    tse_price_date = (twse_price_date_api or last_trading_date_tw()) if tse_ok else existing.get('_tse_price_date', '')

    # TPEX 的 Date 欄位有時回傳查詢日（非實際交易日），若 TPEX date > TSE FMTQIK date 則
    # 以 TSE date 為準（兩市場共用同一休市曆）
    raw_otc_date = tpex_price_date if otc_ok else existing.get('_otc_price_date', '')
    if tse_price_date and raw_otc_date and raw_otc_date > tse_price_date:
        print(f'  ⚠️ TPEX date ({raw_otc_date}) > TSE FMTQIK date ({tse_price_date})，以 TSE date 為準（假日判斷）')
        otc_price_date_final = tse_price_date
    else:
        otc_price_date_final = raw_otc_date

    # 傳入實際交易日，避免假日時 _d 被誤標為假日日期
    tse_prices = update_price_history(existing_tse_price, twse_today, name_map, trading_date=tse_price_date) if tse_ok else existing_tse_price
    otc_prices = update_price_history(existing_otc_price, tpex_today, name_map, trading_date=otc_price_date_final) if otc_ok else existing_otc_price
    if not tse_ok:
        print(f'  ⚠️ TWSE 今日資料不足（{len(twse_today)}），保留舊資料')
    if not otc_ok:
        print(f'  ⚠️ TPEX 今日資料不足（{len(tpex_today)}），保留舊資料')
    atomic_write('stocks_tse.json', {'date': date_str, '_price_date': tse_price_date, 'stocks': tse_prices})
    atomic_write('stocks_otc.json', {'date': date_str, '_price_date': otc_price_date_final, 'stocks': otc_prices})
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
        'date':            date_str,
        '_tse_price_date': tse_price_date,
        '_otc_price_date': otc_price_date_final,
        'sectors':         sectors,
        'tseStocks':       tse_stocks or [{'code': code, 'name': info['n']} for code, info in tse_prices.items()],
        'otcStocks':       otc_stocks or [{'code': c, 'name': d.get('n', c)} for c, d in otc_prices.items()],
        'chips':           chips,
        'bwibbu':          bwibbu,
        'monthRevenue':    month_revenue,
        'income':          income,
        'incomePrev':      income_prev,
        'incomeHistory':   income_history,
        'margin':          margin,
        'news':            news,
    }

    atomic_write('twse_daily.json', output)

    print(f'\n✅ twse_daily.json written:')
    print(f'   sectors={len(sectors)}, otcStocks={len(otc_stocks)}, chips={len(chips)}, bwibbu={len(bwibbu)}')
    print(f'   revenue={len(month_revenue)}, income={len(income)}, news={len(news)}')

if __name__ == '__main__':
    main()
