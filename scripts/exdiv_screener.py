#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
除息前布局篩選器 — 光速填息股篩選
執行: python exdiv_screener.py

【篩選條件】
必備：
  1. 近3年皆配息
  2. 填息成功率 >= MIN_FILL_RATE (預設 80%，可改 40%)
  3. 平均填息天數 <= MAX_FILL_DAYS (預設 60天)
  4. 近3年平均殖利率 >= MIN_YIELD (預設 5%)
  5. 最新季營業利益成長率 > 0
選配：
  6. PE < MAX_PE (預設 15)
  7. PB < MAX_PB (預設 1.5)
"""

import asyncio, json, re, sys, time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

# ─── 可調整參數 ────────────────────────────────────────────────────────────────
MIN_FILL_RATE   = 80    # 最低填息率(%)，放寬可改 40
MAX_FILL_DAYS   = 60    # 最大平均填息天數
MIN_YIELD       = 5.0   # 最低3年平均殖利率(%)
MIN_YEARS_DIV   = 3     # 最少連續配息年數
MAX_PE          = 15    # 最高本益比 (None = 不篩)
MAX_PB          = 1.5   # 最高股價淨值比 (None = 不篩)
REQUIRE_OP_GROWTH = True  # 是否要求最新季營業利益成長率 > 0
DAYS_MIN        = 0     # 距除息日最少天數（0 = 今天也算）
DAYS_MAX        = 30    # 距除息日最多天數（布局窗口，約1個月）
MAX_STOCKS      = 100   # 觀察名單最多查幾支（窗口股另外處理，不受此限）
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent.parent
DAILY_JSON  = BASE_DIR / "twse_daily.json"
TSE_JSON    = BASE_DIR / "stocks_tse.json"
OTC_JSON    = BASE_DIR / "stocks_otc.json"
EXDIV_JSON  = BASE_DIR / "exdiv_2026.json"
NAMES_JSON  = BASE_DIR / "stock_names.json"

def load_names() -> dict:
    try:
        return json.load(open(NAMES_JSON, encoding='utf-8'))
    except Exception:
        return {}

def fetch_upcoming_exdiv() -> dict:
    """
    從 TWSE API 抓未來 60 天除息清單，合併更新 exdiv_2026.json。
    回傳 {code: date_obj} 的完整字典（包含舊資料）。
    """
    today = date.today()
    start_str = today.strftime('%Y%m%d')
    end_str   = (today + timedelta(days=60)).strftime('%Y%m%d')

    # 載入既有資料
    exdiv: dict = {}
    try:
        with open(EXDIV_JSON, encoding='utf-8') as f:
            raw = json.load(f)
            if isinstance(raw, dict):
                exdiv.update(raw)
    except Exception:
        pass

    # ── 上市（TWSE）────────────────────────────────────────────────────────
    twse_urls = [
        f'https://www.twse.com.tw/rwd/zh/exRight/TWT49U?response=json&startDate={start_str}&endDate={end_str}',
        f'https://www.twse.com.tw/exchangeReport/TWT49U?response=json&startDate={start_str}&endDate={end_str}',
    ]
    new_count = 0
    for url in twse_urls:
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://www.twse.com.tw/',
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                j = json.loads(resp.read().decode('utf-8'))
            if j.get('stat') not in ('OK', '成功') or not isinstance(j.get('data'), list):
                continue
            for row in j['data']:
                key = str(row[11] if len(row) > 11 else '').strip()
                if ',' in key:
                    code, datestr = key.split(',', 1)
                    if len(datestr) == 8:
                        mm, dd = datestr[4:6], datestr[6:8]
                        exdiv[code] = f'{mm}/{dd}'
                        new_count += 1
            print(f"  ✅ TWSE 上市：{new_count} 筆")
            break
        except Exception as e:
            print(f"  ⚠️ TWSE 上市 API 失敗: {e}")

    # 上櫃（TPEX）需要 browser session，無法直接 HTTP 取得。
    # 上櫃股的除息日由 Pass B Goodinfo 個股頁面抓取（scrape_goodinfo 的 exDate）。

    # 存回 JSON（不清除舊資料）
    try:
        with open(EXDIV_JSON, 'w', encoding='utf-8') as f:
            json.dump(exdiv, f, ensure_ascii=False)
    except Exception as e:
        print(f"  ⚠️ 寫入 exdiv_2026.json 失敗: {e}")

    # 轉換為 {code: date_obj}
    result = {}
    for code, mdd in exdiv.items():
        try:
            mo, dy = mdd.split('/')
            result[code] = date(2026, int(mo), int(dy))
        except Exception:
            pass
    return result


def load_candidates():
    """從 twse_daily.json income 資料取候選股"""
    try:
        with open(DAILY_JSON, encoding="utf-8") as f:
            daily = json.load(f)
    except Exception as e:
        print(f"[ERROR] 無法讀取 twse_daily.json: {e}")
        return []

    income = daily.get("income", {})
    if not income:
        print("[WARN] income 資料為空，使用全部股票清單")
        # fallback: use all TSE stocks
        try:
            with open(TSE_JSON, encoding="utf-8") as f:
                tse = json.load(f)
            return [{"code": c} for c in tse.get("stocks", {}).keys()]
        except:
            return []

    candidates = []
    for code, inc in income.items():
        if not re.match(r"^\d{4,6}$", str(code)):
            continue
        op = inc.get("operatingIncome")
        if REQUIRE_OP_GROWTH and (op is None or op <= 0):
            continue
        candidates.append({
            "code": code,
            "operatingIncome": op or 0,
            "eps": inc.get("eps"),
        })

    # Sort by operating income (highest first = strongest companies)
    candidates.sort(key=lambda x: x["operatingIncome"], reverse=True)
    return candidates[:MAX_STOCKS]


def _flt(s):
    try:
        return float(re.sub(r"[,%]", "", str(s).strip()))
    except:
        return None


_debug_printed = set()   # 每個 code 只 debug 一次

async def scrape_goodinfo(page, code: str, debug: bool = False) -> dict:
    """
    抓取 Goodinfo 個股股利政策頁面
    回傳 dict 含: code, years_div, fill_rate, fill_days, yield_3y, pe, pb, ex_date, ex_days
    """
    result = {"code": code}
    url = f"https://goodinfo.tw/tw/StockDividendPolicy.asp?STOCK_ID={code}"
    try:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await asyncio.sleep(1.5)
    except Exception as e:
        result["error"] = str(e)
        return result

    try:
        data = await page.evaluate("""() => {
            const text = document.body.innerText;
            const rows = text.split('\\n').map(l => l.trim()).filter(Boolean);

            // ── 名稱 ──
            let name = '';
            for (const r of rows.slice(0, 20)) {
                const m = r.match(/^(\\d{4,6})\\s+(.+?)\\s*[－\\-]/);
                if (m) { name = m[2].trim(); break; }
            }

            // ── 連續配息年數 ──
            let consecutiveYears = 0;
            const consM = text.match(/連續\\s*(\\d+)\\s*年.*?配.*?(?:現金)?股利/) ||
                          text.match(/已連續(\\d+)年/);
            if (consM) consecutiveYears = parseInt(consM[1]);

            // ── 均填息日數 ──
            let fillDays = null;
            const fdPatterns = [
                /均填息日數[^\\d]*(\\d+)/,
                /填息[^\\d]*均[^\\d]*(\\d+)/,
                /平均填息[^\\d]*(\\d+)/,
            ];
            for (const p of fdPatterns) {
                const m = text.match(p);
                if (m) { fillDays = parseFloat(m[1]); break; }
            }

            // ── 近3年平均殖利率 ──
            let yield3y = null;
            const yPatterns = [
                /近3年平均[\\s\\S]{0,300}?殖利率[^\\d]*(\\d+\\.\\d+)/,
                /平均殖利率[^\\d]*(\\d+\\.\\d+)/,
                /3年均殖[^\\d]*(\\d+\\.\\d+)/,
            ];
            for (const p of yPatterns) {
                const m = text.match(p);
                if (m) { yield3y = parseFloat(m[1]); break; }
            }
            // fallback: 從年度行提取殖利率，取近3年平均
            if (yield3y === null) {
                const yVals = [];
                for (const row of rows) {
                    if (!/^20(2[0-9])/.test(row)) continue;
                    // 找行末的 X.XX% 格式（殖利率通常在最後）
                    const ym = row.match(/(\\d+\\.\\d+)%\\s*$/);
                    if (ym) {
                        const v = parseFloat(ym[1]);
                        if (v > 0.3 && v < 30) yVals.push(v);
                    }
                    if (yVals.length >= 3) break;
                }
                if (yVals.length) yield3y = yVals.reduce((a,b)=>a+b,0) / yVals.length;
            }

            // ── 填息率（row-by-row）──
            // Goodinfo 年度表格行結構（innerText）:
            //   2024  12/25  06/11  XX.XX  21  5.2%   → 有填息，21天
            //   2023  12/20  06/09  XX.XX  -   4.8%   → 未填息
            // 除息日(MM/DD) 後接除息參考價(XX.XX)，再接填息天數(數字 or -)，再接殖利率%
            let filled = 0, total = 0;
            for (const row of rows) {
                if (!/^20(2[0-9])/.test(row)) continue;
                if (!/%/.test(row)) continue;
                // pattern: MM/DD <ref_price> <fill_days_or_dash> <yield>%
                const fillM = row.match(/\\d{1,2}\\/\\d{2}\\s+[\\d.]+\\s+(\\d+|-)\\s+[\\d.]+%/);
                if (fillM) {
                    total++;
                    if (fillM[1] !== '-') filled++;
                } else {
                    // 備援：找行內「填息」後接數字或「-」
                    const altM = row.match(/填息[^\\d-]*(\\d+|-)/);
                    if (altM) {
                        total++;
                        if (altM[1] !== '-') filled++;
                    }
                }
                if (total >= 5) break;
            }
            const fillRate = total > 0 ? Math.round(filled / total * 100) : null;

            // ── PE / PB ──
            const peMatch = text.match(/本益比[^\\d]*(\\d+\\.?\\d*)/) ||
                            text.match(/PER[^\\d]*(\\d+\\.?\\d*)/i);
            const pbMatch = text.match(/股價淨值比[^\\d]*(\\d+\\.?\\d*)/) ||
                            text.match(/PBR[^\\d]*(\\d+\\.?\\d*)/i);
            const pe = peMatch ? parseFloat(peMatch[1]) : null;
            const pb = pbMatch ? parseFloat(pbMatch[1]) : null;

            // ── 即將除息日（2026 年份）──
            // Goodinfo 表格最新行通常最上面，格式: 2026  ...  06/23  ...
            let exDate = null;
            for (const row of rows) {
                if (!/^2026/.test(row)) continue;
                // 找 MM/DD 格式的日期
                const dm = row.match(/(\\d{1,2})\\/(\\d{2})/);
                if (dm) {
                    const mo = dm[1].padStart(2,'0'), dy = dm[2];
                    exDate = `2026-${mo}-${dy}`;
                    break;
                }
            }

            return { name, consecutiveYears, fillDays, fillRate, yield3y, pe, pb, exDate };
        }""")

        result.update(data)

        # debug：印出前500字的 innerText 以便 tune regex
        if debug and code not in _debug_printed:
            raw_text = await page.evaluate("() => document.body.innerText.slice(0, 800)")
            print(f"\n[DEBUG {code}] innerText sample:\n{raw_text}\n{'─'*60}")
            _debug_printed.add(code)

        # Parse ex_date and compute days until ex-div
        if data.get("exDate"):
            try:
                parts = data["exDate"].split("-")
                ex_dt = date(int(parts[0]), int(parts[1]), int(parts[2]))
                result["ex_days"] = (ex_dt - date.today()).days
            except:
                result["ex_days"] = None

    except Exception as e:
        result["error"] = str(e)

    return result


def passes_criteria(r: dict) -> tuple[bool, list]:
    """Returns (passes, list_of_fail_reasons)"""
    fails = []

    # 1. 連續配息年數（若解析不到則跳過，不視為失敗）
    cy = r.get("consecutiveYears", 0)
    if cy > 0 and cy < MIN_YEARS_DIV:
        fails.append(f"連續配息{cy}年<{MIN_YEARS_DIV}年")

    # 2. 填息率（None=解析失敗，不視為失敗）
    fr = r.get("fillRate")
    if fr is not None and fr < MIN_FILL_RATE:
        fails.append(f"填息率{fr}%<{MIN_FILL_RATE}%")

    # 3. 平均填息天數（None=跳過）
    fd = r.get("fillDays")
    if fd is not None and fd > MAX_FILL_DAYS:
        fails.append(f"填息{fd}天>{MAX_FILL_DAYS}天")

    # 4. 殖利率（None=跳過，有資料才檢查）
    y3 = r.get("yield3y")
    if y3 is not None and y3 < MIN_YIELD:
        fails.append(f"殖利率{y3:.1f}%<{MIN_YIELD}%")

    # 5. PE（None=跳過）
    if MAX_PE is not None:
        pe = r.get("pe")
        if pe is not None and pe > MAX_PE:
            fails.append(f"PE{pe:.1f}>{MAX_PE}")

    # 6. PB（None=跳過）
    if MAX_PB is not None:
        pb = r.get("pb")
        if pb is not None and pb > MAX_PB:
            fails.append(f"PB{pb:.1f}>{MAX_PB}")

    return (len(fails) == 0), fails


async def main():
    from playwright.async_api import async_playwright

    today = date.today()
    print(f"\n{'='*60}")
    print(f"除息前布局篩選  執行日期：{today}")
    print(f"篩選條件：填息率≥{MIN_FILL_RATE}%  填息≤{MAX_FILL_DAYS}天  殖利率≥{MIN_YIELD}%  PE<{MAX_PE}  PB<{MAX_PB}")
    print(f"除息窗口：{today+timedelta(days=DAYS_MIN)} ~ {today+timedelta(days=DAYS_MAX)} ({DAYS_MIN}~{DAYS_MAX}天後)")
    print(f"{'='*60}\n")

    stock_names = load_names()   # 用戶手動匯入的中文名稱

    # ── Step 1: 從 TWSE 拿最新除息清單 ───────────────────────────────────────
    print("── 更新 TWSE 除息清單...")
    exdiv_map = fetch_upcoming_exdiv()   # {code: date_obj}
    win_lo = today + timedelta(days=DAYS_MIN)
    win_hi = today + timedelta(days=DAYS_MAX)
    window_codes = {
        code: dt for code, dt in exdiv_map.items()
        if win_lo <= dt <= win_hi
    }
    print(f"  除息窗口內（{win_lo}~{win_hi}）確認股：{len(window_codes)} 支")
    if window_codes:
        for c, dt in sorted(window_codes.items(), key=lambda x: x[1]):
            print(f"    {c}  {dt}  ({(dt-today).days}天後)")

    # ── Step 2: 候選股（用於觀察名單）────────────────────────────────────────
    candidates = load_candidates()
    # 排除已在窗口的（另行優先處理）
    watch_candidates = [c for c in candidates if c["code"] not in window_codes][:MAX_STOCKS]
    print(f"\n第一層過濾（營業利益>0，排除窗口股）：{len(watch_candidates)} 支觀察候選\n")

    if not window_codes and not watch_candidates:
        print("[ERROR] 無候選股，請確認 twse_daily.json 存在")
        return

    qualified_window = []   # 符合除息窗口 + 全部條件
    qualified_watch  = []   # 符合全部條件（排除除息窗口，作為備選觀察名單）
    results_all      = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="zh-TW",
        )
        page = await context.new_page()

        # Visit Goodinfo home first (get cookies)
        await page.goto("https://goodinfo.tw/tw/", timeout=20000)
        await asyncio.sleep(1)

        # ── Pass A: 窗口股（TWSE 確認除息日，只查填息率/殖利率）─────────────
        win_list = sorted(window_codes.items(), key=lambda x: x[1])
        for i, (code, ex_dt) in enumerate(win_list):
            sys.stdout.write(f"\r  [窗口 {i+1}/{len(win_list)}] 查詢 {code}...")
            sys.stdout.flush()

            r = await scrape_goodinfo(page, code, debug=(i == 0))
            # 以 TWSE 除息日為準（覆蓋 Goodinfo 解析結果）
            r["exDate"]  = str(ex_dt)
            r["ex_days"] = (ex_dt - today).days

            # income 資訊（若在候選股內）
            inc = next((c for c in candidates if c["code"] == code), {})
            r["operatingIncome"] = inc.get("operatingIncome")
            r["eps"] = inc.get("eps")
            results_all.append(r)

            if "error" not in r:
                passes, fails = passes_criteria(r)
                if passes:
                    qualified_window.append({**r, "in_window": True})
                else:
                    print(f"\n    ✗ {code} 未過：{', '.join(fails)}")

            await asyncio.sleep(1.2)

        print()

        # ── Pass B: 觀察名單（掃描高OP股，找尚未公告但體質好的）────────────
        for i, cand in enumerate(watch_candidates):
            code = cand["code"]
            sys.stdout.write(f"\r  [觀察 {i+1}/{len(watch_candidates)}] 查詢 {code}...")
            sys.stdout.flush()

            r = await scrape_goodinfo(page, code, debug=(i == 0))
            r["operatingIncome"] = cand.get("operatingIncome")
            r["eps"] = cand.get("eps")
            results_all.append(r)

            if "error" in r:
                await asyncio.sleep(1.2)
                continue

            # 若 Goodinfo 有找到除息日且在窗口內 → 補入窗口清單
            ex_days = r.get("ex_days")
            passes, fails = passes_criteria(r)
            if passes:
                in_window = (ex_days is not None and DAYS_MIN <= ex_days <= DAYS_MAX)
                entry = {**r, "in_window": in_window}
                if in_window:
                    qualified_window.append(entry)
                else:
                    qualified_watch.append(entry)

            await asyncio.sleep(1.2)

        await browser.close()

    print(f"\n\n{'='*60}")
    print(f"✅ 符合除息窗口（{DAYS_MIN}~{DAYS_MAX}天後除息）+ 全部條件：{len(qualified_window)} 支")
    print(f"📋 符合全部條件（觀察名單，尚無確定除息日）：{len(qualified_watch)} 支")
    print(f"{'='*60}\n")

    def print_stock(q):
        code = q['code']
        name = q.get('name') or stock_names.get(code, '')
        ex_info = f"除息 {q.get('exDate','未公告')} ({q.get('ex_days','?')}天後)" if q.get('exDate') else "除息日未公告"
        y3 = q.get('yield3y')
        y3_str = f"{y3:.1f}%" if y3 is not None else "?"
        print(f"\n  ▶ {code} {name}  —  {ex_info}")
        print(f"    填息率:{q.get('fillRate','?')}%  填息{q.get('fillDays','?')}天  3年殖利率:{y3_str}  PE:{q.get('pe','N/A')}  PB:{q.get('pb','N/A')}")
        print(f"    連續配息:{q.get('consecutiveYears','?')}年  營業利益:{q.get('operatingIncome',0):,.0f}")

    if qualified_window:
        print("【立即布局候選】")
        for q in sorted(qualified_window, key=lambda x: x.get("ex_days", 99)):
            print_stock(q)
    else:
        print("【立即布局候選】暫無（目前除息窗口內無已公告除息日的合格股）")

    if qualified_watch:
        print(f"\n【觀察名單 — 等待除息日公告】（Top {min(20,len(qualified_watch))}）")
        for q in sorted(qualified_watch, key=lambda x: -(x.get("yield3y") or 0))[:20]:
            print_stock(q)

    # Save full results
    out_path = BASE_DIR / "exdiv_screener_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "run_date": str(today),
            "criteria": {
                "min_fill_rate": MIN_FILL_RATE,
                "max_fill_days": MAX_FILL_DAYS,
                "min_yield": MIN_YIELD,
                "min_years_div": MIN_YEARS_DIV,
                "max_pe": MAX_PE,
                "max_pb": MAX_PB,
                "days_window": [DAYS_MIN, DAYS_MAX],
            },
            "qualified_window": qualified_window,
            "qualified_watch":  qualified_watch,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n結果已存至 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
