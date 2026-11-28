#!/usr/bin/env python3
"""
巴菲特「可口可樂基因」篩選 + 十年持有收益率（Yield on Cost）模組

兩個部分：
  1. check_moat()      護城河檢驗（ROE/ROIC/CapEx比/FCF品質/負債/毛利率）
  2. yield_on_cost()   十年逐年持有收益率、累積現金流 vs 公債利息、黃金交叉、
                       15/20/25x 退場本益比的 IRR 與折現安全邊際

用法（從 repo 根目錄）：
  python scripts/buffett_yoc.py --tickers 2330.TW 2002.TW
  python scripts/buffett_yoc.py --from-vcp --min-vcp 70 --top 30 --only-pass
  python scripts/buffett_yoc.py --tickers 2330.TW --rf 0.016 --growth 0.08 --price 1000

也可被 scan_vcp.py 以 `--buffett` 旗標呼叫，或在程式內：
  from buffett_yoc import analyze_buffett_yield_on_cost
  r = analyze_buffett_yield_on_cost('2330.TW', price=1000, ticker=existing_yf_ticker)

重要假設（皆可用參數覆寫）：
  - 無風險利率 rf 預設 1.5%（台灣10年期公債約略值，請依當時殖利率調整）
  - 折現率預設 = rf（依需求以公債利率為基準；偏樂觀，建議另用 --discount 0.08 對照）
  - EPS 成長率預設取歷史年度淨利 CAGR，夾在 0~12%，算不出來用 5%
  - 現金流 = EPS × 配息率（保留盈餘已反映在 EPS 成長，避免與退場本益比重複計算）
  - 「當年持有收益率」= 當年 EPS / 買入價（巴菲特的 owner earnings 觀點）
"""
import argparse
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DEFAULT_RF = 0.015
DEFAULT_GROWTH = 0.05
GROWTH_CAP = 0.12
EXIT_PES = (15, 20, 25)
YEARS = 10

# 護城河門檻（對應「可口可樂基因」）
THRESH = {
    'roe': 0.20,          # ROE > 20%
    'roic': 0.15,         # ROIC > 15%
    'capex_ocf': 0.30,    # CapEx / OCF < 30%
    'fcf_ni': 0.90,       # FCF / 淨利 >= 0.9（接近或大於 1）
    'ltd_fcf': 3.0,       # 長期負債 / FCF < 3
    'gross_margin': 0.50, # 毛利率 > 50%
}


# ── 小工具 ────────────────────────────────────────────────────────────────

def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _row(df, names, col=0):
    """從 yfinance 財報 DataFrame 取第 col 欄（0=最新年度）的第一個存在的列；沒有則 None。"""
    if df is None or getattr(df, 'empty', True):
        return None
    for n in names:
        if n in df.index:
            vals = [_num(v) for v in df.loc[n].tolist()]
            if col < len(vals):
                return vals[col]
    return None


def _row_series(df, names):
    if df is None or getattr(df, 'empty', True):
        return []
    for n in names:
        if n in df.index:
            return [_num(v) for v in df.loc[n].tolist()]
    return []


def _irr(cashflows, lo=-0.99, hi=1.0):
    """二分法求 IRR；cashflows[0] 為 t=0。無法求解回 None。"""
    def npv(r):
        return sum(cf / (1 + r) ** t for t, cf in enumerate(cashflows))
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def _fmt_pct(x, d=1):
    return 'n/a' if x is None else f'{x * 100:.{d}f}%'


# ── 主模組 ────────────────────────────────────────────────────────────────

