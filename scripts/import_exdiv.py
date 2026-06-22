#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手動匯入除息清單
用法：python import_exdiv.py

把你的資料貼進去，格式隨意，例如：
  2330 台積電 07/15
  2317 鴻海 07/08
  6669 緯穎 06/22
  1301	台塑	2026/07/10
  2412 中華電 2026-07-20

每行一支，代號必須是4-6位數字，日期是 MM/DD 或 YYYY/MM/DD 或 YYYY-MM-DD
"""

import json, re, sys
from datetime import date
from pathlib import Path

BASE_DIR   = Path(__file__).parent.parent
EXDIV_JSON = BASE_DIR / "exdiv_2026.json"
NAMES_JSON = BASE_DIR / "stock_names.json"

def parse_line(line: str):
    """解析一行，回傳 (code, name_or_None, date_str_MM/DD) 或 None"""
    line = line.strip()
    if not line or line.startswith('#'):
        return None

    # 抓代號（4-6位數字）
    code_m = re.search(r'\b(\d{4,6})\b', line)
    if not code_m:
        return None
    code = code_m.group(1)

    # 抓日期 — 支援多種格式
    date_obj = None
    # YYYY/MM/DD 或 YYYY-MM-DD
    m = re.search(r'(20\d{2})[/-](\d{1,2})[/-](\d{2})', line)
    if m:
        date_obj = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        # MM/DD（補 2026 年）
        m = re.search(r'\b(\d{1,2})/(\d{2})\b', line)
        if m:
            date_obj = date(2026, int(m.group(1)), int(m.group(2)))

    if not date_obj:
        return None

    # 抓中文名稱（去掉代號和日期後剩下的中文字串）
    remainder = line
    remainder = remainder.replace(code_m.group(0), '', 1)
    remainder = re.sub(r'(20\d{2})?[/-]?\d{1,2}[/-]\d{2}', '', remainder)
    name_m = re.search(r'[一-鿿\w]{2,}', remainder.strip())
    name = name_m.group(0).strip() if name_m else None

    return code, name, f'{date_obj.month:02d}/{date_obj.day:02d}'


def main():
    # 載入既有資料
    exdiv: dict = {}
    try:
        exdiv.update(json.load(open(EXDIV_JSON, encoding='utf-8')))
    except Exception:
        pass

    names: dict = {}
    try:
        names.update(json.load(open(NAMES_JSON, encoding='utf-8')))
    except Exception:
        pass

    print("貼上你的除息清單（格式：代號 名稱 日期，每行一支）")
    print("輸入完畢後，Windows 按 Ctrl+Z → Enter，Mac/Linux 按 Ctrl+D")
    print("─" * 50)

    lines = sys.stdin.read().splitlines()

    added = 0
    skipped = 0
    for line in lines:
        parsed = parse_line(line)
        if not parsed:
            if line.strip():
                print(f"  ⚠️ 無法解析：{line!r}")
                skipped += 1
            continue
        code, name, mdd = parsed
        exdiv[code] = mdd
        if name:
            names[code] = name
        today = date.today()
        mo, dy = mdd.split('/')
        ex = date(2026, int(mo), int(dy))
        diff = (ex - today).days
        print(f"  ✅ {code} {name or '?'}  除息 2026-{mdd}  ({diff:+d}天)")
        added += 1

    # 存檔
    with open(EXDIV_JSON, 'w', encoding='utf-8') as f:
        json.dump(exdiv, f, ensure_ascii=False, indent=2)
    with open(NAMES_JSON, 'w', encoding='utf-8') as f:
        json.dump(names, f, ensure_ascii=False, indent=2)

    print(f"\n匯入完成：新增/更新 {added} 支，跳過 {skipped} 行")
    print(f"exdiv_2026.json 共 {len(exdiv)} 筆")

    # 預覽未來30天的股票
    today = date.today()
    from datetime import timedelta
    print(f"\n── 未來30天除息股（今天~{today+timedelta(days=30)}）────────────")
    upcoming = []
    for c, mdd in exdiv.items():
        try:
            mo, dy = mdd.split('/')
            ex = date(2026, int(mo), int(dy))
            diff = (ex - today).days
            if 0 <= diff <= 30:
                upcoming.append((c, ex, diff, names.get(c, '')))
        except Exception:
            pass
    for c, ex, diff, nm in sorted(upcoming, key=lambda x: x[1]):
        print(f"  {c} {nm}  {ex}  (+{diff}天)")


if __name__ == "__main__":
    main()
