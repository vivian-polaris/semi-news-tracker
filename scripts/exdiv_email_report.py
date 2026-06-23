#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
除息篩選 Email 報告 — 讀 exdiv_screener_results.json，透過 Brevo API 發送 HTML 報告
執行：python scripts/exdiv_email_report.py
"""

import json, os, sys, datetime
from pathlib import Path
import requests

BREVO_API_KEY = os.environ.get('BREVO_API_KEY') or os.environ.get('RESEND_API_KEY', '')
TO_EMAILS     = ['polaris.sequoia@gmail.com']
FROM_EMAIL    = 'polaris.sequoia@gmail.com'
FROM_NAME     = '除息選股'

BASE_DIR     = Path(__file__).parent.parent
RESULTS_JSON = BASE_DIR / "exdiv_screener_results.json"


def load_results() -> dict:
    if not RESULTS_JSON.exists():
        print('⚠  exdiv_screener_results.json not found — run exdiv_screener.py first.')
        sys.exit(1)
    with open(RESULTS_JSON, encoding='utf-8') as f:
        return json.load(f)


def yield_color(y):
    if y is None: return '#94a3b8'
    if y >= 8: return '#16a34a'
    if y >= 5: return '#2563eb'
    return '#d97706'


def days_color(d):
    if not isinstance(d, (int, float)): return '#94a3b8'
    if d <= 5: return '#dc2626'
    if d <= 10: return '#d97706'
    return '#16a34a'


def build_stock_rows(stocks: list) -> str:
    if not stocks:
        return '<p style="color:#94a3b8;font-size:13px">（無符合條件股票）</p>'
    rows = []
    for q in stocks:
        code    = q.get('code', '')
        name    = q.get('name', code)
        ex_date = q.get('exDate', '未公告')
        td      = q.get('trading_days', '?')
        ex_days = q.get('ex_days', '?')
        y3      = q.get('yield3y')
        fr      = q.get('fillRate')
        fd      = q.get('fillDays')
        pe      = q.get('pe', '—')
        pb      = q.get('pb', '—')
        cy      = q.get('consecutiveYears', '?')
        pa      = q.get('payout_approx')
        div_inc = '✅' if q.get('divIncreasing3y') else '—'
        buy_flag = '🔔 ' if q.get('in_buy_window') else ''

        y3_str = f'{y3:.1f}%' if y3 is not None else '—'
        fr_str = f'{fr}%'    if fr is not None else '—'
        fd_str = f'{fd:.0f}天' if fd is not None else '—'
        pa_str = f'{pa:.0f}%' if pa is not None else '—'

        divs = q.get('divByYear', [])[:3]
        div_hist = '  '.join(f"{d['year']}:{d['cashDiv']}" for d in divs if d.get('cashDiv',0)>0)

        rows.append(
            f'<tr style="border-bottom:1px solid #f1f5f9">'
            f'<td style="padding:6px 8px;font-weight:700;white-space:nowrap">{buy_flag}{code}</td>'
            f'<td style="padding:6px 8px;white-space:nowrap">{name}</td>'
            f'<td style="padding:6px 8px;text-align:center;font-weight:600;color:{days_color(td)};white-space:nowrap">'
            f'{ex_date}<br><span style="font-size:11px;color:#64748b">T-{td}({ex_days}天)</span></td>'
            f'<td style="padding:6px 8px;text-align:center;font-weight:700;color:{yield_color(y3)}">{y3_str}</td>'
            f'<td style="padding:6px 8px;text-align:center">{fr_str}</td>'
            f'<td style="padding:6px 8px;text-align:center">{fd_str}</td>'
            f'<td style="padding:6px 8px;text-align:center">{pa_str}</td>'
            f'<td style="padding:6px 8px;text-align:center">{pe}</td>'
            f'<td style="padding:6px 8px;text-align:center">{pb}</td>'
            f'<td style="padding:6px 8px;text-align:center">{cy}年</td>'
            f'<td style="padding:6px 8px;text-align:center">{div_inc}</td>'
            f'<td style="padding:6px 8px;font-size:11px;color:#64748b;white-space:nowrap">{div_hist}</td>'
            f'</tr>'
        )

    header = (
        '<tr style="background:#f1f5f9;font-size:11px;color:#475569">'
        '<th style="padding:6px 8px;text-align:left">代號</th>'
        '<th style="padding:6px 8px;text-align:left">名稱</th>'
        '<th style="padding:6px 8px">除息日</th>'
        '<th style="padding:6px 8px">近3年殖利率</th>'
        '<th style="padding:6px 8px">填息率</th>'
        '<th style="padding:6px 8px">均填息天</th>'
        '<th style="padding:6px 8px">配息率</th>'
        '<th style="padding:6px 8px">PE</th>'
        '<th style="padding:6px 8px">PB</th>'
        '<th style="padding:6px 8px">連配年</th>'
        '<th style="padding:6px 8px">股利連增3年</th>'
        '<th style="padding:6px 8px">現金股利歷史</th>'
        '</tr>'
    )
    return (
        '<div style="overflow-x:auto">'
        '<table style="width:100%;border-collapse:collapse;font-size:13px;'
        'border:1px solid #e2e8f0;border-radius:6px;overflow:hidden">'
        f'{header}{"".join(rows)}</table></div>'
    )


def build_html(data: dict) -> str:
    run_date         = data.get('run_date', '—')
    qualified_window = data.get('qualified_window', [])
    qualified_watch  = data.get('qualified_watch',  [])
    criteria         = data.get('criteria', {})

    buy_stocks   = sorted([q for q in qualified_window if q.get('in_buy_window')],
                          key=lambda x: x.get('trading_days', 99))
    other_stocks = sorted([q for q in qualified_window if not q.get('in_buy_window')],
                          key=lambda x: x.get('ex_days', 99))
    watch_stocks = sorted(qualified_watch, key=lambda x: -(x.get('yield3y') or 0))[:20]

    tz_tw       = datetime.timezone(datetime.timedelta(hours=8))
    report_time = datetime.datetime.now(tz_tw).strftime('%Y-%m-%d %H:%M')

    crit_str = (
        f"填息率≥{criteria.get('min_fill_rate','?')}%　"
        f"填息≤{criteria.get('max_fill_days','?')}天　"
        f"殖利率≥{criteria.get('min_yield','?')}%　"
        f"連配≥{criteria.get('min_years_div','?')}年　"
        f"PE<{criteria.get('max_pe','?')}　"
        f"PB<{criteria.get('max_pb','?')}"
    )

    sections = ''

    if buy_stocks:
        sections += (
            '<div style="background:#fef2f2;border:1.5px solid #fca5a5;border-radius:8px;padding:14px 18px;margin:16px 0">'
            f'<div style="font-size:16px;font-weight:700;color:#dc2626;margin-bottom:8px">'
            f'🔔 立即進場候選 — {len(buy_stocks)} 支（T-3 至 T-10 交易日）</div>'
            f'{build_stock_rows(buy_stocks)}</div>'
        )

    if other_stocks:
        sections += (
            '<div style="background:#fffbeb;border:1.5px solid #fcd34d;border-radius:8px;padding:14px 18px;margin:16px 0">'
            f'<div style="font-size:16px;font-weight:700;color:#d97706;margin-bottom:8px">'
            f'📅 除息窗口內（尚未到進場時機） — {len(other_stocks)} 支</div>'
            f'{build_stock_rows(other_stocks)}</div>'
        )

    if watch_stocks:
        sections += (
            '<div style="background:#f0fdf4;border:1.5px solid #86efac;border-radius:8px;padding:14px 18px;margin:16px 0">'
            f'<div style="font-size:16px;font-weight:700;color:#15803d;margin-bottom:8px">'
            f'📋 觀察名單（等待除息日公告） — Top {len(watch_stocks)} 支（依殖利率排序）</div>'
            f'{build_stock_rows(watch_stocks)}</div>'
        )

    if not sections:
        sections = '<p style="color:#64748b;font-size:14px;padding:20px">本次掃描無符合條件股票。</p>'

    return (
        '<!DOCTYPE html><html lang="zh-TW">'
        '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        'max-width:980px;margin:0 auto;padding:16px;color:#0f172a;background:#fff">'
        '<div style="background:#0f172a;color:#f8fafc;padding:16px 20px;border-radius:8px;margin-bottom:12px">'
        '<div style="font-size:20px;font-weight:700">💰 除息前布局選股報告</div>'
        f'<div style="font-size:13px;opacity:.75;margin-top:4px">'
        f'掃描日期：{run_date}　報告產生：{report_time}<br>'
        f'篩選條件：{crit_str}</div></div>'
        '<div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:6px;'
        'padding:10px 14px;margin:12px 0;font-size:12px;color:#92400e">'
        '⚠️ 僅供研究參考，非投資建議，投資有風險請審慎評估。</div>'
        f'{sections}'
        '<p style="margin-top:24px;font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0;padding-top:12px">'
        '本報告由 GitHub Actions 自動產生，每 3 天更新一次（MoneyDJ 除權除息表）。<br>'
        'VCPfinder：<a href="https://vivian-vcpfinder.pages.dev/VCPfinder" style="color:#3b82f6">'
        'https://vivian-vcpfinder.pages.dev/VCPfinder</a></p>'
        '</body></html>'
    )


def send_via_brevo(subject: str, html: str) -> str:
    payload = {
        'sender':      {'name': FROM_NAME, 'email': FROM_EMAIL},
        'to':          [{'email': TO_EMAILS[0]}],
        'subject':     subject,
        'htmlContent': html,
    }
    if len(TO_EMAILS) > 1:
        payload['bcc'] = [{'email': e} for e in TO_EMAILS[1:]]
    resp = requests.post(
        'https://api.brevo.com/v3/smtp/email',
        json=payload,
        headers={'api-key': BREVO_API_KEY, 'Content-Type': 'application/json'},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f'Brevo API {resp.status_code}: {resp.text}')
    return resp.json().get('messageId', '?')


def main():
    if not BREVO_API_KEY:
        print('⚠  BREVO_API_KEY 未設定，請設定環境變數')
        sys.exit(1)

    data             = load_results()
    run_date         = data.get('run_date', '—')
    qualified_window = data.get('qualified_window', [])
    qualified_watch  = data.get('qualified_watch',  [])
    buy_count        = sum(1 for q in qualified_window if q.get('in_buy_window'))

    subject = f'💰 除息選股 {run_date} ─ 🔔進場 {buy_count} 支 / 窗口 {len(qualified_window)} 支 / 觀察 {len(qualified_watch)} 支'
    html     = build_html(data)
    email_id = send_via_brevo(subject, html)

    print(f'✉  Email sent → {", ".join(TO_EMAILS)}')
    print(f'   Subject : {subject}')
    print(f'   Brevo id: {email_id}')


if __name__ == '__main__':
    main()
