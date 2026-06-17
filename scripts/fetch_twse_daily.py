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
  - gdr      : [{code,name,date,type,amount}] 最近 180 天 GDR 申請/發行
  - borrow   : {code -> {balance,prevBalance,changeAbs,changePct}} 借券賣出餘額全市場
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

D1_QUERY_ENDPOINT = 'https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query'
MAX_D1_SQL_PER_REQUEST = 50
PRICE_ROWS_PER_STATEMENT = 80
FUND_ROWS_PER_STATEMENT = 50

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

    # ── 來源 1：OpenAPI（無 CORS 問題，欄位有中文名稱，最易解析）──────────────
    data = get('https://openapi.twse.com.tw/v1/marginTrading/MI_MARGN')
    if data and isinstance(data, list) and len(data) > 100:
        for row in data:
            code = str(row.get('股票代號') or '').strip()
            if not code:
                continue
            t = parse_num(row.get('融資今日餘額'))
            p = parse_num(row.get('融資前日餘額'))
            margin[code] = {'today': t, 'prev': p, 'change': t - p}
        print(f'  margin: {len(margin)} stocks from OpenAPI MI_MARGN')
        return margin

    # ── 來源 2：MI_MARGN?selectType=ALL（備援，實測可通過）────────────────────
    print('  [WARN] OpenAPI MI_MARGN 失敗，嘗試 selectType=ALL 備援...')
    try:
        SESSION.get('https://www.twse.com.tw/', timeout=6)
        r = SESSION.get(
            'https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?response=json&selectType=ALL',
            timeout=25,
        )
        if r.ok and r.text.strip().startswith('{'):
            j = r.json()
            for tbl in j.get('tables', []):
                rows = tbl.get('data', [])
                if len(rows) < 100:
                    continue
                # 欄位順序: [代號, 名稱, 融資買進, 融資償還, 現金償還, 前日餘額, 今日餘額, ...]
                for row in rows:
                    if len(row) < 7:
                        continue
                    code = str(row[0]).strip()
                    if not re.match(r'^\d{4,6}$', code):
                        continue
                    t = parse_num(row[6])
                    p = parse_num(row[5])
                    margin[code] = {'today': t, 'prev': p, 'change': t - p}
            if margin:
                print(f'  margin: {len(margin)} stocks from MI_MARGN selectType=ALL')
                return margin
    except Exception as e:
        print(f'  [WARN] MI_MARGN selectType=ALL failed: {e}')

    print('  [WARN] MI_MARGN 所有端點均失敗')
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
        return {}, None
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
    print(f'  [WARN] TPEX openapi 解析失敗（回傳 {len(result)} 支）')
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


def fetch_intraday_yfinance(all_stocks):
    import pandas as pd
    import yfinance as yf

    symbols = [f"{s['code']}.{s['ex']}" for s in all_stocks]
    code_map = {f"{s['code']}.{s['ex']}": s['code'] for s in all_stocks}
    result = {}
    if not symbols:
        print(f'  yfinance 即時價格：{len(result)}/{len(all_stocks)} 支')
        return result

    try:
        data = yf.download(symbols, period='1d', progress=False, auto_adjust=False, threads=True)
    except Exception as e:
        print(f'  [WARN] yfinance download failed: {e}')
        print(f'  yfinance 即時價格：{len(result)}/{len(all_stocks)} 支')
        return result

    if data is None or getattr(data, 'empty', True):
        print(f'  yfinance 即時價格：{len(result)}/{len(all_stocks)} 支')
        return result

    def _last_valid(frame, field):
        try:
            value = frame[field].iloc[-1]
        except Exception:
            return None
        if pd.isna(value):
            return None
        return value

    if isinstance(data.columns, pd.MultiIndex):
        for symbol in symbols:
            code = code_map.get(symbol)
            if not code:
                continue
            try:
                frame = data.xs(symbol, axis=1, level=1)
            except Exception:
                continue
            c = _last_valid(frame, 'Close')
            h = _last_valid(frame, 'High')
            l = _last_valid(frame, 'Low')
            v = _last_valid(frame, 'Volume')
            if c is None or c <= 0 or any(pd.isna(x) for x in (h, l, v)):
                continue
            result[code] = {
                'c': round(float(c), 2),
                'h': round(float(h), 2),
                'l': round(float(l), 2),
                'v': int(float(v)),
            }
    else:
        code = all_stocks[0]['code'] if all_stocks else None
        c = _last_valid(data, 'Close')
        h = _last_valid(data, 'High')
        l = _last_valid(data, 'Low')
        v = _last_valid(data, 'Volume')
        if code and c is not None and c > 0 and not any(pd.isna(x) for x in (h, l, v)):
            result[code] = {
                'c': round(float(c), 2),
                'h': round(float(h), 2),
                'l': round(float(l), 2),
                'v': int(float(v)),
            }

    print(f'  yfinance 即時價格：{len(result)}/{len(all_stocks)} 支')
    return result


def atomic_write(path, data):
    """原子寫入 JSON：先寫暫存檔再 rename，防止中途中斷毀損資料"""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, path)

def chunked(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i+size]