class BuffettValuationModule:
    def __init__(self, risk_free=DEFAULT_RF, discount_rate=None, growth=None,
                 years=YEARS, exit_pes=EXIT_PES):
        self.rf = risk_free
        self.discount = discount_rate if discount_rate is not None else risk_free
        self.growth = growth          # None → 由歷史淨利 CAGR 推估
        self.years = years
        self.exit_pes = tuple(exit_pes)

    # ---- 資料抽取（只用 yfinance Ticker 已有的屬性；傳入現成 ticker 可共用快取）----
    @staticmethod
    def extract_fundamentals(ticker):
        """回傳 dict：護城河所需指標 + eps/配息率/歷史淨利。缺值為 None。"""
        inc = getattr(ticker, 'income_stmt', None)
        bs = getattr(ticker, 'balance_sheet', None)
        cf = getattr(ticker, 'cashflow', None)
        try:
            info = ticker.info or {}
        except Exception:
            info = {}

        ni = _row(inc, ['Net Income', 'Net Income Common Stockholders'])
        rev = _row(inc, ['Total Revenue', 'Operating Revenue'])
        gp = _row(inc, ['Gross Profit'])
        ebit = _row(inc, ['EBIT', 'Operating Income'])
        tax = _row(inc, ['Tax Rate For Calcs'])
        tax = min(max(tax, 0.0), 0.4) if tax is not None else 0.2

        equity = _row(bs, ['Stockholders Equity', 'Common Stock Equity'])
        invested = _row(bs, ['Invested Capital'])
        if invested is None and equity is not None:
            debt = _row(bs, ['Total Debt']) or 0.0
            cash = _row(bs, ['Cash And Cash Equivalents']) or 0.0
            invested = equity + debt - cash
        ltd = _row(bs, ['Long Term Debt', 'Long Term Debt And Capital Lease Obligation'])
        ltd_assumed = ltd is None
        if ltd is None:
            ltd = 0.0   # yfinance 沒有此列通常代表無長期借款；於 notes 標註

        ocf = _row(cf, ['Operating Cash Flow', 'Cash Flow From Continuing Operating Activities'])
        capex = _row(cf, ['Capital Expenditure'])
        capex = abs(capex) if capex is not None else None
        fcf = _row(cf, ['Free Cash Flow'])
        if fcf is None and ocf is not None and capex is not None:
            fcf = ocf - capex

        ni_hist = [v for v in _row_series(inc, ['Net Income', 'Net Income Common Stockholders'])
                   if v is not None]

        eps = _num(info.get('trailingEps'))
        if eps is None and ni is not None and _num(info.get('sharesOutstanding')):
            eps = ni / info['sharesOutstanding']
        payout = _num(info.get('payoutRatio'))

        return {
            'netIncome': ni, 'revenue': rev, 'grossProfit': gp, 'ebit': ebit,
            'equity': equity, 'investedCapital': invested, 'longTermDebt': ltd,
            'ltdAssumedZero': ltd_assumed, 'ocf': ocf, 'capex': capex, 'fcf': fcf,
            'roe': (ni / equity) if (ni is not None and equity) else _num(info.get('returnOnEquity')),
            'roic': (ebit * (1 - tax) / invested) if (ebit is not None and invested) else None,
            'grossMargin': (gp / rev) if (gp is not None and rev) else _num(info.get('grossMargins')),
            'eps': eps, 'payout': payout, 'niHistory': ni_hist,
            'price': _num(info.get('currentPrice')) or _num(info.get('regularMarketPrice')),
        }

    # ---- 1. 護城河檢驗 ----
    def check_moat(self, f):
        T = THRESH
        ocf, capex, fcf, ni = f['ocf'], f['capex'], f['fcf'], f['netIncome']
        vals = {
            'roe': f['roe'],
            'roic': f['roic'],
            'capex_ocf': (capex / ocf) if (capex is not None and ocf and ocf > 0) else None,
            'fcf_ni': (fcf / ni) if (fcf is not None and ni and ni > 0) else None,
            'ltd_fcf': (f['longTermDebt'] / fcf) if (fcf is not None and fcf > 0) else None,
            'gross_margin': f['grossMargin'],
        }
        def ok(k):
            v = vals[k]
            if v is None:
                return None
            if k in ('capex_ocf', 'ltd_fcf'):
                return v < T[k]
            if k == 'fcf_ni':
                return v >= T[k]
            return v > T[k]
        checks = {k: ok(k) for k in vals}
        # FCF<=0 時長期負債/FCF 無意義 → 視為未通過（不是缺資料）
        if checks['ltd_fcf'] is None and fcf is not None and fcf <= 0:
            checks['ltd_fcf'] = False
        passed = sum(1 for v in checks.values() if v is True)
        return {
            'values': vals, 'checks': checks, 'passed': passed, 'total': len(checks),
            'allPass': passed == len(checks),
            'missing': [k for k, v in checks.items() if v is None],
        }

    # ---- 成長率 ----
    def _growth(self, f):
        if self.growth is not None:
            return self.growth
        h = f.get('niHistory') or []
        if len(h) >= 2 and h[0] and h[-1] and h[0] > 0 and h[-1] > 0:
            cagr = (h[0] / h[-1]) ** (1 / (len(h) - 1)) - 1   # h[0]=最新
            return min(max(cagr, 0.0), GROWTH_CAP)
        return DEFAULT_GROWTH

    # ---- 2. 十年持有收益率 ----
    def yield_on_cost(self, eps, price, growth=None, payout=None):
        if not eps or eps <= 0 or not price or price <= 0:
            return None
        g = self.growth if growth is None else growth
        if g is None:
            g = DEFAULT_GROWTH
        payout = 0.5 if payout is None else min(max(payout, 0.2), 1.0)
        rf, r = self.rf, self.discount

        rows, cum_earn, cum_div, cum_bond = [], 0.0, 0.0, 0.0
        golden = payback = cum_cross = None
        divs = []
        for n in range(1, self.years + 1):
            e = eps * (1 + g) ** n
            d = e * payout
            cum_earn += e
            cum_div += d
            cum_bond += price * rf          # 同本金買公債的單利利息（不複利）
            yoc = e / price
            if golden is None and yoc > rf:
                golden = n
            if cum_cross is None and cum_div > cum_bond:
                cum_cross = n
            if payback is None and cum_earn >= price:
                payback = n
            divs.append(d)
            rows.append({
                'year': n, 'eps': round(e, 3), 'yoc': round(yoc, 4),
                'dividend': round(d, 3), 'cumEarnings': round(cum_earn, 2),
                'cumDividends': round(cum_div, 2), 'cumBondInterest': round(cum_bond, 2),
            })

        eps10 = eps * (1 + g) ** self.years
        exits = {}
        pv_div = sum(d / (1 + r) ** (i + 1) for i, d in enumerate(divs))
        for pe in self.exit_pes:
            exit_val = eps10 * pe
            irr = _irr([-price] + divs[:-1] + [divs[-1] + exit_val])
            iv = pv_div + exit_val / (1 + r) ** self.years
            exits[str(pe)] = {
                'exitPrice': round(exit_val, 2),
                'irr': None if irr is None else round(irr, 4),
                'intrinsicValue': round(iv, 2),
                'marginOfSafety': round((iv - price) / iv, 4) if iv > 0 else None,
            }
        return {
            'eps0': round(eps, 3), 'price': price, 'growth': round(g, 4), 'payout': round(payout, 3),
            'rf': rf, 'discount': r, 'years': rows,
            'goldenCrossYear': golden,        # 首次 當年收益率 > 公債利率
            'cumCrossYear': cum_cross,        # 首次 累積股息 > 累積公債利息
            'paybackYear': payback,           # 累積盈餘回收買入成本
            'exits': exits,
        }

    # ---- 入口 ----
    def analyze(self, sym, price=None, ticker=None):
        out = {'sym': sym, 'error': None}
        try:
            if ticker is None:
                import yfinance as yf
                ticker = yf.Ticker(sym)
            f = self.extract_fundamentals(ticker)
        except Exception as e:
            out['error'] = f'fetch failed: {e}'
            return out
        price = price or f['price']
        out['moat'] = self.check_moat(f)
        out['fundamentals'] = {k: f[k] for k in ('roe', 'roic', 'grossMargin', 'eps', 'payout')}
        out['notes'] = ['長期負債無資料，視為 0'] if f['ltdAssumedZero'] else []
        out['yoc'] = self.yield_on_cost(f['eps'], price, self._growth(f), f['payout'])
        if out['yoc'] is None:
            out['error'] = 'EPS 或價格無效（EPS<=0 或缺資料），無法計算收益率'
        return out


