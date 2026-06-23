#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
除息前布局篩選器 v2 — 使用 TWSE/TPEX 公開 API（不再依賴 Goodinfo，GitHub Actions 可正常執行）
執行: python scripts/exdiv_screener.py

【篩選條件】
  1. 近1年現金殖利率 >= MIN_YIELD（來自 TWSE/TPEX BWIBBU 批次）
  2. PE < MAX_PE（同上）
  3. PB < MAX_PB（同上）
  4. 連續配息年數 >= MIN_YEARS_DIV（來自 TWSE 配息歷史）
  5. 現金股利近3年遞增（REQUIRE_DIV_INCREASE）
  6. 最新季 EPS 年增率 > MIN_EPS_YOY（來自 TWSE 財報 API）
  7. 近3年 OP 增率皆 > MIN_OP_GROWTH_3Y（同上）

  ※ 填息率/填息天數：TWSE 無免費資料，以「連續配息 >= 5 年」作為代理指標
"""

import json, re, sys, time
import urllib.request
from datetime import date, timedelta, datetime
from pathlib import Path

# ─── 篩選條件 ──────────────────────────────────────────────────────────────────
MIN_YIELD         = 5.0   # 近1年現金殖利率下限(%)
MIN_YEARS_DIV     = 3     # 最少連續配息年數
REQUIRE_OP_GROWTH = True  # 最新季營業利益 > 0

MAX_PE               = 20    # 本益比上限（None = 不篩，寬鬆至20以涵蓋更多）
MAX_PB               = 2.5   # 股價淨值比上限（None = 不篩）
MIN_EPS_YOY          = None  # 本季EPS年增率下限(%)（None = 不篩）
MIN_OP_GROWTH_3Y     = None  # 近3年OP年增率下限(%)（None = 不篩）
REQUIRE_DIV_INCREASE = False # 現金股利連續3年增加（暫關閉以擴大候選）
MIN_PAYOUT_RATIO     = None  # 配息率下限（None = 不篩）
PROXY_FILL_YEARS     = 5     # 連續配息年數 >= N 視為填息能力佳

# ─── 進場時機（交易日） ────────────────────────────────────────────────────────
BUY_WIN_MAX_TD = 10
BUY_WIN_MIN_TD = 3

# ─── 其他設定 ──────────────────────────────────────────────────────────────────
DAYS_MIN   = 0
DAYS_MAX   = 60
MAX_STOCKS = 100   # 觀察名單最多查幾支（來自 twse_daily.json）
# ──────────────────────────────────────────────────────────────────────────────

BASE_DIR       = Path(__file__).parent.parent
DAILY_JSON     = BASE_DIR / "twse_daily.json"
EXDIV_JSON     = BASE_DIR / "exdiv_moneydj.json"
EXDIV_FALLBACK = BASE_DIR / "exdiv_2026.json"
NAMES_JSON     = BASE_DIR / "stock_names.json"

TODAY = date.today()


# ════════════════════════════════════════════════════════════════════════════════
# 工具函式
# ════════════════════════════════════════════════════════════════════════════════

def _http_get(url: str, timeout: int = 15, retries: int = 2) -> bytes | None:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json, text/html, */*',
        'Accept-Language': 'zh-TW,zh;q=0.9',
    }
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
            else:
                return None
    return None


def trading_days_until(ex_date: date) -> int:
    if ex_date <= TODAY:
        return 0
    count = 0
    d = TODAY + timedelta(days=1)
    while d <= ex_date:
        if d.weekday() < 5:
            count += 1
        d += timedelta(days=1)
    return count


def load_names() -> dict:
    try:
        return json.load(open(NAMES_JSON, encoding='utf-8'))
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════════════════════════
# TWSE / TPEX 公開 API
# ════════════════════════════════════════════════════════════════════════════════

def fetch_twse_bwibbu(target_date: date | None = None) -> dict:
    """
    抓 TWSE 上市股票本益比殖利率表（BWIBBU_d）。
    回傳 {code: {pe, pb, yield_div, name}}
    """
    d = target_date or TODAY
    date_str = d.strftime('%Y%m%d')
    url = (f"https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d"
           f"?response=json&date={date_str}&selectType=ALL")
    raw = _http_get(url)
    result = {}
    if not raw:
        return result
    try:
        j = json.loads(raw.decode('utf-8'))
        if j.get('stat') not in ('OK', '成功'):
            return result
        fields = j.get('fields', [])
        # fields 通常: ['代號','名稱','殖利率(%)','股利年度','本益比','股價淨值比','財報年/季']
        code_i  = next((i for i, f in enumerate(fields) if '代號' in f or f == '代號'), 0)
        name_i  = next((i for i, f in enumerate(fields) if '名稱' in f), 1)
        yield_i = next((i for i, f in enumerate(fields) if '殖利率' in f), 2)
        pe_i    = next((i for i, f in enumerate(fields) if '本益比' in f), 4)
        pb_i    = next((i for i, f in enumerate(fields) if '淨值' in f), 5)
        for row in j.get('data', []):
            try:
                code = str(row[code_i]).strip()
                if not re.match(r'^\d{4,6}$', code):
                    continue
                def _f(v):
                    try:
                        val = float(str(v).replace(',', '').strip())
                        return val if val > 0 else None
                    except Exception:
                        return None
                result[code] = {
                    'name':      str(row[name_i]).strip(),
                    'yield_div': _f(row[yield_i]),
                    'pe':        _f(row[pe_i]),
                    'pb':        _f(row[pb_i]),
                }
            except Exception:
                continue
    except Exception:
        pass
    return result


def fetch_tpex_peratio(target_date: date | None = None) -> dict:
    """
    抓 TPEX 上櫃股票本益比殖利率表。
    回傳 {code: {pe, pb, yield_div, name}}
    """
    d = target_date or TODAY
    roc_year = d.year - 1911
    date_str = f"{roc_year}/{d.month:02d}/{d.day:02d}"
    url = (f"https://www.tpex.org.tw/web/stock/aftertrading/peratio_step2/pera_result.php"
           f"?l=zh-tw&o=json&d={date_str}&s=0,asc,0")
    raw = _http_get(url)
    result = {}
    if not raw:
        return result
    try:
        j = json.loads(raw.decode('utf-8'))
        # TPEX returns 'aaData' list; columns: 代號,名稱,本益比,殖利率(%),股價淨值比,...
        for row in j.get('aaData', []):
            try:
                code = str(row[0]).strip()
                if not re.match(r'^\d{4,6}$', code):
                    continue
                def _f(v):
                    try:
                        val = float(str(v).replace(',', '').strip())
                        return val if val > 0 else None
                    except Exception:
                        return None
                result[code] = {
                    'name':      str(row[1]).strip(),
                    'pe':        _f(row[2]),
                    'yield_div': _f(row[3]),
                    'pb':        _f(row[4]),
                }
            except Exception:
                continue
    except Exception:
        pass
    return result


def fetch_dividend_history(code: str) -> dict:
    """
    從 TWSE 配息搜尋 API 取得個股近6年配息記錄。
    回傳 {consecutive_years, div_by_year, div_increasing_3y}
    """
    start = f"{TODAY.year - 6}0101"
    end   = f"{TODAY.year + 1}1231"
    url = (f"https://www.twse.com.tw/rwd/zh/company/dividendSearch"
           f"?response=json&stock_no={code}&strDate={start}&endDate={end}")
    raw = _http_get(url, timeout=10)

    if not raw:
        return {}
    try:
        j = json.loads(raw.decode('utf-8'))
        if j.get('stat') not in ('OK', '成功'):
            # TPEX stocks may not be in TWSE — that's OK, return empty
            return {}
        fields = j.get('fields', [])
        data   = j.get('data',   [])
        if not data:
            return {}

        # 找欄位索引
        year_i  = next((i for i, f in enumerate(fields) if '年度' in f or '年份' in f), None)
        cash_i  = next((i for i, f in enumerate(fields)
                        if ('現金' in f and '股利' in f) or '現金股利' in f), None)

        if year_i is None or cash_i is None:
            # Fallback: guess by position
            year_i = 0
            cash_i = 2

        by_year: dict[int, float] = {}
        for row in data:
            try:
                yr_raw = str(row[year_i]).replace('/', '').strip()
                # 民國年或西元年
                yr = int(yr_raw[:4]) if len(yr_raw) >= 4 else int(yr_raw)
                if yr < 100:
                    yr += 1911  # 民國年轉西元
                cash = float(str(row[cash_i]).replace(',', '').strip() or '0')
                if cash > 0:
                    by_year[yr] = by_year.get(yr, 0) + cash
            except Exception:
                continue

        if not by_year:
            return {}

        sorted_years = sorted(by_year.keys(), reverse=True)

        # 連續配息年數（從今年往前算）
        consecutive = 0
        for y in range(TODAY.year, TODAY.year - 10, -1):
            if by_year.get(y, 0) > 0:
                consecutive += 1
            else:
                break

        # 近3年股利（newest first）
        div_by_year = [{'year': y, 'cashDiv': by_year[y]} for y in sorted_years[:6]]

        # 股利連增3年
        top3 = [by_year.get(y, 0) for y in sorted_years[:3]]
        div_increasing = (len(top3) == 3 and top3[0] >= top3[1] >= top3[2]
                          and top3[2] > 0)

        return {
            'consecutive_years':  consecutive,
            'div_by_year':        div_by_year,
            'div_increasing_3y':  div_increasing,
        }
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════════════════════════
# TWSE 財報 API（EPS / OP，已有的邏輯保留）
# ════════════════════════════════════════════════════════════════════════════════

def fetch_prev_year_eps(code: str, roc_year: str, quarter: str) -> float | None:
    prev_roc = int(roc_year) - 1
    url = (f"https://www.twse.com.tw/rwd/zh/finance/t163sb01"
           f"?response=json&co_id={code}&year={prev_roc}&season={quarter}")
    raw = _http_get(url)
    if not raw:
        return None
    try:
        j = json.loads(raw.decode('utf-8'))
        if j.get('stat') not in ('OK', '成功'):
            return None
        fields = j.get('fields', [])
        data   = j.get('data', [])
        eps_idx = next((i for i, f in enumerate(fields) if '每股' in f and '盈餘' in f), None)
        if eps_idx is None or not data:
            return None
        return float(str(data[0][eps_idx]).replace(',', '').strip())
    except Exception:
        return None


def fetch_op_3y_growth(code: str, roc_year: str, quarter: str) -> list | None:
    roc = int(roc_year)
    q   = int(quarter)
    ops = []
    for y in [roc, roc - 1, roc - 2, roc - 3]:
        url = (f"https://www.twse.com.tw/rwd/zh/finance/t163sb01"
               f"?response=json&co_id={code}&year={y}&season={q}")
        raw = _http_get(url)
        if not raw:
            ops.append(None)
            continue
        try:
            j = json.loads(raw.decode('utf-8'))
            if j.get('stat') not in ('OK', '成功'):
                ops.append(None)
                continue
            fields = j.get('fields', [])
            data   = j.get('data', [])
            op_idx = next((i for i, f in enumerate(fields) if '營業利益' in f), None)
            if op_idx is None or not data:
                ops.append(None)
                continue
            ops.append(float(str(data[0][op_idx]).replace(',', '')))
        except Exception:
            ops.append(None)

    valid = [x for x in ops if x is not None]
    if len(valid) < 3:
        return None
    growth = []
    for i in range(len(ops) - 1):
        curr, prev = ops[i], ops[i + 1]
        if curr is None or prev is None or prev == 0:
            growth.append(None)
        else:
            growth.append((curr - prev) / abs(prev) * 100)
    return growth[:3]


# ════════════════════════════════════════════════════════════════════════════════
# 除息清單 & 候選股
# ════════════════════════════════════════════════════════════════════════════════

def load_exdiv_map() -> dict:
    result = {}
    try:
        mj = json.load(open(EXDIV_JSON, encoding='utf-8'))
        for r in mj.get('records', []):
            code  = r.get('code', '')
            d_str = r.get('ex_div_date', '')
            if not re.match(r'^\d{4,6}$', code):
                continue
            try:
                parts = d_str.split('/')
                result[code] = {
                    'date':           date(int(parts[0]), int(parts[1]), int(parts[2])),
                    'cash_div_total': r.get('cash_div_total', 0),
                }
            except Exception:
                pass
        if result:
            print(f"  ✅ MoneyDJ exdiv_moneydj.json：{len(result)} 筆")
            return result
    except Exception:
        pass
    # fallback
    try:
        raw = json.load(open(EXDIV_FALLBACK, encoding='utf-8'))
        for code, mdd in raw.items():
            if not re.match(r'^\d{4,6}$', str(code)):
                continue
            try:
                mo, dy = mdd.split('/')
                result[code] = {'date': date(2026, int(mo), int(dy)), 'cash_div_total': 0}
            except Exception:
                pass
        print(f"  ⚠️ fallback exdiv_2026.json：{len(result)} 筆")
    except Exception as e:
        print(f"  [ERROR] 除息清單載入失敗: {e}")
    return result


def load_candidates() -> list:
    try:
        daily = json.load(open(DAILY_JSON, encoding='utf-8'))
    except Exception:
        return []
    income = daily.get('income', {})
    candidates = []
    for code, inc in income.items():
        if not re.match(r'^\d{4,6}$', str(code)):
            continue
        op = inc.get('operatingIncome')
        if REQUIRE_OP_GROWTH and (op is None or op <= 0):
            continue
        candidates.append({
            'code':            code,
            'operatingIncome': op or 0,
            'eps':             inc.get('eps'),
            'roc_year':        inc.get('year', ''),
            'quarter':         inc.get('quarter', ''),
        })
    candidates.sort(key=lambda x: x['operatingIncome'], reverse=True)
    return candidates[:MAX_STOCKS]


# ════════════════════════════════════════════════════════════════════════════════
# 個股基本面抓取（TWSE-based，取代 Goodinfo）
# ════════════════════════════════════════════════════════════════════════════════

def fetch_stock_fundamentals(
    code: str,
    market_data: dict,    # {code: {pe, pb, yield_div, name}} from BWIBBU + TPEX
    prev_eps: float | None = None,
    op_3y_growth: list | None = None,
    candidate_eps: float | None = None,
    candidate_roc_year: str = '',
    candidate_quarter: str = '',
) -> dict:
    result = {'code': code}

    # ── 市場批次資料（PE / PB / 殖利率）──
    mkt = market_data.get(code, {})
    result['name']      = mkt.get('name', '')
    result['pe']        = mkt.get('pe')
    result['pb']        = mkt.get('pb')
    result['yield3y']   = mkt.get('yield_div')   # 近1年殖利率作為代理

    # ── 配息歷史（TWSE per-stock API）──
    div = fetch_dividend_history(code)
    result['consecutiveYears']  = div.get('consecutive_years', 0)
    result['divByYear']         = div.get('div_by_year', [])
    result['divIncreasing3y']   = div.get('div_increasing_3y')

    # ── 填息代理指標（連續配息 >= PROXY_FILL_YEARS）──
    result['fillRate'] = None   # 無法從 TWSE 取得，設 None
    result['fillDays'] = None
    result['fill_proxy_ok'] = (result['consecutiveYears'] >= PROXY_FILL_YEARS)

    # ── EPS / OP 注入 ──
    result['prev_eps']     = prev_eps
    result['op_3y_growth'] = op_3y_growth
    result['eps']          = candidate_eps

    # ── 配息率概算 ──
    if candidate_eps and candidate_eps > 0:
        divs = result['divByYear']
        if divs:
            latest_cash = divs[0].get('cashDiv', 0)
            if latest_cash > 0:
                result['payout_approx'] = round(latest_cash / candidate_eps * 100, 1)

    return result


# ════════════════════════════════════════════════════════════════════════════════
# 篩選條件
# ════════════════════════════════════════════════════════════════════════════════

def passes_criteria(r: dict) -> tuple[bool, list[str]]:
    fails = []

    # 資料門檻：PE/PB/yield 和配息歷史全部 None → 資料不足
    if (r.get('pe') is None and r.get('pb') is None
            and r.get('yield3y') is None and r.get('consecutiveYears', 0) == 0):
        fails.append("TWSE資料不足")
        return False, fails

    # 1. 殖利率
    y = r.get('yield3y')
    if y is not None and y < MIN_YIELD:
        fails.append(f"殖利率{y:.1f}%<{MIN_YIELD}%")

    # 2. 連續配息年數
    cy = r.get('consecutiveYears', 0)
    if cy < MIN_YEARS_DIV:
        fails.append(f"連續配息{cy}年<{MIN_YEARS_DIV}年")

    # 3. PE / PB（OR 邏輯：至少一個達標）
    if MAX_PE is not None or MAX_PB is not None:
        pe = r.get('pe')
        pb = r.get('pb')
        pe_ok = (MAX_PE is None) or (pe is None) or (pe <= MAX_PE)
        pb_ok = (MAX_PB is None) or (pb is None) or (pb <= MAX_PB)
        if pe is not None and pb is not None:
            if not (pe_ok or pb_ok):
                fails.append(f"估值過高:PE{pe:.1f}>{MAX_PE}且PB{pb:.1f}>{MAX_PB}")
        elif pe is not None and not pe_ok:
            fails.append(f"PE{pe:.1f}>{MAX_PE}")
        elif pb is not None and not pb_ok:
            fails.append(f"PB{pb:.1f}>{MAX_PB}")

    # 4. EPS YoY OR OP 3年增率
    if MIN_EPS_YOY is not None or MIN_OP_GROWTH_3Y is not None:
        eps_yoy_pass = False
        op3y_pass    = False

        if MIN_EPS_YOY is not None:
            curr_eps = r.get('eps')
            prev_eps = r.get('prev_eps')
            if curr_eps is not None and prev_eps is not None and prev_eps != 0:
                yoy = (curr_eps - prev_eps) / abs(prev_eps) * 100
                r['eps_yoy'] = round(yoy, 1)
                eps_yoy_pass = (yoy >= MIN_EPS_YOY)

        if MIN_OP_GROWTH_3Y is not None:
            op_3y = r.get('op_3y_growth')
            if op_3y:
                valid = [g for g in op_3y if g is not None]
                if valid:
                    op3y_pass = all(g >= MIN_OP_GROWTH_3Y for g in valid)

        if not (eps_yoy_pass or op3y_pass):
            if MIN_EPS_YOY is not None or MIN_OP_GROWTH_3Y is not None:
                fails.append("EPS/OP成長動能不足")

    # 5. 股利連增3年（可選）
    if REQUIRE_DIV_INCREASE:
        inc3 = r.get('divIncreasing3y')
        if inc3 is not None and not inc3:
            fails.append("股利未連增3年")

    return (len(fails) == 0), fails


# ════════════════════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════════════════════

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print(f"\n{'='*65}")
    print(f"除息前布局篩選 v2 (TWSE API)  執行日期：{TODAY}")
    print(f"殖利率>={MIN_YIELD}%  連配>={MIN_YEARS_DIV}年  PE<{MAX_PE}  PB<{MAX_PB}")
    print(f"進場窗口：除息前 T-{BUY_WIN_MAX_TD} 至 T-{BUY_WIN_MIN_TD} 個交易日")
    print(f"{'='*65}\n")

    stock_names = load_names()

    # ── Step 1: 除息清單 ─────────────────────────────────────────────────────
    print("── 載入除息清單...")
    exdiv_map = load_exdiv_map()   # {code: {date, cash_div_total}}
    win_lo = TODAY + timedelta(days=DAYS_MIN)
    win_hi = TODAY + timedelta(days=DAYS_MAX)
    window_items = {
        code: info for code, info in exdiv_map.items()
        if win_lo <= info['date'] <= win_hi
    }
    print(f"  除息窗口內（{win_lo}~{win_hi}）：{len(window_items)} 支")

    buy_window_codes = {}
    for code, info in window_items.items():
        td = trading_days_until(info['date'])
        if BUY_WIN_MIN_TD <= td <= BUY_WIN_MAX_TD:
            buy_window_codes[code] = info['date']
    print(f"  進場窗口（T-{BUY_WIN_MAX_TD}~T-{BUY_WIN_MIN_TD}）：{len(buy_window_codes)} 支")

    # ── Step 2: 抓 TWSE/TPEX 批次 PE/PB/殖利率 ──────────────────────────────
    print("\n── 抓取市場批次資料（TWSE + TPEX）...")
    market_data = {}

    # 嘗試當天，若失敗往前找最近交易日
    for delta in range(0, 7):
        target = TODAY - timedelta(days=delta)
        if target.weekday() >= 5:
            continue
        twse = fetch_twse_bwibbu(target)
        if twse:
            market_data.update(twse)
            print(f"  ✅ TWSE BWIBBU：{len(twse)} 筆（{target}）")
            break
    else:
        print("  ⚠️ TWSE BWIBBU 抓取失敗")

    for delta in range(0, 7):
        target = TODAY - timedelta(days=delta)
        if target.weekday() >= 5:
            continue
        tpex = fetch_tpex_peratio(target)
        if tpex:
            market_data.update(tpex)
            print(f"  ✅ TPEX 本益比：{len(tpex)} 筆（{target}）")
            break
    else:
        print("  ⚠️ TPEX 抓取失敗")

    # ── Step 3: 候選股（來自 twse_daily.json）────────────────────────────────
    candidates = load_candidates()
    cand_map = {c['code']: c for c in candidates}
    watch_candidates = [c for c in candidates if c['code'] not in window_items][:MAX_STOCKS]
    print(f"\n第一層過濾（OP>0）：{len(candidates)} 支候選，其中非窗口 {len(watch_candidates)} 支觀察")

    qualified_window = []
    qualified_watch  = []
    results_all      = []

    # ── Pass A: 窗口股 ───────────────────────────────────────────────────────
    win_list = sorted(window_items.items(), key=lambda x: x[1]['date'])
    print(f"\n── Pass A：查詢除息窗口股 {len(win_list)} 支（TWSE 配息歷史）...\n")

    for i, (code, info) in enumerate(win_list):
        ex_dt    = info['date']
        sys.stdout.write(f"\r  [{i+1}/{len(win_list)}] {code}  ")
        sys.stdout.flush()

        cand     = cand_map.get(code, {})
        roc_year = cand.get('roc_year', '')
        quarter  = cand.get('quarter', '')

        prev_eps = None
        if MIN_EPS_YOY is not None and roc_year and quarter:
            prev_eps = fetch_prev_year_eps(code, roc_year, quarter)
        op_3y = None
        if MIN_OP_GROWTH_3Y is not None and roc_year and quarter:
            op_3y = fetch_op_3y_growth(code, roc_year, quarter)

        r = fetch_stock_fundamentals(
            code, market_data,
            prev_eps=prev_eps, op_3y_growth=op_3y,
            candidate_eps=cand.get('eps'),
            candidate_roc_year=roc_year, candidate_quarter=quarter,
        )
        r['exDate']          = str(ex_dt)
        r['ex_days']         = (ex_dt - TODAY).days
        r['trading_days']    = trading_days_until(ex_dt)
        r['operatingIncome'] = cand.get('operatingIncome')
        r['cash_div_total']  = info.get('cash_div_total', 0)
        results_all.append(r)

        passes, fails = passes_criteria(r)
        in_buy_win = code in buy_window_codes
        if passes:
            qualified_window.append({**r, 'in_window': True, 'in_buy_window': in_buy_win})
        else:
            sys.stdout.write(f"\n    ✗ {code} 未過：{', '.join(fails)}\n")
            sys.stdout.flush()

        time.sleep(0.3)   # TWSE 頻率限制保護

    print()

    # ── Pass B: 觀察名單 ─────────────────────────────────────────────────────
    if watch_candidates:
        print(f"\n── Pass B：查詢觀察名單 {len(watch_candidates)} 支...\n")
        for i, cand in enumerate(watch_candidates):
            code     = cand['code']
            roc_year = cand.get('roc_year', '')
            quarter  = cand.get('quarter', '')
            sys.stdout.write(f"\r  [{i+1}/{len(watch_candidates)}] {code}  ")
            sys.stdout.flush()

            prev_eps = None
            if MIN_EPS_YOY is not None and roc_year and quarter:
                prev_eps = fetch_prev_year_eps(code, roc_year, quarter)
            op_3y = None
            if MIN_OP_GROWTH_3Y is not None and roc_year and quarter:
                op_3y = fetch_op_3y_growth(code, roc_year, quarter)

            r = fetch_stock_fundamentals(
                code, market_data,
                prev_eps=prev_eps, op_3y_growth=op_3y,
                candidate_eps=cand.get('eps'),
            )
            r['operatingIncome'] = cand.get('operatingIncome')
            results_all.append(r)

            passes, fails = passes_criteria(r)
            if passes:
                ex_days = r.get('ex_days')
                in_win  = ex_days is not None and DAYS_MIN <= ex_days <= DAYS_MAX
                td      = r.get('trading_days', 0) or 0
                in_buy_win = in_win and (BUY_WIN_MIN_TD <= td <= BUY_WIN_MAX_TD)
                entry = {**r, 'in_window': in_win, 'in_buy_window': in_buy_win}
                if in_win:
                    qualified_window.append(entry)
                else:
                    qualified_watch.append(entry)

            time.sleep(0.3)

        print()

    # ── 輸出結果 ──────────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print(f"✅ 符合除息窗口 + 全部條件：{len(qualified_window)} 支")
    buy_stocks = [q for q in qualified_window if q.get('in_buy_window')]
    print(f"   其中進場窗口（T-{BUY_WIN_MAX_TD}~T-{BUY_WIN_MIN_TD}）：{len(buy_stocks)} 支")
    print(f"📋 觀察名單（未公告除息）：{len(qualified_watch)} 支")
    print(f"{'='*65}\n")

    def print_stock(q, show_buy=True):
        code    = q['code']
        name    = q.get('name') or stock_names.get(code, '')
        ex_info = (f"除息 {q.get('exDate','未公告')} "
                   f"(T-{q.get('trading_days','?')}交易日/{q.get('ex_days','?')}天後)"
                   if q.get('exDate') else "除息日未公告")
        flag = " 🔔進場" if (show_buy and q.get('in_buy_window')) else ""
        y3   = q.get('yield3y')
        cyrs = q.get('consecutiveYears', '?')
        pe   = q.get('pe', 'N/A')
        pb   = q.get('pb', 'N/A')
        cd   = q.get('cash_div_total', 0)
        fp   = "✅" if q.get('fill_proxy_ok') else "—"
        print(f"\n  ▶ {code} {name}  —  {ex_info}{flag}")
        print(f"    殖利率:{f'{y3:.1f}%' if y3 else '?'}  PE:{pe}  PB:{pb}  "
              f"連配:{cyrs}年  填息代理:{fp}  現金股利:{cd}")
        divs = q.get('divByYear', [])[:4]
        if divs:
            div_str = "  ".join(f"{d['year']}:{d['cashDiv']}" for d in divs)
            print(f"    股利歷史  {div_str}")

    if buy_stocks:
        print(f"【🔔 立即進場候選（T-{BUY_WIN_MAX_TD}~T-{BUY_WIN_MIN_TD}交易日前）】")
        for q in sorted(buy_stocks, key=lambda x: x.get('trading_days', 99)):
            print_stock(q)

    other = [q for q in qualified_window if not q.get('in_buy_window')]
    if other:
        print(f"\n【除息窗口內但尚未進場時機】")
        for q in sorted(other, key=lambda x: x.get('ex_days', 99)):
            print_stock(q, show_buy=False)

    if qualified_watch:
        print(f"\n【觀察名單 — 等待除息公告】（Top {min(20, len(qualified_watch))}）")
        for q in sorted(qualified_watch, key=lambda x: -(x.get('yield3y') or 0))[:20]:
            print_stock(q, show_buy=False)

    if not qualified_window and not qualified_watch:
        print("本次掃描無符合條件股票。")

    # ── 存檔 ──────────────────────────────────────────────────────────────────
    out_path = BASE_DIR / "exdiv_screener_results.json"
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'run_date': str(TODAY),
            'criteria': {
                'min_yield':           MIN_YIELD,
                'min_years_div':       MIN_YEARS_DIV,
                'max_pe':              MAX_PE,
                'max_pb':              MAX_PB,
                'min_eps_yoy':         MIN_EPS_YOY,
                'min_op_growth_3y':    MIN_OP_GROWTH_3Y,
                'require_div_increase':REQUIRE_DIV_INCREASE,
                'min_payout_ratio':    MIN_PAYOUT_RATIO,
                'buy_window_td':       [BUY_WIN_MIN_TD, BUY_WIN_MAX_TD],
                'fill_proxy_years':    PROXY_FILL_YEARS,
            },
            'qualified_window': qualified_window,
            'qualified_watch':  qualified_watch,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n結果已存至 {out_path}")


if __name__ == '__main__':
    main()