def infer_recent_market_dates(latest_date, count):
    """
    Legacy price JSON stores ordered history without per-row dates.
    For the initial D1 backfill, infer prior trading days by skipping weekends.
    """
    try:
        cursor = datetime.datetime.strptime(latest_date, '%Y-%m-%d').date()
    except Exception:
        cursor = datetime.date.today()
    dates = []
    while len(dates) < count:
        if cursor.weekday() < 5:
            dates.append(cursor.strftime('%Y-%m-%d'))
        cursor -= datetime.timedelta(days=1)
    return dates

def build_price_history_rows(price_map, fallback_latest_date):
    rows = []
    for code, info in price_map.items():
        closes = list(info.get('c') or [])
        highs = list(info.get('h') or [])
        lows = list(info.get('l') or [])
        vols = list(info.get('v') or [])
        if not closes:
            continue
        latest_date = info.get('_d') or fallback_latest_date
        if not latest_date:
            continue
        inferred_dates = infer_recent_market_dates(latest_date, len(closes))
        name = info.get('n') or code
        for idx, row_date in enumerate(inferred_dates):
            close = closes[idx] if idx < len(closes) else None
            high = highs[idx] if idx < len(highs) else close
            low = lows[idx] if idx < len(lows) else close
            open_ = close
            volume = vols[idx] if idx < len(vols) else 0
            rows.append((code, row_date, name, close, high, low, open_, volume))
    return rows

def build_fundamentals_rows(output):
    codes = set()
    for key in ('sectors', 'bwibbu', 'income', 'chips', 'monthRevenue', 'margin'):
        codes.update((output.get(key) or {}).keys())
    rows = []
    for code in sorted(codes):
        sector = (output.get('sectors') or {}).get(code)
        bw = (output.get('bwibbu') or {}).get(code, {})
        inc = (output.get('income') or {}).get(code, {})
        chp = (output.get('chips') or {}).get(code, {})
        rev = (output.get('monthRevenue') or {}).get(code, {})
        mg = (output.get('margin') or {}).get(code, {})
        rows.append((
            code,
            sector,
            bw.get('pe'),
            bw.get('pb'),
            bw.get('divYield'),
            inc.get('eps'),
            inc.get('year'),
            inc.get('quarter'),
            inc.get('earningsGrowth'),
            chp.get('foreignNet'),
            chp.get('trustNet'),
            chp.get('dealerNet'),
            rev.get('current'),
            rev.get('yoy'),
            mg.get('today'),
            mg.get('change'),
            output.get('date'),
        ))
    return rows

def build_bulk_insert_sql(table, columns, rows):
    placeholders = '(' + ','.join(['?'] * len(columns)) + ')'
    values_sql = ','.join([placeholders] * len(rows))
    sql = f'INSERT OR REPLACE INTO {table} ({",".join(columns)}) VALUES {values_sql}'
    params = []
    for row in rows:
        params.extend(row)
    return {'sql': sql, 'params': params}

def d1_post_batches(statements, account_id, database_id, token):
    if not statements:
        return
    url = D1_QUERY_ENDPOINT.format(account_id=account_id, database_id=database_id)
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    total_batches = (len(statements) + MAX_D1_SQL_PER_REQUEST - 1) // MAX_D1_SQL_PER_REQUEST
    for idx, sql_batch in enumerate(chunked(statements, MAX_D1_SQL_PER_REQUEST), start=1):
        resp = requests.post(url, headers=headers, json={'batch': sql_batch}, timeout=120)
        if not resp.ok:
            raise RuntimeError(f'D1 HTTP {resp.status_code}: {resp.text[:400]}')
        payload = resp.json()
        if not payload.get('success'):
            raise RuntimeError(f'D1 API failed: {json.dumps(payload)[:400]}')
        print(f'  D1 batch {idx}/{total_batches}: {len(sql_batch)} SQL statements')

def _d1_post_chunk(secret, prices_chunk, fund_rows, meta_date):
    """送一批 price rows 到 d1write，回傳 written counts。"""
    payload = {
        'prices': prices_chunk,
        'fundamentals': fund_rows,
        'meta_date': meta_date,
    }
    resp = requests.post(
        'https://vivian-vcpfinder.pages.dev/api/d1write',
        headers={'Authorization': f'Bearer {secret}', 'Content-Type': 'application/json'},
        json=payload,
        timeout=120,
    )
    if not resp.ok:
        raise RuntimeError(f'D1 write HTTP {resp.status_code}: {resp.text[:400]}')
    result = resp.json()
    if not result.get('success'):
        raise RuntimeError(f'D1 write failed: {json.dumps(result)[:400]}')
    return result.get('written', {})


