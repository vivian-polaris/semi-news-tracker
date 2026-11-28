# Vivian Edge · VCP Finder — 現況文件

> 此文件是專案的單一真相來源。每次代碼變更後必須同步更新。

**最後更新**：2026-11-17
**當前版本**：v2.0（CANSLIM + Pivot + 籌碼三層備援）

## 模組實現狀態

| 模組 | 檔案 | 狀態 | 說明 |
|------|------|------|------|
| VCP 掃描引擎 | `VCPfinder.html` | ✅ 完成 | calcVCP(), 評分0-100 |
| CANSLIM 評分 | `VCPfinder.html` | ✅ 完成 | 7項指標，C用EPS/營收YoY |
| 基本面評分 | `VCPfinder.html` | ✅ 完成 | calcFundGrade(), 0-100 |
| 籌碼顯示 | `VCPfinder.html` | ✅ 完成 | 三層備援（快取→JSON→T86） |
| 個股快查 | `VCPfinder.html` | ✅ 完成 | 買入潛力/Pivot/賣出壓力3欄 |
| Pivot Point | `VCPfinder.html` | ✅ 完成 | 近15日高點，量價確認邏輯 |
| AI 市場新聞 | `VCPfinder.html` | ✅ 完成 | Groq API via /api/groq |
| 美林投資時鐘 | `VCPfinder.html` | ✅ 完成 | loadClock() |
| 手機版 UI | `VCPfinder.html` | ✅ 完成 | 掃描狀態同一行、技術信號2行 |
| Groq API 代理 | `functions/api/groq.js` | ✅ 完成 | Key server-side |
| 每日資料抓取 | `scripts/fetch_twse_daily.py` | ✅ 完成 | 籌碼+月營收+季報 |
| VCP 預掃描 | `scripts/scan_vcp.py` | ⚠️ 待確認 | vcp_daily.json 只有94 bytes，疑似未正常執行 |
| 巴菲特十年收益率 | `scripts/buffett_yoc.py` | ✅ 完成（僅後端 CLI） | 護城河6項+YoC+IRR；前端尚未顯示 |
| 半導體新聞 | `index-pro.html` | ✅ 完成 | 獨立頁面 |

## 廢棄檔案（不可修改）

| 檔案 | 狀態 | 原因 |
|------|------|------|
| `index.html` | 🗄️ 廢棄 | 舊版，無 CANSLIM/籌碼 |
| `better.html` | 🗄️ 廢棄 | 舊版 |
| `index-card-n-list.html` | 🗄️ 廢棄 | 舊版 |
| `index-sentence.html` | 🗄️ 廢棄 | 舊版 |
| `index_time_period.html` | 🗄️ 廢棄 | 舊版 |

## 已知問題

| 問題 | 嚴重度 | 狀態 |
|------|--------|------|
| CANSLIM C欄有時顯示0 | 高 | 調查中（earningsGrowth資料不穩定） |
| 昇陽半導體籌碼外資顯示與實際相反 | 高 | 調查中（T86欄位對應問題） |
| vcp_daily.json 只有94 bytes | 中 | scan_vcp.py 未正常產出 |

## 設計決策紀錄

- **2026-04-19**：籌碼改為三層備援（localStorage → twse_daily.json → TWSE T86 直連），解決外資欄空白問題
- **2026-04-19**：個股快查改為3欄（買入潛力/Pivot/賣出壓力），明確區分VCP形態品質與突破時機
- **2026-04-19**：CANSLIM C欄改用 earningsGrowth ≠ 0 才採用，否則 fallback 到 revenueGrowth，避免0值誤判

## 自動化排程

- **vcp_scan.yml（VCP 掃描）**：週一到週五 08:00/10:45/13:15/18:15 台北時間更新 `vcp_daily.json` 與價格資料。**2026-11-17 起不再寄 email**（Brevo 寄送持續被收件端擋下）；`scripts/vcp_email_report.py` 與手動的 `vcp_email.yml` 保留但不排程。
- **fetch_daily.yml（資料抓取 + Cloudflare Pages 部署）**：盤中每 30 分鐘 + 收盤後 2 次 + 週一 full sync，共約 12 次/天，每次都會重新 deploy Cloudflare Pages。尚未調整，是「Cloudflare quota 用完」訊息的主要來源，待確認是否要一併降頻。
- **price-updater Worker**：盤中每 2 分鐘打 TWSE 即時報價 API（09:00–13:30，約 150 次/天），寫入 KV。尚未調整。

## 變更紀錄

