#!/usr/bin/env python3
"""VCP 每日選股 Email 報告 — 讀 vcp_daily.json，透過 Resend API 發送 HTML 報告"""

import json, os, sys, datetime
from pathlib import Path
import requests

RESEND_API_KEY = os.environ['RESEND_API_KEY']
TO_EMAILS      = ['polaris.sequoia@gmail.com', 'lightall.blog@gmail.com', 'vast.gamma@gmail.com']
FROM_EMAIL     = 'VCP選股 <onboarding@resend.dev>'

MAX_STALE_DAYS = 4

def load_vcp():
    p = Path('vcp_daily.json')
    if not p.exists():
        print('⚠  vcp_daily.json not found — run vcp_scan workflow first.')
        sys.exit(1)
    with open(p, encoding='utf-8') as f:
        return json.load(f)

_SIG_CSS = {
    'g': 'background:#16a34a;color:#fff',
    'o': 'background:#d97706;color:#fff',
    'r': 'background:#dc2626;color:#fff',
    'n': 'background:#94a3b8;color:#fff',
}

def badge(sig, label):
    css = _SIG_CSS.get(sig, _SIG_CSS['n'])
    return f'<span style="{css};padding:2px 7px;border-radius:3px;font-size:11px;white-space:nowrap">{label}</span>'

def pct_color(pct):
    if pct < 15: return '#16a34a'
    if pct < 25: return '#d97706'
    return '#dc2626'

def score_color(s):
    if s >= 80: return '#dc2626'
    if s >= 60: return '#d97706'
    return '#2563eb'

def build_table(stocks):
    if not stocks:
        return '<p style="color:#94a3b8;font-size:13px">（無符合條件股票）</p>'
    rows = []
    for s in stocks:
        v   = s['vcp']
        ex  = '上市' if s.get('ex') == 'TW' else '上櫃'
        sc  = v['score']
        pct = v.get('pctH', 0)
        rows.append(
            f'<tr style="border-bottom:1px solid #f1f5f9">'
            f'<td style="padding:5px 8px;font-weight:600">{s["code"]}</td>'
            f'<td style="padding:5px 8px">{s.get("name", s["code"])}</td>'
            f'<td style="padding:5px 8px;text-align:center;font-size:11px;color:#64748b">{ex}</td>'
            f'<td style="padding:5px 8px;text-align:center;font-weight:700;color:{score_color(sc)}">{sc}</td>'
            f'<td style="padding:5px 8px;text-align:right">{v["price"]}</td>'
            f'<td style="padding:5px 8px;text-align:center;color:{pct_color(pct)}">{pct}%</td>'
            f'<td style="padding:5px 8px;text-align:center">{badge(v.get("atrS","n"), v.get("atrL","—"))}</td>'
            f'<td style="padding:5px 8px;text-align:center">{badge(v.get("volS","n"), v.get("volL","—"))}</td>'
            f'<td style="padding:5px 8px;text-align:center">{"✅" if v.get("stage2") else "—"}</td>'
            f'<td style="padding:5px 8px;text-align:center">{"✅" if v.get("perfOrd") else "—"}</td>'
            f'</tr>'
        )
    header = (
        '<tr style="background:#f1f5f9;font-size:11px;color:#475569">'
        '<th style="padding:5px 8px;text-align:left">代號</th>'
        '<th style="padding:5px 8px;text-align:left">名稱</th>'
        '<th style="padding:5px 8px">市場</th>'
        '<th style="padding:5px 8px">分數</th>'
        '<th style="padding:5px 8px">收盤</th>'
        '<th style="padding:5px 8px">距高%</th>'
        '<th style="padding:5px 8px">ATR</th>'
        '<th style="padding:5px 8px">量能</th>'
        '<th style="padding:5px 8px">第二段</th>'
        '<th style="padding:5px 8px">均線序</th>'
        '</tr>'
    )
    return (
        '<table style="width:100%;border-collapse:collapse;font-size:13px;'
        'border:1px solid #e2e8f0;border-radius:6px;overflow:hidden">'
        f'{header}{"".join(rows)}</table>'
    )

