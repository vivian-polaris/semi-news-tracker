# VCPfinder 邏輯審計報告
審計日期：2026-05-23  
審計範圍：calcFundScore、calcCANSLIM、quickFund、incomeHistory、坡段判斷

---

## 問題1：建設股 revYoY 極大值混入選股池
**判斷：YES（確實有問題）**

**根本原因：** 建設股採完工認列，月營收 YoY 可達 +995635%（例如 9906 欣巴巴）。`quickFund()` 直接用 `mr.yoy/100` 未做上限 cap，導致這些股票：
- `calcFundScore`：revenueGrowth ≥ 10% 滿分，score=100 ✓（分數不受影響，只要 opMargin 也達標）
- `calcCANSLIM C`：`cGrowth=9956 >= 0.25 → TRUE`，建設股必過 C 條件
- 建設股是景氣循環股，不是 O'Neil CANSLIM 所定義的成長股

**建議修法（quickFund 加 cap）：**
```javascript
// 舊：
revenueGrowth: mr?.yoy != null ? (mr.yoy/100) : null,
// 新：cap at 500%（5.0），排除完工認列異常值，不影響真正高成長股）
revenueGrowth: mr?.yoy != null ? Math.min(mr.yoy/100, 5.0) : null,
```
同一邏輯套用到 `fetchFundamentals()` 中相同位置。

**狀態：待修補**

---

## 問題2：calcFundScore 分數二極化
**判斷：PARTIAL（是設計特性，但應讓使用者知道）**

**根本原因：** 目前只有兩個有效欄位（opMargin + revenueGrowth），calcFundScore 的正規化使分數只有兩種結果：
- opMargin ≥ 15% AND revYoY ≥ 10% → **100分**
- 其他組合 → **73-77分**（無法達到 fundMin=80）

實際上 fundMin=80 等同於「兩個指標都必須滿分」，這是合理的強基本面篩選。

**不需要修改**：earningsGrowth 冷啟動後一年，取得 YoY EPS 資料，第三個欄位（20pts）加入後分佈會更連續。現在維持此設計即可。

---

## 問題3：CANSLIM A 以 operatingMargin 替代 ROE
**判斷：PARTIAL（近似合理，但門檻可調整）**

**根本原因：** TWSE API 無法取得 ROE，改用 `operatingMargin >= 12%` 替代。兩者概念接近（持續獲利能力），但：
- ROE >= 15% = 股東權益報酬率，含槓桿效果
- operatingMargin >= 12% = 純粹的本業獲利率

高資本密集公司（如 TSMC）的 operatingMargin 遠高於 12%，替代合理。

**建議**：維持現況（operatingMargin >= 12% 作為 A 條件），但在 UI tooltip 說明「以營業利益率代替 ROE」。不需改程式邏輯。

---

## 問題4：incomeHistory 季別 key 格式
**判斷：NO（不是問題）**

**驗證結果：** TWSE API `t187ap14_L` 回傳的 `季別` 欄位固定為 `'1'`（單一字元，非 `'01'`），1070 支股票全部一致。incomeHistory key 格式 `'2026Q1'` 不會有不匹配問題。

---

## 問題5：其他發現

### 5a. fetchFundamentals 和 quickFund 的 revYoY 處理不一致
**判斷：YES**

`quickFund()` 和 `fetchFundamentals()` 都有 `revenueGrowth: mr?.yoy/100` 但只有修一個地方，另一個也要同步修。

### 5b. 流動性門檻邏輯正確
```javascript
// 股價≥50：要求均量>100萬股/日（100萬×500元=5億元/日 量能充足）
// 股價<50 ：要求均量>300萬股/日（300萬×20元 =6000萬/日 合理門檻）
```
以股數計算流動性，高價股反而要求更高的金額流動性，設計合理，不需修改。

### 5c. calcCANSLIM M 條件依賴 localStorage
```javascript
const mktUp = (localStorage.getItem('mktSentiment')||'neutral')==='risk_on' || (window.taixChg!=null&&window.taixChg>0);
```
若使用者未手動設定 mktSentiment，M 條件完全依賴當天加權指數漲跌（taixChg），加權跌的那天所有股票 M=FALSE，CANSLIM 自動少1項。這是正確設計（市場環境判斷），不是 bug。

### 5d. 坡段邏輯
讀 VCPfinder.html 坡段部分，MA20斜率+distMA20+RSI 的判斷邏輯結構正確，門檻值（slope<0、RSI>75、distMA20>20 等）符合坡段中段/尾巴/開始的技術定義。無發現邏輯錯誤。

---

## 需要立刻修補的項目

| 優先 | 問題 | 修法 | 影響 |
|------|------|------|------|
| P0 | 建設股 revYoY 無 cap | quickFund + fetchFundamentals 加 `Math.min(..., 5.0)` | 建設股不再混入 CANSLIM C=TRUE |

## 不需修補（確認為設計正確）
- 問題2：分數二極化（等 earningsGrowth 冷啟動結束自然改善）
- 問題3：A 條件替代（合理近似）
- 問題4：季別格式（API 固定輸出，無風險）
- 問題5b：流動性門檻（邏輯正確）
- 問題5c：M 條件（設計如此）
- 問題5d：坡段邏輯（正確）
