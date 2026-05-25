#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_twse_api.py  — 盤中執行，驗證 TWSE 兩個 API 的價格差異
用途：確認 mis.twse.com.tw 回傳即時成交價、STOCK_DAY_ALL 回傳昨收
盤中執行方式：python scripts/test_twse_api.py
"""
import requests
from datetime import datetime

STOCK_ID = "2330"  # 台積電


def test_price_discrepancy():
    print(f"\n{'='*55}")
    print(f"TWSE API 比對測試  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}\n")

    # ── 1. STOCK_DAY_ALL (官方 openapi，盤中通常回傳昨收) ─────────────────
    print("[1] openapi.twse.com.tw / STOCK_DAY_ALL ...")
    day_all_price = None
    try:
        resp = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            timeout=10
        )
        resp.raise_for_status()
        data_all = resp.json()
        # Response is a list with a 'data' key, or sometimes a direct list
        items = data_all.get("data", data_all) if isinstance(data_all, dict) else data_all
        stock = next((s for s in items if s.get("Code") == STOCK_ID), None)
        if stock:
            day_all_price = float(stock.get("ClosingPrice", 0) or 0)
            print(f"    Code         : {stock.get('Code')}")
            print(f"    ClosingPrice : {day_all_price}")
            print(f"    TradeDate    : {stock.get('TradeDate', 'N/A')}")
        else:
            print(f"    ⚠️  Stock {STOCK_ID} not found in STOCK_DAY_ALL")
    except Exception as e:
        print(f"    ❌ Error: {e}")

    print()

    # ── 2. mis.twse.com.tw real-time API ────────────────────────────────
    print("[2] mis.twse.com.tw / getStockInfo.jsp (real-time) ...")
    mis_z = mis_y = None
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://mis.twse.com.tw/stock/fibest.jsp",
        })
        session.get("https://mis.twse.com.tw/stock/", timeout=8)  # warm-up cookies

        url = (f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
               f"?ex_ch=tse_{STOCK_ID}.tw&json=1&delay=0")
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        msg = (data.get("msgArray") or [{}])[0]
        z_str = msg.get("z", "-")
        y_str = msg.get("y", "-")

        def safe_float(s):
            try: return float(s) if s and s != "-" else None
            except (ValueError, TypeError): return None

        mis_z = safe_float(z_str)
        mis_y = safe_float(y_str)
        print(f"    z (即時成交價) : {mis_z}  (raw: '{z_str}')")
        print(f"    y (昨日收盤)   : {mis_y}  (raw: '{y_str}')")
        print(f"    h (今日最高)   : {safe_float(msg.get('h'))}")
        print(f"    l (今日最低)   : {safe_float(msg.get('l'))}")
        print(f"    o (開盤)       : {safe_float(msg.get('o'))}")
        print(f"    v (成交量/股)  : {msg.get('v')}")
    except Exception as e:
        print(f"    ❌ Error: {e}")

    # ── 3. 比對結論 ───────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print("結論")
    print(f"{'='*55}")
    if day_all_price is not None and mis_y is not None:
        if abs(day_all_price - mis_y) < 0.01:
            print(f"✅ BUG CONFIRMED: STOCK_DAY_ALL ({day_all_price}) == MIS y 昨收 ({mis_y})")
            print(f"   → STOCK_DAY_ALL 盤中確實只回傳昨收，不是即時價")
            if mis_z:
                print(f"   → MIS z 即時成交價 = {mis_z}（這才是正確的盤中價格）")
            else:
                print(f"   → MIS z = '-'（股票尚未有成交，屬正常）")
        else:
            print(f"ℹ️  STOCK_DAY_ALL ({day_all_price}) ≠ MIS y ({mis_y})")
            print(f"   可能：STOCK_DAY_ALL 已更新為即時價，或市場已收盤")
    else:
        print("⚠️  無法比對（資料缺失）")

    if mis_z:
        print(f"\n✅ MIS API 可正常取得即時價：{mis_z}")
    else:
        print(f"\n⚠️  MIS z = '-'（股票尚未成交 or 已收盤）")


if __name__ == "__main__":
    test_price_discrepancy()