def sync_d1_database(output, tse_prices, otc_prices, full_sync=False):
    secret = os.environ.get('D1_WRITE_SECRET')
    if not secret:
        print('  [WARN] Skip D1 sync: missing D1_WRITE_SECRET')
        return

    mode = 'FULL_SYNC（252天歷史）' if full_sync else '每日更新（今日1筆）'
    print(f'\n9. Sync Cloudflare D1 [{mode}]...')

    tse_date = output.get('_tse_price_date')
    otc_date = output.get('_otc_price_date') or output.get('date')

    price_rows = []
    for prices_dict, date_str in [(tse_prices, tse_date), (otc_prices, otc_date)]:
        for code, stock in prices_dict.items():
            if not re.match(r'^\d{4,6}$', str(code)):
                continue
            closes = stock.get('c') or []
            if not closes or closes[0] is None:
                continue

            if full_sync:
                # 寫入全部歷史（最多 252 天），用推算交易日期補日期欄位
                highs  = stock.get('h') or []
                lows   = stock.get('l') or []
                vols   = stock.get('v') or []
                latest_date = stock.get('_d') or date_str
                if not latest_date:
                    continue
                dates = infer_recent_market_dates(latest_date, len(closes))
                name  = stock.get('n', str(code))
                for idx, row_date in enumerate(dates):
                    c = closes[idx] if idx < len(closes) else None
                    if c is None:
                        continue
                    price_rows.append({
                        'code': str(code), 'date': row_date, 'name': name,
                        'close': c,
                        'high':   highs[idx]  if idx < len(highs)  else c,
                        'low':    lows[idx]   if idx < len(lows)   else c,
                        'open':   None,
                        'volume': vols[idx]   if idx < len(vols)   else 0,
                    })
            else:
                # 每日只寫今日 1 筆（省 D1 write quota）
                price_rows.append({
                    'code': str(code),
                    'date': stock.get('_d') or date_str,
                    'name': stock.get('n', str(code)),
                    'close': closes[0],
                    'high':   (stock.get('h') or [None])[0],
                    'low':    (stock.get('l') or [None])[0],
                    'open':   None,
                    'volume': (stock.get('v') or [None])[0],
                })

    fund_cols = [
        'code', 'sector', 'pe', 'pb', 'div_yield', 'eps', 'eps_year', 'eps_quarter',
        'earnings_growth', 'foreign_net', 'trust_net', 'dealer_net',
        'revenue_current', 'revenue_yoy', 'margin_today', 'margin_change', 'updated_date',
    ]
    sectors_codes = (
        set((output.get('sectors') or {}).keys()) or
        set((output.get('bwibbu') or {}).keys())
    )
    fund_rows = [
        dict(zip(fund_cols, t)) for t in build_fundamentals_rows(output)
        if not sectors_codes or t[0] in sectors_codes
    ]

    meta_date = tse_date or otc_date or output.get('date')
    print(f'  D1 rows prepared: prices={len(price_rows)}, fundamentals={len(fund_rows)}')

    # 分批送出（每批 500 筆），避免單一 HTTP 請求過大或超時
    CHUNK = 500
    total_written_prices = 0
    chunks = [price_rows[i:i+CHUNK] for i in range(0, max(len(price_rows), 1), CHUNK)]
    for idx, chunk in enumerate(chunks):
        # fundamentals 和 meta_date 只在第一批送出
        written = _d1_post_chunk(
            secret, chunk,
            fund_rows if idx == 0 else [],
            meta_date if idx == 0 else None,
        )
        total_written_prices += written.get('prices', 0)
        if len(chunks) > 1:
            print(f'  批次 {idx+1}/{len(chunks)}: +{written.get("prices",0)} prices')

    print(f'  ✅ D1 sync completed: prices={total_written_prices}, fundamentals={len(fund_rows)}')

def fetch_exdiv():
    """抓取 TWSE 2026 年除息日期，累積存入 exdiv_2026.json
    格式：{ "2330": "06/11", "2317": "07/15", ... }（西元月/日）
    策略：每次只讀「今天 +60 天」的資料，累積合併（不清除舊資料）
    """
    # 載入現有累積資料
    exdiv = {}
    try:
        with open('exdiv_2026.json', 'r', encoding='utf-8') as f:
            existing = json.load(f)
            if isinstance(existing, dict):
                exdiv.update(existing)
    except Exception:
        pass

    today = datetime.date.today()
    start_str = today.strftime('%Y%m%d')
    end_str = (today + datetime.timedelta(days=60)).strftime('%Y%m%d')
    # 只抓 2026 年的資料
    if today.year > 2026:
        print('  ⚠️ 2026年已過，跳過除息抓取')
        return
    end_str = min(end_str, '20261231')

    url = f'https://www.twse.com.tw/rwd/zh/exRight/TWT49U?response=json&startDate={start_str}&endDate={end_str}'
    new_count = 0
    try:
        r = SESSION.get(url, timeout=20)
        j = r.json()
        if j.get('stat') != 'OK' or not isinstance(j.get('data'), list):
            print(f'  ⚠️ 除息資料 stat={j.get("stat")}，跳過')
        else:
            for row in j['data']:
                # row[11] = "code,yyyymmdd"（最可靠的欄位）
                key = str(row[11] if len(row) > 11 else '').strip()
                if ',' in key:
                    code, datestr = key.split(',', 1)
                    if len(datestr) == 8:
                        mm, dd = datestr[4:6], datestr[6:8]
                        if code not in exdiv:
                            exdiv[code] = f'{mm}/{dd}'
                            new_count += 1
    except Exception as e:
        print(f'  ⚠️ 除息資料抓取失敗：{e}')

    atomic_write('exdiv_2026.json', exdiv)
    print(f'  ✅ exdiv_2026.json: 累計 {len(exdiv)} 筆，本次新增 {new_count} 筆')

