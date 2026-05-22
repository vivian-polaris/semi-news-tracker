#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VCP 每日資料健康檢查 — 每個交易日 08:30 TW 自動執行（GitHub Actions）
有問題 → 建立 GitHub Issue → 用戶收到 email 通知
"""
import json, datetime, sys
from pathlib import Path

def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception as e:
        print(f'  [ERR] {path}: {e}')
        return None

def tw_today():
    tz = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(tz).strftime('%Y-%m-%d')

def prev_trading_day(date_str):
    d = datetime.date.fromisoformat(date_str)
    d -= datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d -= datetime.timedelta(days=1)
    return d.isoformat()

def main():
    today = tw_today()
    # 08:30 TW 時最新有效資料日 = 昨日（或上週五）
    expected_min = prev_trading_day(today)

    issues, ok = [], []

    # ── stocks_tse.json ──────────────────────────────────────────────────────
    tse = load_json('stocks_tse.json')
    if tse is None:
        issues.append('❌ stocks_tse.json 無法讀取')
    else:
        date = tse.get('_price_date', '')
        count = len(tse.get('stocks', {}))
        if not date:
            issues.append('❌ stocks_tse.json 缺少 _price_date（可能是尚未部署新版）')
        elif date < expected_min:
            issues.append(f'❌ 上市股價過舊：{date}（應 ≥ {expected_min}）')
        else:
            ok.append(f'✅ 上市股價：{count} 支，截至 {date}')
        if count < 800:
            issues.append(f'❌ 上市股票數量異常：{count}（正常 >1000）')

    # ── stocks_otc.json ──────────────────────────────────────────────────────
    otc = load_json('stocks_otc.json')
    if otc is None:
        issues.append('❌ stocks_otc.json 無法讀取')
    else:
        date = otc.get('_price_date', '')
        count = len(otc.get('stocks', {}))
        if not date:
            issues.append('❌ stocks_otc.json 缺少 _price_date（可能是尚未部署新版）')
        elif date < expected_min:
            issues.append(f'❌ 上櫃股價過舊：{date}（應 ≥ {expected_min}）')
        else:
            ok.append(f'✅ 上櫃股價：{count} 支，截至 {date}')
        if count < 500:
            issues.append(f'❌ 上櫃股票數量異常：{count}（正常 >700）')

    # ── twse_daily.json ──────────────────────────────────────────────────────
    daily = load_json('twse_daily.json')
    if daily is None:
        issues.append('❌ twse_daily.json 無法讀取')
    else:
        income  = len(daily.get('income', {}))
        chips   = len(daily.get('chips', {}))
        revenue = len(daily.get('monthRevenue', {}))
        hist    = len(daily.get('incomeHistory', {}))
        bwibbu  = len(daily.get('bwibbu', {}))

        if income < 500:
            issues.append(f'❌ 損益資料不足：{income} 支（正常 >500）')
        else:
            ok.append(f'✅ 損益：{income} 支')

        if chips < 500:
            issues.append(f'❌ 籌碼資料不足：{chips} 支（正常 >500）')
        else:
            ok.append(f'✅ 籌碼：{chips} 支')

        if revenue < 500:
            issues.append(f'⚠️ 月營收偏少：{revenue} 支（OTC 可能未更新，正常 >800）')
        else:
            ok.append(f'✅ 月營收：{revenue} 支')

        if bwibbu < 500:
            issues.append(f'❌ PE/PB 資料不足：{bwibbu} 支（正常 >500）')
        else:
            ok.append(f'✅ PE/PB：{bwibbu} 支')

        ok.append(f'✅ YoY 歷史累積：{hist} 支')

    # ── 輸出 ─────────────────────────────────────────────────────────────────
    print(f'=== VCP 健康檢查 {today} 08:30 TW ===')
    for msg in ok:
        print(msg)

    if issues:
        print()
        print(f'⚠️ 發現 {len(issues)} 個問題：')
        for msg in issues:
            print(f'  {msg}')
        sys.exit(1)
    else:
        print()
        print('✅ 所有資料正常，今日開盤前無異常')
        sys.exit(0)

if __name__ == '__main__':
    main()
