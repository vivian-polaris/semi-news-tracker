# Vivian Edge · VCP Finder — 現況文件

> 此文件是專案的單一真相來源。每次代碼變更後必須同步更新。

**最後更新**：2026-04-19
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

## 變更紀錄

| 日期 | 變更 | commit |
|------|------|--------|
| 2026-04-19 | CANSLIM完整7項、Pivot分析、籌碼三層備援 | af35f36 |
| 2026-04-19 | 手機4個掃描狀態同一行 | cae2721 |
| 2026-04-19 | 個股快查3欄Pivot | a5480d9 |
| 2026-04-19 | 技術信號固定2行 | 3432027 |