def analyze_buffett_yield_on_cost(sym, price=None, ticker=None, **kwargs):
    """函式介面：analyze_buffett_yield_on_cost('2330.TW', price=1000, risk_free=0.016)"""
    return BuffettValuationModule(**kwargs).analyze(sym, price=price, ticker=ticker)


# ── 報表 / 批次 ───────────────────────────────────────────────────────────

LABELS = {'roe': 'ROE>20%', 'roic': 'ROIC>15%', 'capex_ocf': 'CapEx/OCF<30%',
          'fcf_ni': 'FCF/淨利≥0.9', 'ltd_fcf': '長債/FCF<3', 'gross_margin': '毛利率>50%'}


def print_report(r, name=''):
    print('=' * 70)
    print(f"{r['sym']} {name}")
    if 'moat' not in r:
        print('  ERROR:', r['error']); return
    m = r['moat']
    print(f"護城河 {m['passed']}/{m['total']} {'✅ 通過' if m['allPass'] else '❌ 未全過'}")
    for k, lab in LABELS.items():
        v, c = m['values'][k], m['checks'][k]
        mark = '✅' if c else ('？' if c is None else '❌')
        vs = 'n/a' if v is None else (f'{v:.2f}' if k in ('capex_ocf', 'fcf_ni', 'ltd_fcf') and k != 'capex_ocf' else _fmt_pct(v))
        print(f'  {mark} {lab:<14} {vs}')
    for n in r.get('notes', []):
        print('  ※', n)
    y = r.get('yoc')
    if not y:
        print('  收益率：', r['error']); return
    print(f"\n買入價 {y['price']}  EPS {y['eps0']}  成長 {_fmt_pct(y['growth'])}  配息率 {_fmt_pct(y['payout'],0)}"
          f"  rf {_fmt_pct(y['rf'])}  折現 {_fmt_pct(y['discount'])}")
    print(f"{'年':>3} {'EPS':>8} {'收益率':>7} {'累積股息':>9} {'累積公債息':>10}")
    for row in y['years']:
        print(f"{row['year']:>3} {row['eps']:>8.2f} {_fmt_pct(row['yoc']):>7} "
              f"{row['cumDividends']:>9.2f} {row['cumBondInterest']:>10.2f}")
    print(f"黃金交叉年(收益率>公債) = {y['goldenCrossYear']}　累積股息>累積公債息 = {y['cumCrossYear']}　盈餘回本 = {y['paybackYear']}")
    for pe, e in y['exits'].items():
        print(f"  退場 {pe}x  賣價 {e['exitPrice']:.1f}  IRR {_fmt_pct(e['irr'])}  "
              f"內在價值 {e['intrinsicValue']:.1f}  安全邊際 {_fmt_pct(e['marginOfSafety'])}")


