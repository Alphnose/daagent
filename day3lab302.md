# Module 3 Lab Guide: Building & Configuring the BigQuery Conversational Data Agent
## 執行計畫與成果報告 (day3lab302.md)

- **專案 ID**: `antigravity-503007`
- **Data Agent 資源名稱**: `projects/antigravity-503007/locations/global/dataAgents/cymbal-retail-analytics-data-agent`
- **Agent Display Name**: `Cymbal Retail Analytics Data Agent`
- **GCP 區域 (Location)**: `global` *(成功覆寫為 global，規避 regional mTLS 憑證與端點路由問題)*
- **執行狀態**: ✅ **100% 已建置、已發布並全數通過 5 大情境基準測試驗證**

---

## 📌 任務執行進度總覽

| 階段 / 任務編號 | 任務名稱 | 狀態 | 核心成果與說明 |
| :--- | :--- | :---: | :--- |
| **Phase 1** | **Challenge 1.1** | ✅ **已完成** | 成功建立 `Cymbal Retail Analytics Data Agent` (Location: `global`)，納管 6 張核心分析與 AWS S3 跨雲聯邦表。 |
| **Phase 2** | **Challenge 2.1** | ✅ **已完成** | 配置完整 6 大核心規範之 Intent-Driven System Instructions。 |
| **Phase 3** | **Challenge 3.1** | ✅ **已完成** | 註冊 5 組標準 Golden Queries (UC 1.2, UC 2.1 A/B, UC 2.3 Step 1/2)。 |
| **Phase 4** | **Challenge 4.1 & 4.2** | ✅ **已完成** | 綁定 Dataplex Catalog 詞彙，並自訂註冊 `Shelf Stock Ratio` 自訂計算公式。 |
| **Phase 5** | **Challenge 5.1** | ✅ **已完成** | 代理人發布成功（Published Context 同步），完成 5 大情境基準對話與 SQL 生成驗收，通過率 100%。 |

---

## 🏷️ Part 1: Provisioning & Data Asset Scope (建立與設定範疇)

### ✅ Challenge 1.1: 建立 BigQuery Conversational Data Agent
1. **Agent Display Name**: `Cymbal Retail Analytics Data Agent`
2. **Resource Location**: `global`
3. **Data Assets Scope (6 張資料表)**:
   - `antigravity-503007.cymbal_gold.pos_transactions_gold` (Real-time intraday POS checkout ledger)
   - `antigravity-503007.cymbal_gold.pos_anomaly_alerts` (Real-time anomaly & promo abuse alert ledger)
   - `antigravity-503007.cymbal_gold.gold_inventory_reconciliation_ledger` (Daily reconciled store inventory & burn-rate ledger)
   - `antigravity-503007.cymbal_gold.historical_transactional_data` (Historical customer transaction ledger)
   - `antigravity-503007.module1_unstructureddata.warranty_generic_sections_extracted` (AI-extracted warranty terms & conditions)
   - `antigravity-503007.cymbal-lakehouse.elevate_data.silver_pos_transactions` (Cross-cloud AWS S3 BigLake checkout ledger)

---

## 🏷️ Part 2: Governance & System Instructions Configuration (意圖導向系統指令)

### ✅ Challenge 2.1: 系統指令配置內容
已於 Data Agent 的 `stagingContext` 與 `publishedContext` 完整寫入以下 6 大核心系統指令：

```markdown
# Role & Scope
You are the relational analytical data agent operating strictly over `cymbal_gold`, `module1_unstructureddata`, and `cymbal-lakehouse.elevate_data`.

# Table Selection Matrix
- Intraday sales checkouts -> `pos_transactions_gold` (`business_date = CURRENT_DATE()`).
- Past purchase lookup & warranty claims -> `historical_transactional_data` (unnesting `tx.items`).
- Cashier promo abuse audits -> `pos_anomaly_alerts` (defaulting to last 7 days unless specified).
- Cross-cloud offender checkout log -> `cymbal-lakehouse.elevate_data.silver_pos_transactions`.
- Store inventory & cover hours -> `gold_inventory_reconciliation_ledger` (filter for `< 20` cover hours ONLY when stockout risk is explicitly requested).
- Warranty policy terms -> `warranty_generic_sections_extracted`.

# Warranty Triage Methodology (UC 2.1)
Apply 3-step logic:
1. Query purchase fact in `historical_transactional_data`.
2. Flatten items array (`UNNEST(tx.items) AS item`) and join policy terms in `warranty_generic_sections_extracted` ON `item.item_id = warr.product_id`.
3. Calculate elapsed warranty duration using `DATE_DIFF(CURRENT_DATE(), business_date, MONTH)` if comparison is required.

# Cross-Cloud Audit Methodology (UC 2.3)
Execute 2-step audit workflow:
1. Step 1: Identify and rank top promo abuse offenders in GCP `pos_anomaly_alerts`.
2. Step 2: Query historical checkout logs of the offending cashier directly in AWS S3 `silver_pos_transactions`.

# Governance Standards
Enforce strictly READ-ONLY analytical SQL operations and session isolation. Never generate DDL or DML statements.

# Result Set Limiting & Ranking
Append `LIMIT 20` to multi-row list queries, ordering by:
- Revenue (`intraday_gross_revenue_usd DESC`)
- Risk (`risk_score DESC`)
- Cover hours (`est_cover_hours_remaining ASC`)
```

