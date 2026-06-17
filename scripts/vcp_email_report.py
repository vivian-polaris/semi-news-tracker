#!/usr/bin/env python3
"""VCP 每日選股 Email 報告 — 讀 vcp_daily.json，透過 Resend API 發送 HTML 報告"""

import json, os, sys, datetime
from pathlib import Path
import requests

BREVO_API_KEY  = os.environ.get('BREVO_API_KEY') or os.environ.get('RESEND_API_KEY', '')
TO_EMAILS      = ['polaris.sequoia@gmail.com', 'lightall.blog@gmail.com', 'vast.gamma@gmail.com']
FROM_EMAIL     = 'polaris.sequoia@gmail.com'
FROM_NAME      = 'VCP選股'

MAX_STALE_DAYS = 4
DEFAULT_SEC_FILTER = {
    'IC設計', 'PCB', '伺服器', '其他電子', '化學', '化工', '半導體', '散熱', '數位雲端', '油電燃氣',
    '精密機械', '被動元件', '記憶體', '資訊服務', '通信網路', '連接器', '電子', '電子代工', '電子通路',
    '電子零組件', '電機', '電源', '電腦', '面板'
}

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

def calc_canslim(vcp, fund):
    """1:1 port of calcCANSLIM() from VCPfinder.html (I criterion skipped: no chips data)"""
    eg = fund.get('earningsGrowth') if fund else None
    if isinstance(eg, float) and eg != eg:  # NaN
        eg = None
    c_growth = eg if eg is not None else (fund.get('revenueGrowth') if fund else None)
    C = (c_growth >= 0.25) if c_growth is not None else None

    roe = fund.get('roe') if fund else None
    op  = fund.get('operatingMargin') if fund else None
    A = (roe >= 0.15) if roe is not None else ((op >= 0.12) if op is not None else None)

    N = (vcp['pctH'] < 15) if vcp else None

    v_rat = vcp.get('vRat') if vcp else None
    cont  = vcp.get('contractions', 0) if vcp else 0
    S = ((v_rat < 0.85 and cont >= 2) if v_rat is not None else (cont >= 2)) if vcp else None

    L = (vcp['score'] >= 70) if vcp else None
    M = (vcp.get('stage2', False)) if vcp else None

    return sum(1 for v in [C, A, N, S, L, M] if v is True)


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

def load_twse_daily():
    """讀 twse_daily.json 取 gdr_danger 地雷名單"""
    p = Path('twse_daily.json')
    if not p.exists():
        return {}
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def build_gdr_danger_section(daily):
    """產生 GDR 地雷警示 HTML 段落"""
    danger_list = daily.get('gdr_danger', [])
    if not danger_list:
        return ''

    rows = ''
    for d in danger_list:
        level_color = '#dc2626' if d.get('score', 0) >= 7 else ('#d97706' if d.get('score', 0) >= 5 else '#7c3aed')
        sigs = '・'.join(d.get('signals', []))
        rows += (
            f'<tr style="border-bottom:1px solid #fee2e2">'
            f'<td style="padding:8px 10px;font-weight:700;color:{level_color}">{d["code"]}</td>'
            f'<td style="padding:8px 10px">{d.get("name","")}</td>'
            f'<td style="padding:8px 10px;font-size:12px;color:#64748b">{d.get("note","")[:50]}</td>'
            f'<td style="padding:8px 10px;font-weight:700;color:{level_color}">{d.get("level","")}</td>'
            f'<td style="padding:8px 10px;font-size:12px;color:#dc2626">{sigs}</td>'
            f'</tr>'
        )

    return (
        '<div style="background:#fef2f2;border:1.5px solid #fca5a5;border-radius:8px;'
        'padding:14px 18px;margin:16px 0">'
        '<div style="font-size:16px;font-weight:700;color:#dc2626;margin-bottom:6px">'
        f'🚨 GDR 地雷警示 — 今日發現 {len(danger_list)} 支危險股票</div>'
        '<div style="font-size:12px;color:#7f1d1d;margin-bottom:10px">'
        'GDR 發行稀釋股本 + 借券放空 + 散戶接刀 = 大戶套利收割地雷，務必迴避做多</div>'
        '<table style="width:100%;border-collapse:collapse;font-size:13px">'
        '<thead><tr style="background:#fee2e2;font-size:11px;font-weight:700">'
        '<th style="padding:6px 10px;text-align:left">代號</th>'
        '<th style="padding:6px 10px;text-align:left">名稱</th>'
        '<th style="padding:6px 10px;text-align:left">GDR 資訊</th>'
        '<th style="padding:6px 10px;text-align:left">風險</th>'
        '<th style="padding:6px 10px;text-align:left">警示訊號</th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table></div>'
    )