def staleness_banner(scan_date_str):
    try:
        delta = (datetime.date.today() - datetime.date.fromisoformat(scan_date_str)).days
        if delta >= MAX_STALE_DAYS:
            return (
                f'<div style="background:#fee2e2;border:1px solid #fca5a5;border-radius:6px;'
                f'padding:10px 14px;margin:12px 0;font-size:13px;color:#991b1b">'
                f'⚠️ 資料已 {delta} 天未更新（最後掃描：{scan_date_str}），'
                f'請確認 vcp_scan workflow 正常執行。</div>'
            )
    except Exception:
        pass
    return ''

def build_html(data):
    stocks        = data.get('stocks', [])
    scan_date     = data.get('scanDate', '—')
    scan_time     = data.get('scanTime', '—')
    total_scanned = data.get('totalScanned', 0)
    total_stocks  = data.get('totalStocks', 0)

    hot   = [s for s in stocks if s['vcp']['score'] >= 80]
    good  = [s for s in stocks if 60 <= s['vcp']['score'] < 80]
    watch = [s for s in stocks if 40 <= s['vcp']['score'] < 60][:20]

    tz_tw       = datetime.timezone(datetime.timedelta(hours=8))
    report_time = datetime.datetime.now(tz_tw).strftime('%Y-%m-%d %H:%M')

    sections = ''
    if hot:
        sections += (f'<h3 style="color:#dc2626;margin:24px 0 8px">'
                     f'🔥 強力候選（80分以上，共 {len(hot)} 支）</h3>{build_table(hot)}')
    if good:
        sections += (f'<h3 style="color:#d97706;margin:24px 0 8px">'
                     f'🟢 良好候選（60–79分，共 {len(good)} 支）</h3>{build_table(good)}')
    if watch:
        sections += (f'<h3 style="color:#2563eb;margin:24px 0 8px">'
                     f'🟡 觀察名單（40–59分，顯示前 {len(watch)} 支）</h3>{build_table(watch)}')
    if not sections:
        sections = '<p style="color:#64748b;font-size:14px">今日無符合條件的 VCP 候選（分數 ≥ 40）</p>'

    return (
        '<!DOCTYPE html><html lang="zh-TW">'
        '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        'max-width:940px;margin:0 auto;padding:16px;color:#0f172a;background:#fff">'
        '<div style="background:#0f172a;color:#f8fafc;padding:16px 20px;border-radius:8px;margin-bottom:4px">'
        '<div style="font-size:20px;font-weight:700">📈 VCP 每日選股報告</div>'
        f'<div style="font-size:13px;opacity:.75;margin-top:4px">'
        f'掃描日期：{scan_date} {scan_time}　掃描股數：{total_scanned:,}/{total_stocks:,}'
        f'　VCP 候選：{len(stocks)} 支　報告產生：{report_time}</div></div>'
        f'{staleness_banner(scan_date)}'
        '<div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:6px;'
        'padding:10px 14px;margin:12px 0;font-size:12px;color:#92400e">'
        '⚠️ 僅供研究參考，非投資建議，投資有風險請審慎評估。</div>'
        f'{sections}'
        '<p style="margin-top:24px;font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0;padding-top:12px">'
        '本報告由 GitHub Actions 自動產生，每個交易日 08:00（台北時間）發送。<br>'
        'VCPfinder：<a href="https://vivian-vcpfinder.pages.dev/VCPfinder" style="color:#3b82f6">'
        'https://vivian-vcpfinder.pages.dev/VCPfinder</a></p>'
        '</body></html>'
    )

def send_via_resend(subject, html):
    resp = requests.post(
        'https://api.resend.com/emails',
        json={
            'from':    FROM_EMAIL,
            'to':      TO_EMAILS,
            'subject': subject,
            'html':    html,
        },
        headers={'Authorization': f'Bearer {RESEND_API_KEY}'},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f'Resend API {resp.status_code}: {resp.text}')
    return resp.json().get('id', '?')

def main():
    data      = load_vcp()
    stocks    = data.get('stocks', [])
    scan_date = data.get('scanDate', '—')
    hot_count = sum(1 for s in stocks if s['vcp']['score'] >= 60)

    subject = f'📈 VCP {scan_date} ─ {hot_count} 支高分候選'
    html    = build_html(data)
    email_id = send_via_resend(subject, html)

    print(f'✉  Email sent → {", ".join(TO_EMAILS)}')
    print(f'   Subject : {subject}')
    print(f'   Resend id: {email_id}')

if __name__ == '__main__':
    main()
