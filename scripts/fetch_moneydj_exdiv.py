#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
從 MoneyDJ 除權除息表抓取資料 → exdiv_moneydj.json
執行：python scripts/fetch_moneydj_exdiv.py
GitHub Actions 每 3 天自動執行（需要 playwright + chromium）
"""

import json, sys
from datetime import date, datetime, timezone
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("缺少 playwright，請先執行：pip install playwright && playwright install chromium")
    sys.exit(1)

BASE_DIR    = Path(__file__).parent.parent
OUTPUT_JSON = BASE_DIR / "exdiv_moneydj.json"

URL = "https://concords.moneydj.com/z/ze/zeb/zeb.djhtm"

EXTRACT_JS = """
() => {
  const rows = document.querySelectorAll('tr');
  const records = [];
  for (const row of rows) {
    const cells = row.querySelectorAll('td');
    if (cells.length < 5) continue;
    const t = Array.from(cells).map(c => c.innerText.trim());
    if (!/^\\d{4,6}/.test(t[0])) continue;
    const m = t[0].match(/^(\\d{4,6})(.*)/);
    if (!m) continue;
    if (!/^\\d{4}\\/\\d{2}\\/\\d{2}$/.test(t[1] || '')) continue;
    records.push({
      code:               m[1],
      name:               m[2].trim(),
      ex_div_date:        t[1],
      cash_div_earnings:  parseFloat(t[2]) || 0,
      cash_div_reserve:   parseFloat(t[3]) || 0,
      cash_div_total:     parseFloat(t[4]) || 0,
      div_pay_date:       t[5] || '',
      ex_right_date:      t[6] || '',
      stock_div_earnings: parseFloat(t[7]) || 0,
      stock_div_reserve:  parseFloat(t[8]) || 0,
      stock_div_total:    parseFloat(t[9]) || 0
    });
  }
  return records;
}
"""


def fetch() -> list[dict]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=60000)
        records = page.evaluate(EXTRACT_JS)
        browser.close()
    return records


def main():
    print("抓取 MoneyDJ 除權除息表…")
    records = fetch()
    print(f"  共 {len(records)} 筆")

    today = date.today().strftime("%Y/%m/%d")
    future = [r for r in records if r["ex_div_date"] >= today]
    print(f"  今天起未來除息：{len(future)} 筆")

    out = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count":      len(records),
        "records":    records,
    }
    OUTPUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已寫入 {OUTPUT_JSON}")

    # 預覽近 10 天內除息股
    print(f"\n── 近 10 天除息股（今天 {today}）──────────────────────")
    today_dt = date.today()
    for r in records:
        d_str = r["ex_div_date"]
        if d_str < today:
            continue
        diff = (datetime.strptime(d_str, "%Y/%m/%d").date() - today_dt).days
        if diff > 10:
            break
        total = r["cash_div_total"]
        right = f"  配股{r['stock_div_total']}" if r["stock_div_total"] else ""
        print(f"  {r['code']} {r['name']:<10} {d_str}  現金 {total:.4f}{right}  (+{diff}天)")


if __name__ == "__main__":
    main()