def build_html(data):
    stocks        = data.get('stocks', [])
    scan_date     = data.get('scanDate', '—')
    scan_time     = data.get('scanTime', '—')
    total_scanned = data.get('totalScanned', 0)
    total_stocks  = data.get('totalStocks', 0)

    # 套用與 VCPfinder.html 相同的預設 filter：VCP≥80、CANSLIM≥5（I項無chips資料略過）
    filtered = [
        s for s in stocks
        if s['vcp']['score'] >= 80
        and calc_canslim(s['vcp'], s.get('fund')) >= 5
        and s.get('sector', '') in DEFAULT_SEC_FILTER
    ]

    tz_tw       = datetime.timezone(datetime.timedelta(hours=8))
    report_time = datetime.datetime.now(tz_tw).strftime('%Y-%m-%d %H:%M')

    # GDR 地雷段落
    daily = load_twse_daily()
    gdr_danger_section = build_gdr_danger_section(daily)

    sections = ''
    if gdr_danger_section:
        sections += gdr_danger_section
    if filtered:
        sections += (f'<h3 style="color:#dc2626;margin:24px 0 8px">'
                     f'🔥 VCP候選（VCP≥80 + CANSLIM≥5，共 {len(filtered)} 支）</h3>{build_table(filtered)}')
    if not sections:
        sections = '<p style="color:#64748b;font-size:14px">今日無符合條件的 VCP 候選</p>'

    return (
        '<!DOCTYPE html><html lang="zh-TW">'
        '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        'max-width:940px;margin:0 auto;padding:16px;color:#0f172a;background:#fff">'
        '<div style="background:#0f172a;color:#f8fafc;padding:16px 20px;border-radius:8px;margin-bottom:4px">'
        '<div style="font-size:20px;font-weight:700">📈 VCP 每日選股報告</div>'
        f'<div style="font-size:13px;opacity:.75;margin-top:4px">'
        f'掃描日期：{scan_date} {scan_time}　掃描股數：{total_scanned:,}/{total_stocks:,}'
        f'　VCP候選(原始)：{len(stocks)} 支　報告產生：{report_time}</div></div>'
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

def send_via_brevo(subject, html):
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
    data      = load_vcp()
    stocks    = data.get('stocks', [])
    scan_date = data.get('scanDate', '—')
    filtered_count = sum(
        1 for s in stocks
        if s['vcp']['score'] >= 80
        and calc_canslim(s['vcp'], s.get('fund')) >= 5
        and s.get('sector', '') in DEFAULT_SEC_FILTER
    )
    daily         = load_twse_daily()
    danger_count  = len(daily.get('gdr_danger', []))

    subject = f'📈 VCP {scan_date} ─ {filtered_count} 支候選'
    if danger_count:
        subject += f' ／ 🚨 {danger_count} 支GDR地雷'
    html    = build_html(data)
    email_id = send_via_brevo(subject, html)

    print(f'✉  Email sent → {", ".join(TO_EMAILS)}')
    print(f'   Subject : {subject}')
    print(f'   Brevo id: {email_id}')

if __name__ == '__main__':
    main()