# ── 9. GDR 發行偵測（MOPS 海外存託憑證 + TWSE OpenAPI + News RSS）────────────
def fetch_gdr_list(name_to_code=None):
    """抓最近 180 天的 GDR 申請/發行股票，來源：TWSE OpenAPI t187ap13_L + MOPS + Google News
    name_to_code: {公司名稱: 股票代號} dict，用於從新聞標題中比對公司名稱取得代號
    """
    import html as html_lib
    result = []
    seen_codes = set()

    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    now_tw = datetime.datetime.now(tz_tw)
    year_roc = now_tw.year - 1911

    # ── 來源 1：TWSE openapi t187ap13_L ─────────────────────────────────────
    try:
        r = SESSION.get('https://openapi.twse.com.tw/v1/opendata/t187ap13_L', timeout=20)
        if r.ok:
            d = r.json()
            if isinstance(d, list):
                for row in d:
                    code = str(row.get('公司代號') or '').strip()
                    name = str(row.get('公司名稱') or '').strip()
                    sec_type = str(row.get('有價證券種類') or row.get('種類') or '').strip()
                    date_raw = str(row.get('申請日期') or row.get('日期') or '').strip()
                    amount = str(row.get('募集金額') or row.get('金額') or '').strip()
                    if re.search(r'GDR|ADR|DR|存託憑證', sec_type, re.I):
                        if code and re.match(r'^\d{4,6}$', code) and code not in seen_codes:
                            date_ad = ''
                            m = re.match(r'(\d{3})[/-]?(\d{2})[/-]?(\d{2})', date_raw)
                            if m:
                                date_ad = f'{int(m.group(1))+1911}-{m.group(2)}-{m.group(3)}'
                            result.append({'code': code, 'name': name, 'date': date_ad,
                                           'type': sec_type, 'amount': amount, 'src': 'TWSE-OpenAPI'})
                            seen_codes.add(code)
        print(f'  GDR from TWSE OpenAPI: {len(result)}')
    except Exception as e:
        print(f'  [WARN] GDR TWSE OpenAPI failed: {e}')

    # ── 來源 2：MOPS ajax_t78sb01_q1 ─────────────────────────────────────────
    try:
        mops_url = 'https://mops.twse.com.tw/mops/web/ajax_t78sb01_q1'
        for yr_offset in range(2):
            yr = year_roc - yr_offset
            r = SESSION.post(mops_url, data={
                'encodeURIComponent': '1', 'step': '1', 'firstin': '1',
                'off': '1', 'queryName': 'co_id', 'inpuType': 'co_id',
                'TYPEK': 'all', 'isnew': 'false', 'co_id': '',
                'year': str(yr), 'month': '', 'type': '',
            }, timeout=20, headers={'Content-Type': 'application/x-www-form-urlencoded'})
            if not r.ok:
                continue
            trs = re.findall(r'<tr[^>]*>(.*?)</tr>', r.text, re.S)
            for tr in trs:
                tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
                tds = [html_lib.unescape(re.sub(r'<[^>]+>', '', td).strip()) for td in tds]
                if len(tds) < 3:
                    continue
                code = ''
                for td in tds[:3]:
                    m = re.match(r'^(\d{4,6})$', td.strip())
                    if m:
                        code = m.group(1)
                        break
                if not code or code in seen_codes:
                    continue
                name = tds[1] if len(tds) > 1 else ''
                date_raw = next((td for td in tds if re.match(r'\d{3}[/年]\d{1,2}', td)), '')
                date_ad = ''
                m2 = re.match(r'(\d{3})[/年](\d{1,2})[/月]?(\d{0,2})', date_raw)
                if m2:
                    date_ad = f'{int(m2.group(1))+1911}-{m2.group(2).zfill(2)}-{(m2.group(3) or "01").zfill(2)}'
                sec_type = next((td for td in tds if re.search(r'GDR|ADR|DR|存託', td, re.I)), 'GDR')
                result.append({'code': code, 'name': name, 'date': date_ad,
                               'type': sec_type, 'amount': '', 'src': 'MOPS'})
                seen_codes.add(code)
        print(f'  GDR total after MOPS: {len(result)}')
    except Exception as e:
        print(f'  [WARN] GDR MOPS failed: {e}')

    # ── 來源 2b：MOPS 重大訊息關鍵字搜尋（t05st01_q2）─────────────────────────
    # 補充 MOPS GDR 申請表（來源2）未收錄的「計劃公告」階段訊息
    try:
        mops_news_url = 'https://mops.twse.com.tw/mops/web/ajax_t05st01_q2'
        mops_news_kws = ['GDR', '海外存託憑證', '私募GDR', '海外募資 GDR']
        n_before = len(result)
        for yr_offset in range(2):
            yr = year_roc - yr_offset
            for kw in mops_news_kws:
                try:
                    r = SESSION.post(mops_news_url, data={
                        'encodeURIComponent': '1', 'step': '1', 'firstin': '1',
                        'off': '1', 'keyword': kw, 'TYPEK': 'all',
                        'co_id': '', 'year': str(yr), 'month': '', 'type': '',
                    }, timeout=20, headers={
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'Referer': 'https://mops.twse.com.tw/mops/web/t05st01',
                    })
                    if not r.ok or '<table' not in r.text:
                        continue
                    trs = re.findall(r'<tr[^>]*>(.*?)</tr>', r.text, re.S)
                    for tr in trs:
                        tds = re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)
                        tds = [html_lib.unescape(re.sub(r'<[^>]+>', '', td).strip()) for td in tds]
                        if len(tds) < 4:
                            continue
                        code = tds[0].strip() if re.match(r'^\d{4,6}$', tds[0].strip()) else ''
                        if not code or code in seen_codes:
                            continue
                        # 主旨（第5欄或最後欄）需含 GDR/存託/海外募資 才算 GDR 相關
                        subject = tds[4] if len(tds) > 4 else tds[-1]
                        if not re.search(r'GDR|ADR|DR|存託|海外募資|海外上市', subject, re.I):
                            continue
                        name = tds[1] if len(tds) > 1 else ''
                        date_raw = tds[2] if len(tds) > 2 else ''
                        date_ad = ''
                        m2 = re.match(r'(\d{3})/(\d{1,2})/(\d{1,2})', date_raw)
                        if m2:
                            date_ad = f'{int(m2.group(1))+1911}-{m2.group(2).zfill(2)}-{m2.group(3).zfill(2)}'
                        result.append({'code': code, 'name': name, 'date': date_ad,
                                       'type': 'GDR', 'amount': '', 'src': 'MOPS-重大訊息',
                                       'title': subject[:100]})
                        seen_codes.add(code)
                        print(f'    MOPS重大訊息: {code} {name}（{subject[:50]}）')
                except Exception as e2:
                    print(f'  [WARN] MOPS重大訊息 {kw} {yr}: {e2}')
        print(f'  GDR total after MOPS重大訊息: {len(result)} (+{len(result)-n_before})')
    except Exception as e:
        print(f'  [WARN] GDR MOPS重大訊息 failed: {e}')

    # ── 來源 3：Google News RSS ───────────────────────────────────────────────
    # 名稱比對條件：GDR/DR/存託（明確）或 海外募資/海外上市（廣義，搭配廣義關鍵字搜尋）
    _gdr_title_re = re.compile(r'GDR|ADR|DR|存託|海外存託|海外募資|海外上市|海外掛牌|海外私募', re.I)
    try:
        gdr_keywords = [
            # 明確 GDR/存託 類
            '台股 GDR 發行', '海外存託憑證 申請', 'DR 上市 台灣',
            '台股 私募 GDR', 'GDR 私募 申請', '私募海外存託憑證',
            'GDR 定價 台灣', '海外存託憑證 規劃', 'ADR GDR 台灣',
            # 廣義海外募資（需標題同時含 GDR 相關字才算）
            '台股 海外募資計劃', '海外私募 台灣', '台灣企業 海外上市 GDR',
        ]
        for kw in gdr_keywords:
            rss_url = f'https://news.google.com/rss/search?q={requests.utils.quote(kw)}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant'
            r = SESSION.get(rss_url, timeout=12)
            if not r.ok:
                continue
            # 用 r.text（已依 HTTP Content-Type 解碼），再 encode 成 UTF-8 給 ET 解析
            root = ET.fromstring(r.text.encode('utf-8'))
            for item in root.iter('item'):
                title = (item.findtext('title') or '').strip()
                pub = (item.findtext('pubDate') or '')[:16]
                # 方法一：從標題找代號（括號內數字或開頭4-6位數字）
                found_by_code = False
                for m in re.finditer(r'[（(「](\d{4,6})[）)」]|^(\d{4,6})\b', title):
                    code = m.group(1) or m.group(2)
                    if code and code not in seen_codes and re.match(r'^\d{4,6}$', code):
                        result.append({'code': code, 'name': '', 'date': pub,
                                       'type': 'GDR', 'amount': '', 'src': 'news',
                                       'title': title[:100]})
                        seen_codes.add(code)
                        found_by_code = True
                # 方法二：公司名稱比對（標題需含 GDR/存託/海外募資/海外上市 等關鍵字）
                if not found_by_code and name_to_code and _gdr_title_re.search(title):
                    for cname, ccode in name_to_code.items():
                        if ccode in seen_codes or len(cname) < 3:
                            continue
                        if cname in title:
                            result.append({'code': ccode, 'name': cname, 'date': pub,
                                           'type': 'GDR', 'amount': '', 'src': 'news',
                                           'title': title[:100]})
                            seen_codes.add(ccode)
                            print(f'    GDR news 名稱比對: {cname} → {ccode}（{title[:60]}）')
                            break
    except Exception as e:
        print(f'  [WARN] GDR news RSS failed: {e}')

    # 修正 news 來源的 pubDate 格式（RSS RFC 2822 → YYYY-MM-DD）
    import email.utils as _eu
    for g in result:
        if g.get('src') == 'news' and g.get('date') and not re.match(r'^\d{4}-\d{2}-\d{2}', g['date']):
            try:
                g['date'] = _eu.parsedate_to_datetime(g['date']).strftime('%Y-%m-%d')
            except Exception:
                g['date'] = ''

    # 180天 = 保留上限；90天 = 進行中 vs 已完成分界
    # completed=True 代表 GDR 已完成，股票已流入市面
    cutoff_keep   = (now_tw - datetime.timedelta(days=180)).strftime('%Y-%m-%d')
    cutoff_active = (now_tw - datetime.timedelta(days=90)).strftime('%Y-%m-%d')

    valid = []
    for g in result:
        date = g.get('date', '')
        if not date:
            continue          # 日期無法解析，排除（不再「無日期就保留」）
        if date < cutoff_keep:
            continue          # 超過 180 天，排除
        g['completed'] = date < cutoff_active   # >90天前 = GDR 已完成
        valid.append(g)

    active_cnt = sum(1 for g in valid if not g.get('completed'))
    done_cnt   = sum(1 for g in valid if g.get('completed'))
    print(f'  GDR final: {len(valid)} active={active_cnt} completed={done_cnt} → {[g["code"] for g in valid]}')
    return valid


