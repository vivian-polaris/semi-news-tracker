# Vivian Edge · VCP Finder — 計畫目標

**目標版本**：v2.1
**最後更新**：2026-04-19

## 當前目標

### 目標 1：資料準確性修復
**優先級**：P0 | **狀態**：進行中

- [ ] 修復 CANSLIM C欄顯示0的問題（earningsGrowth資料來源問題）
- [ ] 修復籌碼外資買賣方向錯誤問題（T86欄位對應驗證）
- [ ] 修復 vcp_daily.json 只有94 bytes（scan_vcp.py 未正常產出）

**驗收條件**：查詢昇陽半導體，籌碼外資與 TWSE 官網一致；C欄有實際百分比數字

### 目標 2：UI 細節完善
**優先級**：P1 | **狀態**：進行中

- [ ] 交易所/產業欄置中
- [ ] 其他欄位置中（MA排列、技術信號除外）

## Handover（待接手任務）

### 修復 CANSLIM C 欄顯示 0
**優先級**：P0 | **狀態**：待執行
**問題描述**：CANSLIM C欄（Current EPS成長 ≥25%）在多數股票顯示 0% 或 null
**根本原因**：`earningsGrowth` 來自 Yahoo Finance quoteSummary，台股資料不穩定
**技術設計**：
- 當前邏輯在 `calcCANSLIM()` → `_ceg = fund?.earningsGrowth != null && fund.earningsGrowth !== 0`
- 問題：Yahoo earningsGrowth 可能回傳 0（非缺失），與真實成長0混淆
- 建議：改用 twse_daily.json 的 income.eps 計算 YoY（需前一年同期資料）
**執行步驟**：
1. 在 `scripts/fetch_twse_daily.py` 中加入前年同期 EPS 抓取
2. 在 twse_daily.json 新增 `earningsYoY` 欄位
3. 更新 `calcCANSLIM()` 優先使用 `earningsYoY`
**驗收條件**：CANSLIM C欄對至少80%有財報的股票顯示非零數值

### 修復籌碼外資方向錯誤
**優先級**：P0 | **狀態**：待執行
**問題描述**：昇陽半導體外資實際賣超，但系統顯示買超
**根本原因**：T86 欄位索引對應可能有誤
**執行步驟**：
1. 在 `fetch_twse_daily.py` 中印出 T86 fields 陣列，確認 fIdx/tIdx/dIdx
2. 與 TWSE 官網當日資料交叉驗證
3. 修正欄位索引
**驗收條件**：抽查5支股票，籌碼方向與 TWSE 官網一致

## 歸檔目標

### [完成] 2026-04-19：CANSLIM 7項評分
完整實現 C/A/N/S/L/I/M 七項指標，加入篩選器和排序。

### [完成] 2026-04-19：籌碼三層備援
A快取 → B twse_daily.json → C T86直連，解決籌碼空白問題。

### [完成] 2026-04-19：個股快查 Pivot 分析
3欄顯示（買入潛力/Pivot位置/賣出壓力），明確說明 VCP 與量價的關係。

## 版本歷史

| 版本 | 日期 | 主要變更 |
|------|------|---------|
| v2.0 | 2026-04-19 | CANSLIM + Pivot + 籌碼三層備援 |
| v1.0 | 2026-04-18 | 基本 VCP 掃描 + 基本面 + Groq AI 新聞 |