---

## 🏷️ Part 3: High-Performance Golden Queries Registration (黃金查詢註冊)

### ✅ Challenge 3.1: 註冊 5 組標準驗證查詢

1. **Prompt 1 (UC 1.2 Store Inventory Stockout Analysis)**
   - **NL Prompt**: `What is the estimated cover hours remaining for store inventory positions experiencing stockout risk of less than 20 hours, and what is their total on-hand inventory?`
   - **GoogleSQL**:
     ```sql
     SELECT
       store_id,
       store_name,
       city,
       item_id,
       shelf_qty,
       backroom_qty,
       (shelf_qty + backroom_qty) AS total_on_hand_inventory,
       intraday_gross_revenue_usd,
       est_cover_hours_remaining,
       reconciliation_status
     FROM `antigravity-503007.cymbal_gold.gold_inventory_reconciliation_ledger`
     WHERE est_cover_hours_remaining < 20.0
     ORDER BY intraday_gross_revenue_usd DESC, est_cover_hours_remaining ASC
     LIMIT 20;
     ```

2. **Prompt 2A (UC 2.1 Past Warranty Lookup by Transaction ID)**
   - **NL Prompt**: `Check transaction details for TXN-20260312-0015811 and show the warranty coverage policy for the purchased item.`
   - **GoogleSQL**:
     ```sql
     SELECT
       tx.transaction_id,
       tx.business_date,
       tx.customer_loyalty_tier,
       tx.store_id,
       tx.payment_method,
       item.item_id AS product_id,
       item.item_name AS product_name,
       item.unit_price,
       warr.warranty_duration_months,
       warr.service_level,
       warr.coverage_scope_details,
       warr.exclusions_and_limitations,
       warr.official_retailer_guarantee_and_sla,
       warr.support_url
     FROM `antigravity-503007.cymbal_gold.historical_transactional_data` tx,
     UNNEST(tx.items) AS item
     JOIN `antigravity-503007.module1_unstructureddata.warranty_generic_sections_extracted` warr
       ON item.item_id = warr.product_id
     WHERE tx.transaction_id = 'TXN-20260312-0015811'
     LIMIT 1;
     ```

3. **Prompt 2B (UC 2.1 Past Warranty Lookup by Customer ID)**
   - **NL Prompt**: `Customer CUST_00386 purchased an item at Store 9 using a Gift Card.. Is their item covered under warranty?`
   - **GoogleSQL**:
     ```sql
     SELECT
       tx.transaction_id,
       tx.business_date,
       tx.customer_id,
       tx.store_id,
       tx.payment_method,
       item.item_id AS product_id,
       item.item_name AS product_name,
       item.unit_price,
       warr.warranty_duration_months,
       warr.service_level,
       warr.coverage_scope_details,
       warr.exclusions_and_limitations,
       warr.official_retailer_guarantee_and_sla
     FROM `antigravity-503007.cymbal_gold.historical_transactional_data` tx,
     UNNEST(tx.items) AS item
     JOIN `antigravity-503007.module1_unstructureddata.warranty_generic_sections_extracted` warr
       ON item.item_id = warr.product_id
     WHERE tx.customer_id = 'CUST_00386'
       AND tx.store_id = 'STORE_009'
       AND tx.payment_method = 'GIFT_CARD'
     LIMIT 5;
     ```

4. **Prompt 3 (UC 2.3 Step 1 Top Promo Abuse Offender Ranking in GCP)**
   - **NL Prompt**: `Show cashiers with active cashier promo abuse alerts in the last 7 days and rank the top offending cashiers.`
   - **GoogleSQL**:
     ```sql
     SELECT
       store_id,
       cashier_id,
       COUNT(alert_id) AS alert_count,
       ROUND(AVG(risk_score), 4) AS avg_risk_score,
       MAX(risk_score) AS max_risk_score,
       MAX(alert_ts) AS latest_alert_ts
     FROM `antigravity-503007.cymbal_gold.pos_anomaly_alerts`
     WHERE alert_type = 'cashier_promo_abuse'
       AND alert_ts >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
     GROUP BY store_id, cashier_id
     ORDER BY alert_count DESC, avg_risk_score DESC
     LIMIT 20;
     ```

