#!/usr/bin/env python3
"""Taiwan Futures huge-volume black-candle alert — sends Brevo email when TXF=F prints a giant bearish candle."""

import os, json, sys
from datetime import datetime, timezone, timedelta

import yfinance as yf
import requests

SYMBOL           = 'TXF=F'
STATE_FILE       = 'alert_state.json'
COOLDOWN_SEC     = 30 * 60   # 30 min between alerts
VOLUME_MULT      = 3         # volume must exceed N * 20-bar average
ROLLING_WINDOW   = 20

TW_TZ       = timezone(timedelta(hours=8))
MARKET_OPEN = timedelta(hours=9, minutes=0)
MARKET_CLOSE = timedelta(hours=13, minutes=45)

BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '')
FROM_EMAIL    = 'polaris.sequoia@gmail.com'
TO_EMAIL      = 'polaris.sequoia@gmail.com'


def in_market_hours():
    now_tw = datetime.now(TW_TZ)
    t = now_tw - now_tw.replace(hour=0, minute=0, second=0, microsecond=0)
    return MARKET_OPEN <= t <= MARKET_CLOSE


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)


def send_email(subject, html):
    if not BREVO_API_KEY:
        print('BREVO_API_KEY not set, skipping email')
        return
    resp = requests.post(
        'https://api.brevo.com/v3/smtp/email',
        json={
            'sender':      {'name': '期貨警報', 'email': FROM_EMAIL},
            'to':          [{'email': TO_EMAIL}],
            'subject':     subject,
            'htmlContent': html,
        },
        headers={'api-key': BREVO_API_KEY, 'Content-Type': 'application/json'},
        timeout=20,
    )
    if not resp.ok:
        print(f'Brevo error {resp.status_code}: {resp.text}')
    else:
        print(f'Email sent: {subject}')


def build_html(alert_time, open_, close, high, low, vol, avg_vol, drop_pct):
    color = '#dc2626'
    return f'''<!DOCTYPE html><html lang="zh-TW"><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,sans-serif;max-width:560px;margin:0 auto;padding:16px;color:#0f172a">
<div style="background:#0f172a;color:#f8fafc;padding:16px 20px;border-radius:8px;margin-bottom:12px">
  <div style="font-size:20px;font-weight:700">&#x1F6A8; 台指期天量黑K警報</div>
  <div style="font-size:13px;opacity:.75;margin-top:4px">觸發時間：{alert_time}（台北）</div>
</div>
<table style="width:100%;border-collapse:collapse;font-size:14px">
  <tr style="background:#fef2f2"><td style="padding:8px 12px;font-weight:700;color:{color}">跌幅</td><td style="padding:8px 12px;font-weight:700;color:{color}">{drop_pct:.2f}%</td></tr>
  <tr><td style="padding:8px 12px;color:#64748b">開盤</td><td style="padding:8px 12px">{open_:.0f}</td></tr>
  <tr style="background:#f8fafc"><td style="padding:8px 12px;color:#64748b">收盤</td><td style="padding:8px 12px;color:{color};font-weight:700">{close:.0f}</td></tr>
  <tr><td style="padding:8px 12px;color:#64748b">最高</td><td style="padding:8px 12px">{high:.0f}</td></tr>
  <tr style="background:#f8fafc"><td style="padding:8px 12px;color:#64748b">最低</td><td style="padding:8px 12px">{low:.0f}</td></tr>
  <tr><td style="padding:8px 12px;color:#64748b">成交量</td><td style="padding:8px 12px;font-weight:700;color:{color}">{vol:,.0f}</td></tr>
  <tr style="background:#f8fafc"><td style="padding:8px 12px;color:#64748b">20根均量</td><td style="padding:8px 12px">{avg_vol:,.0f}</td></tr>
  <tr><td style="padding:8px 12px;color:#64748b">量比</td><td style="padding:8px 12px;font-weight:700;color:{color}">{vol/avg_vol:.1f}x</td></tr>
</table>
<p style="font-size:11px;color:#94a3b8;margin-top:16px">台指期（TXF=F）1分K｜天量黑K定義：量 &gt; {VOLUME_MULT}x 20根均量 + 收&lt;開</p>
</body></html>'''


def main():
    if not in_market_hours():
        print('Outside market hours, exit')
        sys.exit(0)

    print(f'Fetching {SYMBOL} 1m data...')
    df = yf.Ticker(SYMBOL).history(period='1d', interval='1m')
    if df.empty or len(df) < ROLLING_WINDOW + 1:
        print(f'Insufficient data ({len(df)} bars), exit')
        sys.exit(0)

    latest  = df.iloc[-1]
    open_   = latest['Open']
    close   = latest['Close']
    high    = latest['High']
    low     = latest['Low']
    vol     = latest['Volume']
    avg_vol = df['Volume'].iloc[-(ROLLING_WINDOW + 1):-1].mean()

    is_black    = close < open_
    is_huge_vol = avg_vol > 0 and vol > VOLUME_MULT * avg_vol

    print(f'Latest: O={open_:.0f} C={close:.0f} V={vol:.0f} avgV={avg_vol:.0f} black={is_black} huge={is_huge_vol}')

    if not (is_black and is_huge_vol):
        print('No alert condition met')
        sys.exit(0)

    state = load_state()
    last_ts = state.get('last_alert_time')
    if last_ts:
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(last_ts)).total_seconds()
        if elapsed < COOLDOWN_SEC:
            print(f'Cooldown active ({elapsed/60:.0f}m ago), skip')
            sys.exit(0)

    drop_pct    = (close - open_) / open_ * 100
    alert_time  = datetime.now(TW_TZ).strftime('%Y-%m-%d %H:%M')
    subject     = f'[Alert] TXF black candle {alert_time} vol:{vol:,.0f} avg:{avg_vol:,.0f} drop:{drop_pct:.2f}%'
    html        = build_html(alert_time, open_, close, high, low, vol, avg_vol, drop_pct)

    send_email(subject, html)

    state['last_alert_time'] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print('State updated, done')


if __name__ == '__main__':
    main()
