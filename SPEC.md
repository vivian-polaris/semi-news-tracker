# Vivian Edge · VCP Finder — 技術規格

> 此文件是系統設計的權威定義。修改架構或模組介面時必須同步更新。

## 系統架構

```
使用者瀏覽器
    │
    ├── VCPfinder.html（主應用，單一 HTML 檔）
    │       │
    │       ├── 資料來源 A：本地 JSON（快取，最快）
    │       │   ├── twse_daily.json     籌碼/財報/產業
    │       │   ├── stocks_tse.json     上市股票清單+歷史價格
    │       │   └── stocks_otc.json     上櫃股票清單+歷史價格
    │       │
    │       ├── 資料來源 B：Yahoo Finance（即時，CORS proxy）
    │       │   └── 個股歷史 OHLCV（個股快查用）
    │       │
    │       ├── 資料來源 C：TWSE T86 API（籌碼備援）
    │       │
    │       └── AI：/api/groq（Cloudflare Pages Function）
    │               └── 代理 Groq API（隱藏 API Key）
    │
    ├── index-pro.html（半導體新聞追蹤，次要產品）
    │
    └── [廢棄] index.html, better.html, index-card-n-list.html,
              index-sentence.html, index_time_period.html

GitHub Actions（自動排程）
    ├── fetch_daily.yml → scripts/fetch_twse_daily.py
    │   每天 16:45 台灣時間執行，更新 twse_daily.json
    │
    └── deploy.yml → 每次 push 自動部署到 Cloudflare Pages
```

## 主要模組（VCPfinder.html 內）

### 1. VCP 計算引擎 `calcVCP(data)`
- **輸入**：OHLCV 日線資料陣列（最新在前，至少 55 天）
- **輸出**：`{ score, price, ma50, ma150, ma200, stage2, atrL, atrS, atrRat, volL, volS, vRat, pctH, rsi, contractions, vcpPivot, distVcpPivot, pivotStatus, pivotNote, pp, ppR1, ppR2, ppS1, ppS2 }`
- **評分邏輯**：
  - Stage 2（MA排列）：+20 分
  - 距52週高 <15%：+15 分；<25%：+10 分
  - ATR收縮 <0.75：+30 分；<0.90：+15 分
  - 量縮 <0.70：+20 分；<0.85：+10 分
  - RSI 40–65：+5 分
  - 多段收縮 ≥3：+8 分；≥2：+4 分
- **Pivot**：近15個交易日最高點（壓縮形態頂部）
- **PP**：樞軸點（前日 H+L+C / 3）

### 2. CANSLIM 計算 `calcCANSLIM(vcp, fund, chips)`
- **C**：EPS YoY ≥25%（fallback 到月營收 YoY）
- **A**：ROE ≥15%（fallback 到 operatingMargin ≥12%）
- **N**：距52週高 <5%
- **S**：量縮（vRat <0.85）且收縮 ≥1 段
- **L**：VCP分數 ≥70
- **I**：外資或投信買超 >0
- **M**：Stage 2
- **輸出**：`{ C, A, N, S, L, I, M, total, letters }`（各項為 `true/false/null`）

### 3. 基本面計算 `calcFundGrade(fund)`
- ROE（30分）、毛利率（15分）、營業利益率（15分）、EPS成長（20分）、營收成長（15分）
- 回傳 0–100 分

### 4. 資料取得流程（掃描）
```
1. 優先從 stocks_tse.json / stocks_otc.json 取得歷史價格
2. 失敗時 fallback 到 Yahoo Finance CORS proxy（3個輪流）
3. 籌碼：A) localStorage快取 → B) twse_daily.json → C) TWSE T86 直連
4. 基本面：twse_daily.json（月營收/季報）+ Yahoo Finance quoteSummary
5. 借券賣出餘額（fetch_twse_daily.py）：A) TWSE `TWT93U`（第二組『前日餘額/當日餘額』，股→張）→ B) CF Worker /api/borrow → C) MI_SLBK（被封鎖）→ D) BFT41U → E) OpenAPI
6. 掃描名單代號規則：4 碼股票 或 ETF（`00` 開頭 + 3~4 碼數字 + 可選結尾英文字母，如 0050、00878、00679B、00981A）；ETF 產業標為「ETF」，無基本面/CANSLIM 資料
7. 啟動行為：每次開啟頁面自動呼叫 startScan()；有快取時先顯示快取
```

