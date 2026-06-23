#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
除息前布局篩選器 — 光速填息股篩選
執行: python scripts/exdiv_screener.py

【必備條件】
  1. 近3年皆配息
  2. 填息成功率 >= MIN_FILL_RATE（預設 80%，可放寬至 40%）
  3. 平均填息天數 <= MAX_FILL_DAYS（預設 60天）
  4. 近3年平均現金殖利率 >= MIN_YIELD（預設 5%）
  5. 最新季營業利益成長率 > 0

【進階選配】
  6.  PE < MAX_PE（本益比上限）
  7.  PB < MAX_PB（股價淨值比上限）
  8.  本季 EPS 年增率 > MIN_EPS_YOY（預設 10%）
  9.  近3年營業利益年增率皆 > MIN_OP_GROWTH_3Y（預設 5%）
  10. 現金股利連續3年增加（REQUIRE_DIV_INCREASE）
  11. 近5年配息率 >= MIN_PAYOUT_RATIO 的年數 >= MIN_PAYOUT_YEARS（3/5年）

【進場時機】
  除息日前 BUY_WIN_MIN_TD～BUY_WIN_MAX_TD 個交易日（T-10至T-3）
"""

import asyncio, json, re, sys, time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

# ─── 必備條件 ──────────────────────────────────────────────────────────────────
MIN_FILL_RATE     = 60    # 填息率(%)，可放寬至 40
MAX_FILL_DAYS     = 120   # 最大平均填息天數（60=保守，120=常見，180=寬鬆）
MIN_YIELD         = 5.0   # 近3年平均現金殖利率(%)
MIN_YEARS_DIV     = 3     # 最少連續配息年數
REQUIRE_OP_GROWTH = True  # 最新季營業利益成長率 > 0

# ─── 進階選配 ──────────────────────────────────────────────────────────────────
MAX_PE               = 15    # 本益比上限（None = 不篩）
MAX_PB               = 1.5   # 股價淨值比上限（None = 不篩）
MIN_EPS_YOY          = 10.0  # 本季EPS年增率下限(%)（None = 不篩）
MIN_OP_GROWTH_3Y     = 5.0   # 近3年OP年增率皆>下限(%)（None = 不篩）
REQUIRE_DIV_INCREASE = True  # 現金股利連續3年增加（True = 篩）
MIN_PAYOUT_RATIO     = 70.0  # 配息率下限(%)
MIN_PAYOUT_YEARS     = 3     # 近5年中配息率達標的最低年數

# ─── 進場時機（交易日） ────────────────────────────────────────────────────────
BUY_WIN_MAX_TD = 10   # 除息前最多 N 個交易日進場（T-10）
BUY_WIN_MIN_TD = 3    # 除息前最少 N 個交易日進場（T-3）

# ─── 其他設定 ──────────────────────────────────────────────────────────────────
DAYS_MIN    = 0     # 距除息日最少天數（廣篩用，0 = 今天也算）
DAYS_MAX    = 60    # 距除息日最多天數
MAX_STOCKS  = 100   # 觀察名單最多查幾支
# ──────────────────────────────────────────────────────────────────────────────

BASE_DIR        = Path(__file__).parent.parent
DAILY_JSON      = BASE_DIR / "twse_daily.json"
TSE_JSON        = BASE_DIR / "stocks_tse.json"
OTC_JSON        = BASE_DIR / "stocks_otc.json"
EXDIV_JSON      = BASE_DIR / "exdiv_moneydj.json"   # 改用 MoneyDJ 資料（更完整）
EXDIV_FALLBACK  = BASE_DIR / "exdiv_2026.json"
NAMES_JSON      = BASE_DIR / "stock_names.json"


# ════════════════════════════════════════════════════════════════════════════════
# 工具函式
# ════════════════════════════════════════════════════════════════════════════════

def load_names() -> dict:
    try:
        return json.load(open(NAMES_JSON, encoding='utf-8'))
    except Exception:
        return {}


def trading_days_until(ex_date: date) -> int:
    """計算今天到除息日的交易日數（近似：排除週六日，不含台灣假日）"""
    today = date.today()
    if ex_date <= today:
        return 0
    count = 0
    d = today + timedelta(days=1)
    while d <= ex_date:
        if d.weekday() < 5:   # 0=Mon … 4=Fri
            count += 1
        d += timedelta(days=1)
    return count


def _http_get(url: str, timeout: int = 12) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://www.twse.com.tw/',
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def fetch_prev_year_eps(code: str, roc_year: str, quarter: str) -> float | None:
    """
    從 TWSE 季報 API 抓前一年同季 EPS。
    roc_year: '115'（民國年）, quarter: '1'~'4'
    """
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
        # 找 "每股盈餘" 欄位索引
        eps_idx = next((i for i, f in enumerate(fields) if '每股' in f and '盈餘' in f), None)
        if eps_idx is None or not data:
            return None
        for row in data:
            val = str(row[eps_idx]).replace(',', '').strip()
            f = float(val)
            return f
    except Exception:
        return None


def fetch_op_3y_growth(code: str, roc_year: str, quarter: str) -> list[float] | None:
    """
    從 TWSE 抓近3年同季營業利益，計算年增率列表（最新→最舊）。
    回傳 [yoy_curr, yoy_prev] 或 None（資料不足）。
    """
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
            val = float(str(data[0][op_idx]).replace(',', ''))
            ops.append(val)
        except Exception:
            ops.append(None)

    # 計算年增率（需要4個點才能得到3個增率）
    if len([x for x in ops if x is not None]) < 3:
        return None
    growth_rates = []
    for i in range(len(ops) - 1):
        curr, prev = ops[i], ops[i + 1]
        if curr is None or prev is None or prev == 0:
            growth_rates.append(None)
        else:
            growth_rates.append((curr - prev) / abs(prev) * 100)
    # 回傳最新的3個增率（recent→older）
    return growth_rates[:3]


# ════════════════════════════════════════════════════════════════════════════════
# 除息清單載入（優先 exdiv_moneydj.json）
# ════════════════════════════════════════════════════════════════════════════════

def load_exdiv_map() -> dict[str, date]:
    """回傳 {code: date_obj} 的除息清單"""
    result: dict[str, date] = {}

    # ── 優先：MoneyDJ 完整資料 ──────────────────────────────────────────────
    try:
        mj = json.load(open(EXDIV_JSON, encoding='utf-8'))
        for r in mj.get('records', []):
            code = r.get('code', '')
            d_str = r.get('ex_div_date', '')
            if not re.match(r'^\d{4,6}$', code):
                continue
            try:
                parts = d_str.split('/')
                result[code] = date(int(parts[0]), int(parts[1]), int(parts[2]))
            except Exception:
                pass
        if result:
            print(f"  ✅ MoneyDJ exdiv_moneydj.json：{len(result)} 筆")
            return result
    except Exception:
        pass

    # ── Fallback：舊 exdiv_2026.json ────────────────────────────────────────
    try:
        raw = json.load(open(EXDIV_FALLBACK, encoding='utf-8'))
        for code, mdd in raw.items():
            if not re.match(r'^\d{4,6}$', str(code)):
                continue
            try:
                mo, dy = mdd.split('/')
                result[code] = date(2026, int(mo), int(dy))
            except Exception:
                pass
        print(f"  ⚠️ 使用 fallback exdiv_2026.json：{len(result)} 筆")
    except Exception as e:
        print(f"  [ERROR] 除息清單載入失敗: {e}")
    return result


# ════════════════════════════════════════════════════════════════════════════════
# 候選股（從 twse_daily.json income 過濾）
# ════════════════════════════════════════════════════════════════════════════════

def load_candidates() -> list[dict]:
    try:
        daily = json.load(open(DAILY_JSON, encoding="utf-8"))
    except Exception as e:
        print(f"[ERROR] 無法讀取 twse_daily.json: {e}")
        return []

    income = daily.get("income", {})
    if not income:
        print("[WARN] income 資料為空")
        return []

    candidates = []
    for code, inc in income.items():
        if not re.match(r"^\d{4,6}$", str(code)):
            continue
        op = inc.get("operatingIncome")
        if REQUIRE_OP_GROWTH and (op is None or op <= 0):
            continue
        candidates.append({
            "code":            code,
            "operatingIncome": op or 0,
            "eps":             inc.get("eps"),
            "roc_year":        inc.get("year", ""),
            "quarter":         inc.get("quarter", ""),
        })

    candidates.sort(key=lambda x: x["operatingIncome"], reverse=True)
    return candidates[:MAX_STOCKS]


# ════════════════════════════════════════════════════════════════════════════════
# Goodinfo 個股頁面抓取（Playwright）
# ════════════════════════════════════════════════════════════════════════════════

_debug_printed: set = set()

GOODINFO_JS = """
() => {
    const text = document.body.innerText;
    const rows = text.split('\\n').map(l => l.trim()).filter(Boolean);
    const tables = document.querySelectorAll('table');

    // ── 名稱 ──
    let name = '';
    for (const r of rows.slice(0, 20)) {
        const m = r.match(/^(\\d{4,6})\\s+(.+?)\\s*[－\\-]/);
        if (m) { name = m[2].trim(); break; }
    }

    // ── PE / PB（從股價表 table[5] DOM 讀取，更穩定）──
    let pe = null, pb = null;
    try {
        const t5rows = Array.from(tables[5]?.querySelectorAll('tr') || []);
        // Row 2: "...PBR PER PEG", Row 3: actual values
        const valRow = t5rows.find((r, i) => {
            const prev = t5rows[i-1];
            return prev && prev.innerText.includes('PBR') && prev.innerText.includes('PER');
        });
        if (valRow) {
            const cells = Array.from(valRow.querySelectorAll('td,th')).map(c => c.innerText.trim());
            // cells[5]=PBR, cells[6]=PER
            const pbVal = parseFloat(cells[5]);
            const peVal = parseFloat(cells[6]);
            if (!isNaN(pbVal) && pbVal > 0) pb = pbVal;
            if (!isNaN(peVal) && peVal > 0) pe = peVal;
        }
    } catch(e) {}
    // fallback regex
    if (!pe) { const m = text.match(/本益比[^\\d]*([\\d.]+)/) || text.match(/PER[^\\d]*([\\d.]+)/i); if (m) pe = parseFloat(m[1]) || null; }
    if (!pb) { const m = text.match(/股價淨值比[^\\d]*([\\d.]+)/) || text.match(/PBR[^\\d]*([\\d.]+)/i); if (m) pb = parseFloat(m[1]) || null; }

    // ── 連續配息年數 ──
    let consecutiveYears = 0;
    const consM = text.match(/連續\\s*(\\d+)\\s*年.*?配.*?(?:現金)?股利/) || text.match(/已連續(\\d+)年/);
    if (consM) consecutiveYears = parseInt(consM[1]);

    // ── 年度股利資料（table[10]）──
    // cells: [0]=年度 [4]=現金股利合計 [7]=股票股利合計 [9]=填息日數
    const divByYear = [];
    let fillRate = null;
    try {
        const t10 = tables[10];
        if (t10) {
            const yearRows = Array.from(t10.querySelectorAll('tr')).filter(r => {
                const c0 = r.querySelectorAll('td,th')[0]?.innerText.trim();
                return /^20\\d{2}$/.test(c0);
            });
            let filled = 0, total = 0;
            for (const row of yearRows) {
                const cells = Array.from(row.querySelectorAll('td,th')).map(c => c.innerText.trim());
                const year      = parseInt(cells[0]);
                const cashDiv   = parseFloat(cells[4]) || 0;
                const stkDiv    = parseFloat(cells[7]) || 0;
                const fdCell    = cells[9] || '-';
                const fdVal     = parseFloat(fdCell);
                // 排除被解析成年份的數字（> 999 視為無效）
                const fdSane    = (!isNaN(fdVal) && fdVal > 0 && fdVal < 1000) ? fdVal : null;
                const hasFilled = fdSane !== null;
                if (cashDiv > 0 || stkDiv > 0) {
                    total++;
                    if (hasFilled) filled++;
                }
                divByYear.push({ year, cashDiv, stkDiv,
                    fillDaysRow: fdSane });
            }
            if (total > 0) fillRate = Math.round(filled / total * 100);
        }
    } catch(e) {}

    // ── 平均填息日數（table[9] 現金股利摘要行）──
    let fillDays = null;
    try {
        const t9 = tables[9];
        if (t9) {
            const cashRow = Array.from(t9.querySelectorAll('tr'))
                .find(r => r.innerText.includes('現金股利') && !r.innerText.includes('股票'));
            if (cashRow) {
                const cells = Array.from(cashRow.querySelectorAll('td,th')).map(c => c.innerText.trim());
                // cell[3] = 均填息日數
                const fd = parseFloat(cells[3]);
                if (!isNaN(fd) && fd > 0) fillDays = fd;
            }
        }
    } catch(e) {}
    // fallback regex
    if (!fillDays) {
        for (const p of [/均填息日數[^\\d]*(\\d+)/, /填息[^\\d]*均[^\\d]*(\\d+)/, /平均填息[^\\d]*(\\d+)/]) {
            const m = text.match(p);
            if (m) { fillDays = parseFloat(m[1]); break; }
        }
    }
    // fallback innerText fill rate
    if (fillRate === null) {
        let f2 = 0, t2 = 0;
        for (const row of rows) {
            if (!/^20(2[0-9])/.test(row) || !/%/.test(row)) continue;
            const m = row.match(/\\d{1,2}\\/\\d{2}\\s+[\\d.]+\\s+(\\d+|-)\\s+[\\d.]+%/);
            if (m) { t2++; if (m[1] !== '-') f2++; }
            if (t2 >= 5) break;
        }
        if (t2 > 0) fillRate = Math.round(f2 / t2 * 100);
    }

    // ── 近3年平均殖利率 ──
    let yield3y = null;
    for (const p of [
        /近3年平均[\\s\\S]{0,300}?殖利率[^\\d]*([\\d.]+)/,
        /平均殖利率[^\\d]*([\\d.]+)/,
        /3年均殖[^\\d]*([\\d.]+)/
    ]) {
        const m = text.match(p);
        if (m) { yield3y = parseFloat(m[1]); break; }
    }
    // fallback: 從年度行末尾取 X.XX% 平均
    if (yield3y === null) {
        const yVals = [];
        for (const row of rows) {
            if (!/^20(2[0-9])/.test(row)) continue;
            const ym = row.match(/([\\d.]+)%\\s*$/);
            if (ym) { const v = parseFloat(ym[1]); if (v > 0.3 && v < 30) yVals.push(v); }
            if (yVals.length >= 3) break;
        }
        if (yVals.length) yield3y = yVals.reduce((a,b)=>a+b,0) / yVals.length;
    }

    // ── 配息率（近5年）——從 divByYear cashDiv + 外部 eps_by_year 計算 ──
    // 這裡先把 cashDiv by year 傳出去，Python 端用 EPS 計算
    // （若未能從外部取得 EPS，Python 端會標記為「資料不足跳過」）

    // ── 現金股利連續3年增加 ──
    let divIncreasing3y = null;
    const sorted = divByYear.slice().sort((a,b) => b.year - a.year);
    if (sorted.length >= 3 && sorted.slice(0,3).every(r => r.cashDiv > 0)) {
        divIncreasing3y = sorted[0].cashDiv >= sorted[1].cashDiv &&
                          sorted[1].cashDiv >= sorted[2].cashDiv;
    }

    // ── 即將除息日（2026）──
    let exDate = null;
    for (const row of rows) {
        if (!/^2026/.test(row)) continue;
        const dm = row.match(/(\\d{1,2})\\/(\\d{2})/);
        if (dm) {
            const mo = dm[1].padStart(2,'0'), dy = dm[2];
            exDate = `2026-${mo}-${dy}`;
            break;
        }
    }

    return {
        name, pe, pb, consecutiveYears, fillDays, fillRate, yield3y, exDate,
        divByYear: sorted.slice(0, 6),   // 最新6年，newest first
        divIncreasing3y
    };
}
"""


async def scrape_goodinfo(page, code: str,
                           prev_eps: float | None = None,
                           op_3y_growth: list | None = None,
                           debug: bool = False) -> dict:
    result = {"code": code}
    url = f"https://goodinfo.tw/tw/StockDividendPolicy.asp?STOCK_ID={code}"
    try:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(1.5)
    except Exception as e:
        result["error"] = str(e)
        return result

    try:
        data = await page.evaluate(GOODINFO_JS)
        result.update(data)

        if debug and code not in _debug_printed:
            raw_text = await page.evaluate("() => document.body.innerText.slice(0, 600)")
            print(f"\n[DEBUG {code}]\n{raw_text}\n{'─'*60}")
            _debug_printed.add(code)

        # 解析除息日 → ex_days
        if data.get("exDate"):
            try:
                parts = data["exDate"].split("-")
                ex_dt = date(int(parts[0]), int(parts[1]), int(parts[2]))
                result["ex_days"]       = (ex_dt - date.today()).days
                result["trading_days"]  = trading_days_until(ex_dt)
            except Exception:
                pass

        # ── 注入 EPS YoY（Python 端傳入）──
        result["prev_eps"]      = prev_eps
        result["op_3y_growth"]  = op_3y_growth

        # ── 配息率計算（用 divByYear cashDiv + prev_eps 近似）──
        # 若有 prev_eps，用它當近期 EPS 的代理；
        # 準確版需要逐年 EPS，僅做「有資料就篩」的 best-effort
        result["payout_approx"] = None
        if prev_eps and prev_eps > 0:
            recent = data.get("divByYear", [])
            if recent:
                latest_cash = recent[0].get("cashDiv", 0)
                if latest_cash > 0:
                    result["payout_approx"] = round(latest_cash / prev_eps * 100, 1)

    except Exception as e:
        result["error"] = str(e)

    return result


# ════════════════════════════════════════════════════════════════════════════════
# 篩選條件判斷
# ════════════════════════════════════════════════════════════════════════════════

def passes_criteria(r: dict) -> tuple[bool, list[str]]:
    fails = []

    # ── 資料品質門檻：三個核心指標都是 None 視為資料不足，直接淘汰 ───────────
    fr = r.get("fillRate")
    fd = r.get("fillDays")
    y3 = r.get("yield3y")
    cy = r.get("consecutiveYears", 0)
    div_by_year = r.get("divByYear", [])
    if fr is None and fd is None and y3 is None and cy == 0 and not div_by_year:
        fails.append("Goodinfo資料不足(無股利歷史)")
        return False, fails

    # ── 必備1: 連續配息年數（0 = 無配息歷史，直接淘汰）──────────────────────
    if cy < MIN_YEARS_DIV:
        fails.append(f"連續配息{cy}年<{MIN_YEARS_DIV}年")

    # ── 必備2: 填息率 ────────────────────────────────────────────────────────
    if fr is not None and fr < MIN_FILL_RATE:
        fails.append(f"填息率{fr}%<{MIN_FILL_RATE}%")

    # ── 必備3: 平均填息天數 ──────────────────────────────────────────────────
    if fd is not None and fd > MAX_FILL_DAYS:
        fails.append(f"填息{fd:.0f}天>{MAX_FILL_DAYS}天")

    # ── 必備4: 近3年平均殖利率 ───────────────────────────────────────────────
    if y3 is not None and y3 < MIN_YIELD:
        fails.append(f"殖利率{y3:.1f}%<{MIN_YIELD}%")

    # ── 必備5: 最新季營業利益成長率 > 0（已在 load_candidates 過濾，雙保險）──
    op = r.get("operatingIncome")
    if REQUIRE_OP_GROWTH and op is not None and op <= 0:
        fails.append("營業利益≤0")

    # ── 進階A: PE < MAX_PE OR PB < MAX_PB（估值安全門檻，用 OR）─────────────
    if MAX_PE is not None or MAX_PB is not None:
        pe = r.get("pe")
        pb = r.get("pb")
        pe_ok = (MAX_PE is None) or (pe is None) or (pe <= MAX_PE)
        pb_ok = (MAX_PB is None) or (pb is None) or (pb <= MAX_PB)
        if pe is not None and pb is not None:
            # 兩者都有資料 → 至少一個達標
            if not (pe_ok or pb_ok):
                fails.append(f"估值過高:PE{pe:.1f}>{MAX_PE}且PB{pb:.1f}>{MAX_PB}")
        elif pe is not None and not pe_ok:
            fails.append(f"PE{pe:.1f}>{MAX_PE}(PB無資料)")
        elif pb is not None and not pb_ok:
            fails.append(f"PB{pb:.1f}>{MAX_PB}(PE無資料)")

    # ── 進階B: EPS年增率 > MIN_EPS_YOY OR OP3年增率皆>MIN_OP_GROWTH_3Y ───────
    eps_yoy_checked = False
    op3y_checked    = False
    eps_yoy_pass    = False
    op3y_pass       = False

    if MIN_EPS_YOY is not None:
        curr_eps = r.get("eps")
        prev_eps = r.get("prev_eps")
        if curr_eps is not None and prev_eps is not None and prev_eps != 0:
            yoy = (curr_eps - prev_eps) / abs(prev_eps) * 100
            r["eps_yoy"] = round(yoy, 1)
            eps_yoy_checked = True
            eps_yoy_pass = (yoy >= MIN_EPS_YOY)

    if MIN_OP_GROWTH_3Y is not None:
        op_3y = r.get("op_3y_growth")
        if op_3y is not None:
            valid = [g for g in op_3y if g is not None]
            if valid:
                op3y_checked = True
                op3y_pass = all(g >= MIN_OP_GROWTH_3Y for g in valid)

    if eps_yoy_checked or op3y_checked:
        if not (eps_yoy_pass or op3y_pass):
            yoy_str = f"EPS年增{r.get('eps_yoy','?')}%" if eps_yoy_checked else "EPS?%"
            op_str  = f"OP3年增不足" if op3y_checked else "OP3年?%"
            fails.append(f"爆發動能不足({yoy_str}且{op_str})")

    # ── 進階C: 現金股利連增3年 OR 配息率≥70%（3/5年）─────────────────────────
    div_inc_checked = False
    div_inc_pass    = False
    payout_checked  = False
    payout_pass     = False

    if REQUIRE_DIV_INCREASE:
        inc3 = r.get("divIncreasing3y")
        if inc3 is not None:
            div_inc_checked = True
            div_inc_pass = bool(inc3)

    if MIN_PAYOUT_RATIO is not None:
        pa = r.get("payout_approx")
        if pa is not None:
            payout_checked = True
            payout_pass = (pa >= MIN_PAYOUT_RATIO)

    if div_inc_checked or payout_checked:
        if not (div_inc_pass or payout_pass):
            divs = r.get("divByYear", [])
            vals = [d["cashDiv"] for d in divs[:3] if d.get("cashDiv", 0) > 0]
            pa   = r.get("payout_approx")
            inc3 = r.get("divIncreasing3y")
            d_str = f"{'連增' if inc3 else '未連增'}3年({'/'.join(str(v) for v in vals)})" if div_inc_checked else ""
            p_str = f"配息率≈{pa:.0f}%<{MIN_PAYOUT_RATIO}%" if payout_checked and pa else ""
            fails.append(f"配息穩定度不足:{d_str}{' 且 ' if d_str and p_str else ''}{p_str}")

    return (len(fails) == 0), fails


# ════════════════════════════════════════════════════════════════════════════════
# 主流程
# ════════════════════════════════════════════════════════════════════════════════

async def main():
    from playwright.async_api import async_playwright
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    today = date.today()
    print(f"\n{'='*65}")
    print(f"除息前布局篩選  執行日期：{today}")
    print(f"必備：填息率>={MIN_FILL_RATE}%  填息<={MAX_FILL_DAYS}天  殖利率>={MIN_YIELD}%  OP>0")
    print(f"進階：PE<{MAX_PE}  PB<{MAX_PB}  EPS年增>={MIN_EPS_YOY}%  OP3年增>={MIN_OP_GROWTH_3Y}%")
    print(f"進階：股利連增3年={REQUIRE_DIV_INCREASE}  配息率>={MIN_PAYOUT_RATIO}%({MIN_PAYOUT_YEARS}/5年)")
    print(f"進場窗口：除息前 T-{BUY_WIN_MAX_TD} 至 T-{BUY_WIN_MIN_TD} 個交易日")
    print(f"{'='*65}\n")

    stock_names = load_names()

    # ── Step 1: 載入除息清單 ─────────────────────────────────────────────────
    print("── 載入除息清單...")
    exdiv_map = load_exdiv_map()    # {code: date_obj}
    win_lo = today + timedelta(days=DAYS_MIN)
    win_hi = today + timedelta(days=DAYS_MAX)
    window_codes = {
        code: dt for code, dt in exdiv_map.items()
        if win_lo <= dt <= win_hi
    }
    print(f"  除息窗口內（{win_lo}~{win_hi}）：{len(window_codes)} 支")

    # 計算交易日並標記「進場窗口」（T-3 ~ T-10）
    buy_window_codes = {}
    for code, dt in window_codes.items():
        td = trading_days_until(dt)
        if BUY_WIN_MIN_TD <= td <= BUY_WIN_MAX_TD:
            buy_window_codes[code] = dt
    print(f"  進場窗口（T-{BUY_WIN_MAX_TD}~T-{BUY_WIN_MIN_TD}交易日）：{len(buy_window_codes)} 支")
    for c, dt in sorted(buy_window_codes.items(), key=lambda x: x[1]):
        td = trading_days_until(dt)
        print(f"    {c}  {dt}  (T-{td}交易日)")

    # ── Step 2: 候選股 ────────────────────────────────────────────────────────
    candidates = load_candidates()
    watch_candidates = [c for c in candidates if c["code"] not in window_codes][:MAX_STOCKS]
    print(f"\n第一層過濾（OP>0，排除窗口股）：{len(watch_candidates)} 支觀察候選\n")

    if not window_codes and not watch_candidates:
        print("[ERROR] 無候選股，請確認 twse_daily.json 存在")
        return

    qualified_window = []
    qualified_watch  = []
    results_all      = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            locale="zh-TW",
        )
        page = await context.new_page()

        await page.goto("https://goodinfo.tw/tw/", timeout=20000)
        await asyncio.sleep(1)

        # ── Pass A: 窗口股 ──────────────────────────────────────────────────
        win_list = sorted(window_codes.items(), key=lambda x: x[1])
        for i, (code, ex_dt) in enumerate(win_list):
            sys.stdout.write(f"\r  [窗口 {i+1}/{len(win_list)}] 查詢 {code}...")
            sys.stdout.flush()

            inc = next((c for c in candidates if c["code"] == code), {})
            roc_year = inc.get("roc_year", "")
            quarter  = inc.get("quarter",  "")

            # 取前一年同季 EPS（用於 YoY 計算）
            prev_eps = None
            if MIN_EPS_YOY is not None and roc_year and quarter:
                prev_eps = fetch_prev_year_eps(code, roc_year, quarter)

            # 取近3年 OP 增率
            op_3y = None
            if MIN_OP_GROWTH_3Y is not None and roc_year and quarter:
                op_3y = fetch_op_3y_growth(code, roc_year, quarter)

            r = await scrape_goodinfo(page, code, prev_eps=prev_eps,
                                      op_3y_growth=op_3y, debug=(i == 0))
            r["exDate"]          = str(ex_dt)
            r["ex_days"]         = (ex_dt - today).days
            r["trading_days"]    = trading_days_until(ex_dt)
            r["operatingIncome"] = inc.get("operatingIncome")
            r["eps"]             = inc.get("eps")
            results_all.append(r)

            if "error" not in r:
                passes, fails = passes_criteria(r)
                in_buy_win = code in buy_window_codes
                if passes:
                    qualified_window.append({**r, "in_window": True,
                                             "in_buy_window": in_buy_win})
                else:
                    print(f"\n    ✗ {code} 未過：{', '.join(fails)}")

            await asyncio.sleep(1.2)

        print()

        # ── Pass B: 觀察名單 ─────────────────────────────────────────────────
        for i, cand in enumerate(watch_candidates):
            code = cand["code"]
            sys.stdout.write(f"\r  [觀察 {i+1}/{len(watch_candidates)}] 查詢 {code}...")
            sys.stdout.flush()

            roc_year = cand.get("roc_year", "")
            quarter  = cand.get("quarter",  "")

            prev_eps = None
            if MIN_EPS_YOY is not None and roc_year and quarter:
                prev_eps = fetch_prev_year_eps(code, roc_year, quarter)

            op_3y = None
            if MIN_OP_GROWTH_3Y is not None and roc_year and quarter:
                op_3y = fetch_op_3y_growth(code, roc_year, quarter)

            r = await scrape_goodinfo(page, code, prev_eps=prev_eps,
                                      op_3y_growth=op_3y)
            r["operatingIncome"] = cand.get("operatingIncome")
            r["eps"]             = cand.get("eps")
            results_all.append(r)

            if "error" in r:
                await asyncio.sleep(1.2)
                continue

            ex_days = r.get("ex_days")
            passes, fails = passes_criteria(r)
            if passes:
                in_win = (ex_days is not None and DAYS_MIN <= ex_days <= DAYS_MAX)
                td = r.get("trading_days", 0) or 0
                in_buy_win = in_win and (BUY_WIN_MIN_TD <= td <= BUY_WIN_MAX_TD)
                entry = {**r, "in_window": in_win, "in_buy_window": in_buy_win}
                if in_win:
                    qualified_window.append(entry)
                else:
                    qualified_watch.append(entry)

            await asyncio.sleep(1.2)

        await browser.close()

    # ── 輸出結果 ──────────────────────────────────────────────────────────────
    print(f"\n\n{'='*65}")
    print(f"✅ 符合除息窗口 + 全部條件：{len(qualified_window)} 支")
    print(f"   其中進場窗口（T-{BUY_WIN_MAX_TD}~T-{BUY_WIN_MIN_TD}）：{sum(1 for q in qualified_window if q.get('in_buy_window'))} 支")
    print(f"📋 符合全部條件（觀察名單，未公告除息日）：{len(qualified_watch)} 支")
    print(f"{'='*65}\n")

    def print_stock(q, show_buy_flag=True):
        code = q['code']
        name = q.get('name') or stock_names.get(code, '')
        ex_info = f"除息 {q.get('exDate','未公告')} (T-{q.get('trading_days','?')}交易日/{q.get('ex_days','?')}天後)" if q.get('exDate') else "除息日未公告"
        buy_flag = " 🔔進場" if (show_buy_flag and q.get('in_buy_window')) else ""
        y3   = q.get('yield3y')
        pa   = q.get('payout_approx')
        yoy  = q.get('eps_yoy')
        di   = q.get('divIncreasing3y')
        print(f"\n  ▶ {code} {name}  —  {ex_info}{buy_flag}")
        print(f"    填息率:{q.get('fillRate','?')}%  填息:{q.get('fillDays','?')}天  殖利率:{f'{y3:.1f}%' if y3 else '?'}  PE:{q.get('pe','N/A')}  PB:{q.get('pb','N/A')}")
        print(f"    連續配息:{q.get('consecutiveYears','?')}年  EPS年增:{f'{yoy:.1f}%' if yoy is not None else '?'}  配息率≈:{f'{pa:.0f}%' if pa else '?'}  股利連增3年:{di}")
        # divByYear
        divs = q.get('divByYear', [])[:4]
        if divs:
            div_str = "  ".join(f"{d['year']}:{d['cashDiv']}" for d in divs)
            print(f"    現金股利  {div_str}")

    if qualified_window:
        # 先印進場窗口股，再印其他
        buy_stocks = [q for q in qualified_window if q.get('in_buy_window')]
        other_stocks = [q for q in qualified_window if not q.get('in_buy_window')]

        if buy_stocks:
            print(f"【🔔 立即進場候選（T-{BUY_WIN_MAX_TD}~T-{BUY_WIN_MIN_TD}交易日前）】")
            for q in sorted(buy_stocks, key=lambda x: x.get("trading_days", 99)):
                print_stock(q)

        if other_stocks:
            print(f"\n【除息窗口內但尚未到進場時機】")
            for q in sorted(other_stocks, key=lambda x: x.get("ex_days", 99)):
                print_stock(q, show_buy_flag=False)
    else:
        print("【立即布局候選】暫無")

    if qualified_watch:
        print(f"\n【觀察名單 — 等待除息日公告】（Top {min(20,len(qualified_watch))}）")
        for q in sorted(qualified_watch, key=lambda x: -(x.get("yield3y") or 0))[:20]:
            print_stock(q, show_buy_flag=False)

    # 存結果
    out_path = BASE_DIR / "exdiv_screener_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "run_date": str(today),
            "criteria": {
                "min_fill_rate":        MIN_FILL_RATE,
                "max_fill_days":        MAX_FILL_DAYS,
                "min_yield":            MIN_YIELD,
                "min_years_div":        MIN_YEARS_DIV,
                "require_op_growth":    REQUIRE_OP_GROWTH,
                "max_pe":               MAX_PE,
                "max_pb":               MAX_PB,
                "min_eps_yoy":          MIN_EPS_YOY,
                "min_op_growth_3y":     MIN_OP_GROWTH_3Y,
                "require_div_increase": REQUIRE_DIV_INCREASE,
                "min_payout_ratio":     MIN_PAYOUT_RATIO,
                "min_payout_years":     MIN_PAYOUT_YEARS,
                "buy_window_td":        [BUY_WIN_MIN_TD, BUY_WIN_MAX_TD],
            },
            "qualified_window": qualified_window,
            "qualified_watch":  qualified_watch,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n結果已存至 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