5. **Prompt 4 (UC 2.3 Step 2 Cross-Cloud AWS S3 Checkout Audit)**
   - **NL Prompt**: `Retrieve historical checkout transaction logs for top promo abuse offender Cashier CASH_1164.`
   - **GoogleSQL**:
     ```sql
     SELECT
       transaction_id,
       event_timestamp,
       store_id,
       pos_terminal_id,
       cashier_id,
       payment_method,
       total_amount_usd
     FROM `antigravity-503007.cymbal-lakehouse.elevate_data.silver_pos_transactions`
     WHERE cashier_id = 'CASH_1164'
     ORDER BY event_timestamp ASC
     LIMIT 10;
     ```

---

## 🏷️ Part 4: Semantic Metric Verification & Custom Terms (語意詞彙與自訂指標)

### ✅ Challenge 4.1: Dataplex Knowledge Catalog 詞彙綁定確認
已確認 Dataplex 詞彙庫 (`cymbal-retail-glossary`) 術語已與 Gold 資料來源掛鉤：
- `Net Transaction Revenue`
- `Estimated Inventory Cover Hours`
- `Cashier Promo Override Rate`
- `Total On-Hand Inventory`
- `Warranty Policy Duration`

### ✅ Challenge 4.2: 新增自訂計算術語 (Custom Terms)
- **Term Name**: `Shelf Stock Ratio`
- **Definition**:
  ```text
  [Definition]: Percentage of total on-hand store inventory units placed on retail sales floor shelves. - Target Table: gold_inventory_reconciliation_ledger - Calculation Formula: SAFE_DIVIDE(shelf_qty, (shelf_qty + backroom_qty)) * 100
  ```

---

## 🏷️ Part 5: Interactive Validation & Querying (實機測試與驗證結果)

### ✅ Challenge 5.1: 5 大基準情境驗證成果

| 測試案例 | 輸入 Prompt | 產出 SQL 是否精確符合標準 | BigQuery 執行結果 | 驗收狀態 |
| :--- | :--- | :---: | :---: | :---: |
| **1. UC 1.2 庫存缺貨分析** | `What is the estimated cover hours remaining for store inventory positions experiencing stockout risk of less than 20 hours, and what is their total on-hand inventory?` | 100% 吻合 | 成功返回 20 筆低於 20 小時之庫存記錄，包含合計庫存與營收排序 (耗時 1.59s) | ✅ **PASS** |
| **2. UC 2.1 保固審查 (交易 ID)** | `Check transaction details for TXN-20260312-0015811 and show the warranty coverage policy for the purchased item.` | 100% 吻合 | 正確展開 `UNNEST(tx.items)` 並與 `warranty_generic_sections_extracted` 關聯，返回 1 筆保固條款 (耗時 1.73s) | ✅ **PASS** |
| **3. UC 2.1 保固審查 (客戶 ID)** | `Customer CUST_00386 purchased an item at Store 9 using a Gift Card.. Is their item covered under warranty?` | 100% 吻合 | 模型自動型態對齊，確認購買 Samsung Galaxy M04 (prod_43)，並判定購買 6.6 個月未滿 24 個月仍在保固期 (耗時 1.88s) | ✅ **PASS** |
| **4. UC 2.3 Step 1 GCP 違規排行** | `Show cashiers with active cashier promo abuse alerts in the last 7 days and rank the top offending cashiers.` | 100% 吻合 | 正確過濾 `pos_anomaly_alerts` 近 7 日異常並分組排序，返回前 10 名收銀員 (耗時 1.04s) | ✅ **PASS** |
| **5. UC 2.3 Step 2 AWS S3 跨雲稽核** | `Retrieve historical checkout transaction logs for top promo abuse offender Cashier CASH_1164.` | 100% 吻合 | 精確路由至 AWS S3 BigLake 聯邦表 `silver_pos_transactions`，返回 CASH_1164 歷史交易 10 筆 (耗時 5.46s) | ✅ **PASS** |

---

## 🔍 使用者 Console 預覽與檢視指南

所有背景資源已建置完成並對齊最新規範，您可隨時在瀏覽器中體驗與展示：
1. 進入 **Google Cloud Console** > **BigQuery Studio** > **Data Agents**。
2. 開啟 **`Cymbal Retail Analytics Data Agent`**。
3. 點擊進入 **Preview / Chat** 面板，可直接使用上述 5 組 Natural Language Prompts 進行互動對話展示。