def run_on_items(items, module, workers=4):
    """items: [(sym, name, price)]；yfinance 請求以執行緒並行。回傳 list[dict]。"""
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(module.analyze, s, p): (s, n) for s, n, p in items}
        for fu in as_completed(futs):
            s, n = futs[fu]
            r = fu.result(); r['name'] = n
            results.append(r)
    order = {s: i for i, (s, _, _) in enumerate(items)}
    results.sort(key=lambda r: order.get(r['sym'], 0))
    return results


def vcp_items(vcp_path, min_vcp=70, top=None):
    """從 vcp_daily.json（VCP 掃描輸出）取代號/名稱/現價，共用其價格，不重複抓。"""
    with open(vcp_path, encoding='utf-8') as fh:
        data = json.load(fh)
    stocks = [s for s in data.get('stocks', []) if s['vcp']['score'] >= min_vcp]
    stocks.sort(key=lambda s: s['vcp']['score'], reverse=True)
    if top:
        stocks = stocks[:top]
    return [(s['sym'], s['name'], s['vcp'].get('price')) for s in stocks]


def run_on_vcp_results(vcp_results, out_path, min_vcp=70, top=30, **mod_kwargs):
    """供 scan_vcp.py 呼叫：vcp_results 為其 filtered 清單。"""
    items = [(r['sym'], r['name'], r['vcp'].get('price'))
             for r in vcp_results if r['vcp']['score'] >= min_vcp][:top]
    res = run_on_items(items, BuffettValuationModule(**mod_kwargs))
    Path(out_path).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    return res


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    ap = argparse.ArgumentParser(description='巴菲特護城河 + 十年持有收益率')
    ap.add_argument('--tickers', nargs='*', help='如 2330.TW 2002.TW')
    ap.add_argument('--from-vcp', action='store_true', help='讀 vcp_daily.json 的 VCP 候選股')
    ap.add_argument('--vcp-file', default=str(Path(__file__).parent.parent / 'vcp_daily.json'))
    ap.add_argument('--min-vcp', type=int, default=70, help='VCP 分數下限')
    ap.add_argument('--top', type=int, default=30, help='最多分析幾檔')
    ap.add_argument('--only-pass', action='store_true', help='只顯示護城河全過的')
    ap.add_argument('--price', type=float, help='買入價（僅單檔時有效；預設用最新價）')
    ap.add_argument('--rf', type=float, default=DEFAULT_RF, help='無風險利率，預設 0.015')
    ap.add_argument('--discount', type=float, help='折現率，預設等於 rf')
    ap.add_argument('--growth', type=float, help='EPS 年成長率，預設用歷史淨利 CAGR')
    ap.add_argument('--out', help='輸出 JSON 路徑')
    a = ap.parse_args(argv)

    items = []
    if a.from_vcp:
        items += vcp_items(a.vcp_file, a.min_vcp, a.top)
    for t in a.tickers or []:
        items.append((t, '', a.price if len(a.tickers) == 1 else None))
    if not items:
        ap.error('請給 --tickers 或 --from-vcp')

    mod = BuffettValuationModule(a.rf, a.discount, a.growth)
    results = run_on_items(items, mod)
    shown = [r for r in results if not a.only_pass or r.get('moat', {}).get('allPass')]
    for r in shown:
        print_report(r, r.get('name', ''))
    print(f'\n共 {len(results)} 檔，護城河全過 {sum(1 for r in results if r.get("moat", {}).get("allPass"))} 檔')
    if a.out:
        Path(a.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
        print('已輸出', a.out)


if __name__ == '__main__':
    main()