| 日期 | 變更 | commit |
|------|------|--------|
| 2026-11-28 | 新增 scripts/buffett_yoc.py（巴菲特護城河篩選 + 十年持有收益率/IRR/安全邊際），scan_vcp.py 加選用 `--buffett` 旗標；yfinance 台股財報偶有缺欄，缺值顯示 n/a | 本次 |
| 2026-11-17 | 刪除「大盤情緒（自動判斷）」及連動程式（手動覆蓋按鈕、自動情緒計算、外資整體買超彙總）；保留 BTC 與監控模式；CANSLIM「M」改為只看加權指數今日是否上漲（taixChg） | 未提交 |
| 2026-11-17 | Fed 行事曆依 Fed 官網/BLS 校正日期（FOMC 6/17、9/16、10/28、12/9；CPI 11/10、12/10；原本差一天），補到 2027/4；顯示台灣時間（含美國夏令時間換算）；說明改為升息週期 | 未提交 |
| 2026-11-17 | 「本週大盤展望」開頁只顯示快取、不自動呼叫 AI；AI 錯誤不再快取，並丟棄舊版遺留的錯誤快取。全站開頁現為零 AI 呼叫 | 未提交 |
| 2026-11-17 | 「買賣紀律清單」改為可勾選（買進前/持有中/賣出前），標示巴菲特/蒙格/自己的規則；買進前全勾才顯示可下單；勾選狀態存 localStorage 隔日清空 | 未提交 |
| 2026-11-17 | 地緣/重大事件偵測改為「規則偵測」（24 小時內新聞＋急性/慢性關鍵字，不呼叫 AI）；開頁不再自動耗 AI 額度，AI 摘要改手動按鈕；抓不到新聞顯示灰色「無法判斷」不給綠燈；AI 錯誤訊息不再被快取 | 未提交 |
| 2026-11-17 | 「買股票的紀律」依 2026 年宏觀事件改寫：新增事件時間軸、跳空停損、崩盤日不抄底、外資單日賣超>500億不買、單日跌>4%兩天不開新倉、事件日紀律（數字為建議值可調） | 未提交 |
| 2026-11-17 | 移除 VCP 表格的「除息日」欄（已有獨立除息日曆，內容重複）；移除「除息精選」區塊（前端；exdiv_screener.py 與 exdiv_screener_results.json 後端仍保留） | 未提交 |
| 2026-11-17 | 除息日曆改為單一扁平表格（除息日/代號/名稱/現金股利/本次殖利率/發放日）；修正日期差一天的 bug（toISOString 在 UTC+8 會退回前一天）；股價載入後重畫以算殖利率 | 未提交 |
| 2026-11-17 | Fed Watch 行事曆改為表格並白話化（中文事件名、這是什麼、好/壞/需留意）；行事曆資料只排到 2026-12-11，需補資料 | 未提交 |
| 2026-11-17 | 借券改用 TWSE TWT93U（全市場約1200檔，單位換算為張），MI_SLBK 被封鎖不再是限制；原 CF Worker/MI_SLBK/BFT41U/OpenAPI 保留為備援 | 未提交 |
| 2026-11-17 | 掃描名單與價格資料納入所有 ETF（上市 00xxx、含 A/B 字母結尾，上櫃債券 ETF），產業標為「ETF」 | 未提交 |
| 2026-11-17 | 每次開啟頁面都自動掃描（有快取先顯示快取，掃描完成後覆蓋） | 未提交 |
| 2026-11-17 | 地緣事件偵測擴大為「重大市場事件」：新增輝達/黃仁勳/台積電/出口管制新聞源與判斷範圍 | 未提交 |
| 2026-11-18 | 修復「本週大盤展望／地緣事件」搜不到新聞：根因＝中文查詢在 Google News `hl=en-US` 下回 0 則、corsproxy.io 已 401。改為 fetchRSS 依中文自動用 zh-TW、地緣查詢加 `when:1d`、週展望加 `when:7d`；新增 `/api/rss` Pages Function 為首選抓取路徑，移除 corsproxy.io | 未提交 |
| 2026-11-17 | 近期除息日曆：不含當日、預設30天、依日期分組、可篩 ETF；移除「台灣 AI 半導體生態圖」區塊 | 未提交 |
| 2026-11-17 | vcp_scan.yml 移除 email 寄送步驟，只保留掃描與資料更新 | 5fc8635 |
| 2026-04-19 | CANSLIM完整7項、Pivot分析、籌碼三層備援 | af35f36 |
| 2026-04-19 | 手機4個掃描狀態同一行 | cae2721 |
| 2026-04-19 | 個股快查3欄Pivot | a5480d9 |
| 2026-04-19 | 技術信號固定2行 | 3432027 |