# ── 10. 借券賣出餘額全市場掃描 ───────────────────────────────────────────────
def fetch_borrow_balance(existing_borrow=None):
    """抓 TWSE 全部股票借券賣出餘額（MI_SLBK），與前日比較計算暴增幅度"""
    prev = existing_borrow or {}
    borrow = {}

    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    today_str = datetime.datetime.now(tz_tw).strftime('%Y%m%d')

    raw = None

    # ── 來源 0：CF Worker /api/borrow（走 Cloudflare IP，繞過 GitHub Actions 封鎖）──
    for cf_url in [
        'https://vivian-vcpfinder.pages.dev/api/borrow',
        'https://vivian-vcpfinder.pages.dev/api/borrow',  # retry once
    ]:
        try:
            r = SESSION.get(cf_url, timeout=20)
            if r.ok and r.text.strip().startswith('{'):
                j = r.json()
                if j.get('stat') == 'OK' and j.get('data') and len(j['data']) > 0:
                    raw = j
                    src = j.get('_src', 'CF-Worker')
                    print(f'  borrow: CF Worker OK ({src}), {len(j["data"])} rows')
                    break
        except Exception as e:
            print(f'  [WARN] CF Worker borrow: {e}')

    # ── 主端點：MI_SLBK 全市場（需 warmup cookie） ───────────────────────────
    try:
        # 先 warmup（讓 TWSE 設定 session cookie，避免空 body 回應）
        SESSION.get('https://www.twse.com.tw/zh/page/fund/MI_SLBK.html', timeout=10)
    except Exception:
        pass

    for url in [
        f'https://www.twse.com.tw/rwd/zh/fund/MI_SLBK?response=json&selectType=AL&date={today_str}',
        'https://www.twse.com.tw/rwd/zh/fund/MI_SLBK?response=json&selectType=AL',
        f'https://www.twse.com.tw/fund/MI_SLBK?response=json&selectType=AL&date={today_str}',
    ]:
        try:
            r = SESSION.get(url, timeout=30, headers={'Accept': 'application/json, text/javascript, */*'})
            snippet = r.text[:80].replace('\n', ' ') if r.text else '(empty)'
            print(f'  MI_SLBK {r.status_code} body={snippet!r}')
            if r.ok and r.text.strip():
                j = r.json()
                if j.get('stat') == 'OK' and j.get('data'):
                    raw = j
                    print(f'  borrow: MI_SLBK OK, {len(j["data"])} rows, date={j.get("date","")}')
                    break
                else:
                    print(f'  [WARN] MI_SLBK stat={j.get("stat")}, rows={len(j.get("data",[]))}')
            else:
                print(f'  [WARN] MI_SLBK empty body or {r.status_code}')
        except Exception as e:
            print(f'  [WARN] MI_SLBK {url[:60]}: {e}')

    # ── 備援 1：BFT41U 借券賣出餘額前20名 ─────────────────────────────────────
    if not raw:
        for url in [
            'https://www.twse.com.tw/rwd/zh/fund/BFT41U?response=json',
            f'https://www.twse.com.tw/rwd/zh/fund/BFT41U?response=json&date={today_str}',
        ]:
            try:
                r = SESSION.get(url, timeout=20)
                if r.ok and r.text.strip():
                    j = r.json()
                    if j.get('stat') == 'OK' and j.get('data'):
                        raw = j
                        print(f'  borrow: BFT41U fallback OK (top 20), date={j.get("date","")}')
                        break
            except Exception as e:
                print(f'  [WARN] BFT41U {url[:60]}: {e}')

    # ── 備援 2：openapi 公開 JSON（無需 cookie） ──────────────────────────────
    if not raw:
        try:
            d = get('https://openapi.twse.com.tw/v1/stockNews/SecuritiesLending')
            if d and isinstance(d, list) and len(d) > 0:
                raw = {'data': d, 'stat': 'OK', 'date': today_str, '_openapi': True}
                print(f'  borrow: OpenAPI SecuritiesLending OK, {len(d)} rows')
        except Exception as e:
            print(f'  [WARN] OpenAPI SecuritiesLending: {e}')

    if not raw:
        print('  [WARN] 無法取得借券資料，保留舊資料')
        return prev

    date_str = raw.get('date', today_str)
    fields = raw.get('fields', [])
    is_openapi = raw.get('_openapi', False)

    def _parse_row(row):
        """統一解析 MI_SLBK / BFT41U array row 或 OpenAPI dict"""
        if is_openapi and isinstance(row, dict):
            code = str(row.get('SecuritiesCompanyCode') or row.get('StockNo') or '').strip()
            name = str(row.get('CompanyName') or row.get('SecuritiesCompanyName') or '').strip()
            try:
                balance = int(str(row.get('LendingBalance') or row.get('LendingShares') or 0).replace(',', ''))
            except:
                balance = 0
            return code, name, balance
        else:
            # array row：找 balance_col
            balance_col = 4
            if fields:
                for i, f in enumerate(fields):
                    if '合計出借' in f or '出借數量' in f or '賣出餘額' in f:
                        balance_col = i
                        break
            code = str(row[0]).strip() if row else ''
            name = str(row[1]).strip() if len(row) > 1 else ''
            try:
                balance = int(str(row[balance_col]).replace(',', '')) if len(row) > balance_col else 0
            except:
                balance = 0
            return code, name, balance

    for row in raw.get('data', []):
        code, name, balance = _parse_row(row)
        if not re.match(r'^\d{4,6}$', code):
            continue
        prev_entry = prev.get(code, {})
        prev_bal = prev_entry.get('balance', 0) if isinstance(prev_entry, dict) else 0
        change_abs = balance - prev_bal
        change_pct = round(change_abs / prev_bal * 100, 2) if prev_bal > 0 else 0.0
        borrow[code] = {
            'name': name, 'balance': balance,
            'prevBalance': prev_bal, 'changeAbs': change_abs,
            'changePct': change_pct, 'date': date_str,
        }

    surge = [(c, v) for c, v in borrow.items() if v['changePct'] >= 20 and v['balance'] >= 500]
    surge.sort(key=lambda x: -x[1]['changePct'])
    print(f'  borrow: {len(borrow)} stocks，借券暴增(≥20%,≥500張): {len(surge)} 支')
    for c, v in surge[:5]:
        print(f'    {c} {v["name"]} 餘額={v["balance"]:,} 增{v["changePct"]:+.1f}%')
    return borrow


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
        if not yahoo_ok_tse:
            print('  盤中模式（層1b）：yfinance 即時報價（fallback）...')
            yf_all = fetch_intraday_yfinance(all_for_yahoo)
            yf_tse = {k: v for k, v in yf_all.items() if k in tse_codes}
            yf_otc = {k: v for k, v in yf_all.items() if k in otc_codes}
            if len(yf_tse) > 100:
                twse_today, twse_price_date_api = yf_tse, today_date
                yahoo_ok_tse = True
            if len(yf_otc) > 100:
                tpex_today, tpex_price_date = yf_otc, today_date
                yahoo_ok_otc = True

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

        # TWSE FMTQIK 延遲更新（通常要到晚上 20:00 後才有今日資料）
        # 若 TPEX 已有今日資料但 FMTQIK 還在昨日 → 改用 MIS 即時 API 補抓 TSE 今日收盤
        today_tw = last_trading_date_tw()
        if (tpex_price_date == today_tw
                and twse_price_date_api
                and twse_price_date_api < today_tw):
            print(f'  ⚠️ STOCK_DAY_ALL 未更新 ({twse_price_date_api} < {today_tw})，改用 MIS 補抓 TSE 今日收盤')
            all_tse = [{'code': s['code'], 'ex': 'TW'} for s in tse_stocks]
            if all_tse:
                mis_today = fetch_twse_mis_prices(all_tse)
                if len(mis_today) > 100:
                    twse_today = mis_today
                    twse_price_date_api = today_tw
                    print(f'  ✅ MIS 補抓成功：{len(mis_today)} 支，price_date={today_tw}')
                else:
                    twse_price_date_api = today_tw
                    print(f'  ⚠️ MIS 補抓不足（{len(mis_today)} 支），沿用 STOCK_DAY_ALL，date 強制更新為今日')

    # 若官方 API 回傳資料不足（市場休假/API 故障），保留舊資料
    tse_ok = len(twse_today) > 100
    otc_ok = len(tpex_today) > 100

    # _price_date：用 FMTQIK 或 MIS 補抓後的實際交易日
    tse_price_date = (twse_price_date_api or last_trading_date_tw()) if tse_ok else existing.get('_tse_price_date', '')

    # 兩市場共用同一交易日曆；FMTQIK 延遲已由上方 MIS 補正，直接用各自日期
    raw_otc_date = tpex_price_date if otc_ok else existing.get('_otc_price_date', '')
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

    print('\n9. GDR 發行偵測（MOPS + TWSE OpenAPI + News RSS）...')
    # 建立公司名稱 → 代號對照表（供 Google News 標題比對，名稱長度≥3 避免誤抓）
    _name_to_code = {s['name']: s['code'] for s in (tse_stocks + otc_stocks)
                     if s.get('name') and s.get('code') and len(s.get('name', '')) >= 3}
    gdr_list = fetch_gdr_list(name_to_code=_name_to_code)
    if not gdr_list:
        old_gdr = existing.get('gdr', [])
        if old_gdr:
            print(f'  ⚠️ GDR 資料空，保留舊資料（{len(old_gdr)}筆）')
            gdr_list = old_gdr
    # 補名稱（從本次已抓到的股票清單）
    stock_name_map = {s['code']: s['name'] for s in (tse_stocks + otc_stocks) if s.get('code') and s.get('name')}
    for g in gdr_list:
        if not g.get('name') and g.get('code') in stock_name_map:
            g['name'] = stock_name_map[g['code']]
            print(f'    補名稱: {g["code"]} → {g["name"]}')

    print('\n10. 借券賣出餘額全市場掃描（TWSE MI_SLBK）...')
    borrow_data = fetch_borrow_balance(existing.get('borrow', {}))
    if not borrow_data and existing.get('borrow'):
        print(f'  ⚠️ 借券資料空，保留舊資料（{len(existing["borrow"])}筆）')
        borrow_data = existing['borrow']

    # ── 11. 5日歷史：融資 / 借券 rolling buffer（每天 append，保留最近 5 天）────
    print('\n11. 5日歷史快照...')
    margin_history = existing.get('margin_history', [])
    if margin and len(margin) > 100:
        snap_m = {code: v.get('today', 0) for code, v in margin.items()}
        margin_history = [h for h in margin_history if h.get('date') != date_str]
        margin_history.append({'date': date_str, 'data': snap_m})
        margin_history = margin_history[-5:]
        print(f'  margin_history: {len(margin_history)} days')

    borrow_history = existing.get('borrow_history', [])
    if borrow_data and len(borrow_data) > 100:
        snap_b = {code: v.get('balance', 0) for code, v in borrow_data.items()}
        borrow_history = [h for h in borrow_history if h.get('date') != date_str]
        borrow_history.append({'date': date_str, 'data': snap_b})
        borrow_history = borrow_history[-5:]
        print(f'  borrow_history: {len(borrow_history)} days')

    # ── 12. GDR 地雷偵測：對 GDR 名單做四條件評分 ──────────────────────────────
    print('\n12. GDR 地雷偵測...')
    gdr_codes = {g['code'] for g in gdr_list}
    gdr_danger = []
    for g in gdr_list:
        code = g['code']
        danger = {'code': code, 'name': g.get('name', ''), 'note': g.get('note', ''),
                  'signals': [], 'score': 0}

        # 借券暴增（今日）
        brow = borrow_data.get(code, {})
        if brow.get('changePct', 0) >= 20 and brow.get('balance', 0) >= 500:
            danger['signals'].append('借券暴增')
            danger['score'] += 3
        elif brow.get('balance', 0) >= 5000:
            danger['signals'].append('高借券')
            danger['score'] += 2

        # 借券連3日增（從 borrow_history）
        if len(borrow_history) >= 3:
            balances = [h['data'].get(code, 0) for h in borrow_history[-3:]]
            if len(balances) == 3 and balances[1] > balances[0] and balances[2] > balances[1]:
                if '借券暴增' not in danger['signals']:
                    danger['signals'].append('借券連3增')
                danger['score'] += 2

        # 融資接刀（今日融資增加）
        marg = margin.get(code, {})
        if marg.get('change', 0) > 0:
            danger['signals'].append('融資接刀')
            danger['score'] += 2

        # 融資連3增
        if len(margin_history) >= 3:
            mvals = [h['data'].get(code, 0) for h in margin_history[-3:]]
            if len(mvals) == 3 and mvals[1] > mvals[0] and mvals[2] > mvals[1]:
                if '融資接刀' not in danger['signals']:
                    danger['signals'].append('融資連3增')
                danger['score'] += 2

        # 外資買超（籌碼背離）
        chip = chips.get(code, {})
        foreign_shares = chip.get('foreignNet', 0)
        if foreign_shares > 100000:
            danger['signals'].append('外資買超(誘多)')
            danger['score'] += 1

        if danger['score'] >= 3:
            danger['level'] = '🚨 極危' if danger['score'] >= 7 else ('🔴 高危' if danger['score'] >= 5 else '🟠 警戒')
            gdr_danger.append(danger)

    gdr_danger.sort(key=lambda x: -x['score'])
    print(f'  GDR 地雷警示: {len(gdr_danger)} 支')
    for d in gdr_danger[:5]:
        print(f'    {d["code"]} {d["name"]} score={d["score"]} {d["signals"]}')

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
        'margin_history':  margin_history,
        'news':            news,
        'gdr':             gdr_list,
        'gdr_danger':      gdr_danger,
        'borrow':          borrow_data,
        'borrow_history':  borrow_history,
    }

    atomic_write('twse_daily.json', output)
    full_sync = os.environ.get('FULL_SYNC', '').strip().lower() in ('1', 'true', 'yes')
    try:
        sync_d1_database(output, tse_prices, otc_prices, full_sync=full_sync)
    except Exception as e:
        print(f'  [WARN] D1 sync failed (non-fatal): {e}')

    print(f'\n✅ twse_daily.json written:')
    print(f'   sectors={len(sectors)}, otcStocks={len(otc_stocks)}, chips={len(chips)}, bwibbu={len(bwibbu)}')
    print(f'   revenue={len(month_revenue)}, income={len(income)}, news={len(news)}')
    print(f'   gdr={len(gdr_list)}, gdr_danger={len(gdr_danger)}, borrow={len(borrow_data)}')

    # 除息日期（一次性抓全年，不受盤中/盤後限制）
    print('\n── 除息資料 ─────────────────────────────────────')
    fetch_exdiv()

if __name__ == '__main__':
    main()
