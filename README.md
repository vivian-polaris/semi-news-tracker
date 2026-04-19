# Vivian Edge · VCP Finder

> 台股動能精選 — VCP 技術形態掃描 + CANSLIM 評分 + AI 市場新聞

🔗 **線上版本**：https://vivian-vcpfinder.pages.dev/VCPfinder

## 這是什麼

每天自動掃描全台灣 ~1700 支股票，找出符合 **VCP（Volatility Contraction Pattern）** 形態的候選股，結合 **CAN SLIM**（William O'Neil）評分和 **AI 市場新聞摘要**，幫助投資人快速識別潛力個股。

## 功能

- **VCP 掃描**：Stage 2 + ATR收縮 + 量縮 + 距高點，評分 0–100
- **CAN SLIM**：7項指標（C/A/N/S/L/I/M），篩選高品質成長股
- **個股快查**：輸入股票代號，即時顯示買入潛力 / Pivot位置 / 賣出壓力
- **AI 市場震撼彈**：Top 10 財經新聞（含戰爭、川普、霍爾木茲海峽）
- **美林投資時鐘**：當前經濟週期分析

## 架構

```
VCPfinder.html          主應用（單一 HTML，~2800行）
index-pro.html          半導體新聞追蹤
scripts/
  fetch_twse_daily.py   每日抓取籌碼/財報資料
  scan_vcp.py           VCP 預掃描
functions/api/groq.js   Groq API 代理（隱藏 API Key）
.github/workflows/
  deploy.yml            push → 自動部署 Cloudflare
  fetch_daily.yml       每天 16:45 自動更新資料
twse_daily.json         籌碼/財報/產業（每日更新）
stocks_tse.json         上市股票歷史價格
stocks_otc.json         上櫃股票歷史價格
```

## 開發

```bash
# 修改後直接 push，GitHub Actions 自動部署
git add VCPfinder.html
git commit -m "描述"
git push
```

**開發者請先閱讀 `AI_CONTEXT.md`**

## 核心文件

| 文件 | 說明 |
|------|------|
| `AI_CONTEXT.md` | AI 開發上下文（所有模型必讀）|
| `CLAUDE.md` | Claude 專屬約束 |
| `SPEC.md` | 技術規格 |
| `STATUS.md` | 現況與已知問題 |
| `ROADMAP.md` | 計畫目標與待辦 |
