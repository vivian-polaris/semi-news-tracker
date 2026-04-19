# Vivian Edge · VCP Finder — AI 開發上下文

> **本文件適用於所有參與開發的 AI 模型（Claude、Gemini、GPT、Codex 等）。**
> 在進行任何開發動作前，必須先閱讀本文件，再按指定順序閱讀核心文件。

## 語言

- 使用**繁體中文**對話和程式碼註解

## ⚠️ 最重要：正確工作檔案

**唯一正確的工作檔案：`VCPfinder.html`（在 repo 根目錄）**

```
修改 VCPfinder.html → git commit → git push → GitHub Actions 自動部署到 Cloudflare
```

❌ 絕對不要修改以下檔案（舊版本，已廢棄）：
- `index.html`
- `better.html`
- `index-card-n-list.html`
- `index-sentence.html`
- `index_time_period.html`

## 強制閱讀順序

每個開發 session 開始時，按以下順序閱讀：

| 步驟 | 文件 | 你要從中獲得什麼 |
|------|------|-----------------|
| 1 | `SPEC.md` | 系統架構、模組契約、資料流、安全約束 |
| 2 | `STATUS.md` | 哪些功能已完成、已知問題、最新變更 |
| 3 | `ROADMAP.md` → **特別是 Handover 區** | 當前要做的具體任務、驗收條件 |

## 核心文件體系

| 文件 | 角色 | 更新時機 |
|------|------|--------|
| `SPEC.md` | 技術權威定義 | 修改架構/資料結構/模組 |
| `STATUS.md` | 現況追蹤 | 每次代碼變更 |
| `ROADMAP.md` | 目標+交接 | 目標或任務調整 |
| `README.md` | 對外介紹 | 使用方法變更 |
| `AI_CONTEXT.md` | AI 開發上下文 | 開發規則調整 |
| `CLAUDE.md` | Claude 專屬約束 | Claude 行為調整 |

## 核心開發規則

1. **SPEC 優先**：SPEC.md 是權威定義，程式碼必須遵守 SPEC
2. **文件同步**：修改架構 → 更新 SPEC；完成功能 → 更新 STATUS；目標調整 → 更新 ROADMAP
3. **commit + push 是必須的**：每次修改後必須 `git commit` 並 `git push`，不可只改本機

## 文件找不到答案時

1. **不要猜測**
2. **告訴使用者**：「SPEC/ROADMAP 中沒有關於 XXX 的定義，我需要確認」
3. **等待使用者回覆**後，先更新對應的核心文件
4. **然後才動手**開發