### 5. AI 新聞 `loadTrump()`
- RSS 新聞 → Groq API（via /api/groq）→ 繁中摘要 Top 10
- 強制要求：以色列-伊朗戰爭、川普表態、霍爾木茲海峽

### 6. 個股快查 `buildStockCard(vcpData, stockInfo, fund, chips, cs)`
- 顯示：買入潛力 / Pivot位置 / 賣出壓力（3欄同行）
- 買入潛力：VCP分數（0–100）
- Pivot位置：現價距突破線 %（綠=0~+3%，黃=±2%，紅=>+8%）
- 賣出壓力：超買信號（0–100）

### 7. 巴菲特護城河 + 十年收益率 `scripts/buffett_yoc.py`
- **介面**：`BuffettValuationModule(risk_free, discount_rate, growth).analyze(sym, price, ticker)`；函式版 `analyze_buffett_yield_on_cost()`；批次 `run_on_vcp_results()`
- **資料**：yfinance 年度財報（income_stmt/balance_sheet/cashflow + info）；買入價沿用 `vcp_daily.json` 的 `vcp.price`，可傳入現成 Ticker 物件共用快取
- **護城河 6 項**：ROE>20%、ROIC>15%、CapEx/OCF<30%、FCF/淨利≥0.9、長債/FCF<3、毛利率>50%（缺資料為 null，不算通過）
- **十年收益率**：逐年 EPS×(1+g)^n / 買入價；累積股息 vs 公債單利；黃金交叉年（收益率>rf）；15/20/25x 退場 IRR 與以折現率（預設=rf）計的安全邊際
- **入口**：`python scripts/buffett_yoc.py --from-vcp --min-vcp 70 --only-pass`；`python scripts/scan_vcp.py --buffett` 輸出 `buffett_daily.json`（vcp_scan.yml 已加此旗標，每次掃描更新）
- **前端**：VCPfinder.html 個股快查下方「巴菲特護城河 + 十年持有收益率」折疊卡，讀 `./buffett_daily.json`（VCP≥70 前 40 檔），可勾「只看 6 項全過」

## 資料結構

### twse_daily.json
```json
{
  "date": "20260419",
  "chips": { "2330": { "foreignNet": 1234, "trustNet": 56, "dealerNet": -78 } },
  "monthRevenue": { "2330": { "yoy": 12.5, "current": 1234567 } },
  "income": { "2330": { "eps": 9.58, "revenue": 628000, "operatingIncome": 230000, "netIncome": 195000, "year": "113", "quarter": "4" } }
}
```

### stocks_tse.json / stocks_otc.json
```json
{
  "2330": {
    "name": "台積電",
    "sector": "半導體",
    "history": [ { "date": "2026-04-19", "open": 900, "high": 920, "low": 895, "close": 910, "volume": 12345678 } ]
  }
}
```

## 部署架構

| 服務 | 用途 |
|------|------|
| Cloudflare Pages | 靜態 HTML 托管 + Pages Functions |
| `functions/api/groq.js` | Groq API Key 代理（server-side，Key 不暴露） |
| `functions/api/rss.js` | RSS 伺服器端代理（僅允許 news.google.com），`/api/rss?url=`，取代不穩定的免費 CORS proxy |
| GitHub Actions `deploy.yml` | push → 自動部署 |
| GitHub Actions `fetch_daily.yml` | 每天 16:45 更新資料 JSON |

## 安全約束

- Groq API Key 存在 Cloudflare Pages 環境變數，不得寫入前端
- 不得直接 call Groq API from browser（必須走 /api/groq）

## 修改此規格的規則

- 任何架構變更、新增模組、修改資料結構 → 必須先更新此文件
- 修改前端計算邏輯（VCP/CANSLIM公式）→ 必須更新「主要模組」章節
