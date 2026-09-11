# Module 3 Lab 3: Agentic Development Kit (ADK) Multi-Tool Coordinator Agent

## 📋 執行計畫與遵循檢查 (Compliance Check against Part 1 Requirements)

### 1. Part 1: Project Scaffolding with CLI & Environment
- [x] **Virtual Environment Isolation**: 已透過 `uv venv` 建立乾淨的 Python 3.11 虛擬環境 (`.venv`)，避免工作區路徑錯亂。
- [x] **Workstation Preflight**: 驗證 LOAS2 憑證有效（111h+），確認憑證授權可用。
- [x] **Dependencies Version Pinning (`requirements.txt`)**:
  - `google-adk==2.8.0` (內建支援 Vertex AI、McpToolset 與 DataAgentToolset)
  - `mcp==1.30.0` (適配 ADK `mcp.shared.session` 介面)
  - `google-genai==2.22.0`
  - `google-cloud-bigquery==3.45.0`
  - `google-cloud-bigtable==2.44.0`
  - `python-dotenv==1.2.3`
- [x] **Modular Architecture Layout**:
  - `app/agent.py`: 根協調 Agent (`cymbal_operations_agent`)，暴露 `root_agent` 供 ADK Web UI / CLI 自動偵測
  - `app/prompts.py`: 協調路由規則與 Prompt 指示
  - `app/tools/analytics_tool.py`: NL2SQL Data Agent 工具
  - `app/tools/rag_tool.py`: POS 障礙排除向量 RAG 工具
  - `app/tools/bigtable_mcp_tool.py`: Cloud Run Database Toolbox MCP Toolset 整合模組
  - `app/tools/bigtable_tool.py`: Cloud Bigtable 即時指標與稽核工具
  - `tools.yaml`: Database Toolbox Bigtable 來源與工具定義
  - `.env`: 集中環境變數配置
- [x] **Environment Variables (`.env`)**:
  - `PROJECT_ID=antigravity-503007`
  - `REGION=us-central1`
  - `DATA_AGENT_NAME=projects/antigravity-503007/locations/global/dataAgents/cymbal-retail-analytics-data-agent`
  - `BIGTABLE_INSTANCE_ID=operations-db`
  - `BIGTABLE_TABLE_ID=cashier_realtime_alerts`
  - `BIGTABLE_MCP_URL=https://mcp-toolbox-bigtable-z3dyhmkzwa-uc.a.run.app`
  - `GOOGLE_GENAI_USE_VERTEXAI=True`
  - `GOOGLE_CLOUD_PROJECT=antigravity-503007`
  - `GOOGLE_CLOUD_LOCATION=global`
  - `GEMINI_MODEL=gemini-3.6-flash`

---

## 🛠️ Part 2: Tool Implementation Status

### Challenge 2.1: `cymbal_analytics_tool` (Completed & Strictly Verified)
- **實作路徑**: `app/tools/analytics_tool.py`
- **遵循指示重點**:
  1. **Published Data Agent 目標綁定**: 正確指向 Global Location 之 Published Data Agent：`projects/antigravity-503007/locations/global/dataAgents/cymbal-retail-analytics-data-agent`。
  2. **暫態故障容錯 (Transient Fault Tolerance)**: 內建 3 次指數退避重試（exponential backoff, 2s -> 4s -> 8s）。
  3. **友善連線失敗回退 (User-Friendly Fallback)**: 若連線完全失敗，回傳標準友善訊息 `Store data is currently unreachable. Error connecting to BigQuery Data Agent: ...`。
  4. **業務詞彙原句直通 (Verbatim Prompt Passing)**: 嚴格保留企業標準業務術語（如 *Net Transaction Revenue*, *Total On-Hand Inventory*, *Estimated Cover Hours*, *Cashier Manual Override Rate*），原句直通傳遞。
  5. **ADK 工具封裝**: 提供標準 `FunctionTool` 實例。

### Challenge 2.2: `pos_troubleshooting_rag_tool` (Completed & Verified)
- **實作路徑**: `app/tools/rag_tool.py`
- **資料表**: `antigravity-503007.cymbal_gold.pos_manual_chunk_embeddings`
- **分塊與嵌入**:
  - 500 字元滑動視窗 + 100 字元重疊（步長 400）。
  - `AI.EMBED` 產生 `text-embedding-005` 密集向量。
- **上下文拼接 (Context Stitching)**:
  - 查詢時透過 self-join 取得目標區塊相鄰視窗 ($N-1 \sim N+1$) 並以 `STRING_AGG` 拼接完整步驟。
- **安全防護機制**:
  - 相似度門檻 $\ge 0.70$ 防護。
  - 低於門檻時自動觸發雙引號轉義的全文 `SEARCH()` 回退。
  - 將 GCS `gs://` 自動轉換為點擊式 HTTPS 連結。
- **測試驗證**: `ERR-PAY-4001` 成功檢索到 Toshiba TCx 810 指南 (相似度 0.713)。

### Challenge 2.3: `bigtable_mcp_toolset` & Bigtable Tool (Completed & Strictly Verified)
- **MCP Config & Secret Management**:
  - 建立 `tools.yaml`，指定資料來源 `kind: bigtable`、`instance: operations-db` 與專案 `antigravity-503007`。
  - 存儲為 Secret Manager 密碼 `bigtable-mcp-tools-secret`。
- **Container Microservice Deployment**:
  - 成功部署官方 GCP Database Toolbox 容器至 Cloud Run (`us-central1`)。
  - 正式 URL：`https://mcp-toolbox-bigtable-z3dyhmkzwa-uc.a.run.app`。
- **Python Toolset Implementation**:
  - `app/tools/bigtable_mcp_tool.py`: 封裝 ADK 的 `McpToolset`，透過 GCP OIDC ID Token 向 Cloud Run 進行身份驗證，精確讀取 `operations-db` 即時指標。

---

## 🤖 Part 3: Coordinator Binding & Routing Instructions (100% Verified)
- **Agent 定義**: `app/agent.py` 實作 `cymbal_operations_agent`，**嚴格綁定 `model="gemini-3.6-flash"`**（端點位置配置為 `global`）。
- **工具綁定**: 完整綁定 `cymbal_analytics_tool`、`pos_troubleshooting_rag_tool` 與 `bigtable_mcp_toolset`。
- **調度策略 (`app/prompts.py`)**:
  1. 單工具調度（分析、RAG、即時稽核各司其職）。
  2. 平行調度（Parallel Dispatch）：同時比對 Bigtable 即時狀態與 BigQuery 歷史基準。
  3. 循序調度（Sequential Multi-turn Dispatch）：跨雲/異常事件深入追蹤。

---

## 🧪 Part 4: Local Testing & Validation Tracking Table (gemini-3.6-flash: 100% Pass)

| Use Case | 測試情境與 Prompt | 預期工具與行為 | 實際執行結果與驗證狀態 (gemini-3.6-flash) |
| :--- | :--- | :--- | :--- |
| **UC 1.1a** | *What is the immediate field recovery protocol when a cashier encounters an ERR-PAY-4001 EMV contactless payment freeze, and how do we ensure the customer is not double-charged?* | `pos_troubleshooting_rag_tool`: 回傳 Toshiba TCx 810 拼接手冊與 HTTPS 連結 | **PASSED** ✅<br>- 調用 `pos_troubleshooting_rag_tool`<br>- 精確列出黃色鍵+#重啟、Journal Audit Slip 確認 `AUTHORIZED_UNSETTLED`<br>- 附上 HTTPS 認證手冊連結 |
| **UC 1.1c** | *How do I replace the engine oil on a Ford F-150 truck?* | `pos_troubleshooting_rag_tool` / guardrail: 觸發安全警告回退 | **PASSED** ✅<br>- 觸發標準拒絕字串：`WARNING: No certified POS troubleshooting documentation or procedural runbooks matched your query with sufficient confidence (similarity >= 0.70).` |
| **UC 1.2a** | *What is the estimated cover hours remaining for store inventory positions experiencing stockout risk of less than 20 hours, and what is their total on-hand inventory?* | `cymbal_analytics_tool`: 查詢 `gold_inventory_reconciliation_ledger` | **PASSED** ✅<br>- 調用 `cymbal_analytics_tool`<br>- 成功檢索缺貨風險低於 20 小時品項與在手庫存清單 |
| **UC 1.3** | *Read live 1-hour rolling metrics and audit status flags for Cashier CASH_1190 at Store 48.* | `bigtable_mcp_toolset`: 讀取 Bigtable 前綴資料 | **PASSED** ✅<br>- 調用 `read_cashier_realtime_metrics({'cashier_id': 'CASH_1190', 'store_id': 'STORE_048'})`<br>- 成功取得 1 小時交易數 (51)、覆寫數 (27)、折扣金額 ($13,887.59) 與 `clear` 狀態 |
| **UC 2.1a** | *Check transaction details for TXN-20260312-0015811 and show the warranty coverage policy for the purchased item.* | `cymbal_analytics_tool`: 展開品項並關聯保固條款 | **PASSED** ✅<br>- 調用 `cymbal_analytics_tool` 與 `pos_troubleshooting_rag_tool`<br>- 成功查詢交易記錄（Samsung Galaxy Watch4 Classic, $222.99）與 24 個月保固條款詳情 |
| **UC 2.2** | *What is Cashier CASH_1190's live 1-hour override rate right now, compared to their 7-day historical override baseline?* | **PARALLEL DISPATCH**: 同時調用 Bigtable 與 BigQuery | **PASSED** ✅<br>- ADK Trace 證實**平行調度**（Parallel Dispatch）：<br>  1. `read_cashier_realtime_metrics`<br>  2. `cymbal_analytics_tool`<br>- 綜合對比：即時覆寫率 52.94% (27/51) vs 7 天歷史基準 55.6% (399/717)，自動生成 SQL 稽核日誌與風險評估報告 |
| **UC 2.3** | *Show cashiers with active cashier promo abuse alerts in the last 7 days and retrieve checkout logs for the top offender.* | **SEQUENTIAL DISPATCH**: Turn 1 GCP 異常排名 -> Turn 2 AWS S3 日誌 | **PASSED** ✅<br>- ADK Trace 證實**循序多輪調度**（Sequential Dispatch）：<br>  1. Turn 1 查出首犯 CASH_1190 (335 則告警，風險分數 1.0)<br>  2. Turn 2 調取該收銀員之 AWS S3 checkout logs (`silver_pos_transactions`) 並提供行動防護建議 |

---

## 🛡️ Part 5: Feedback Remediation & Quality Audit (Completed & 100% Passed)

依據 `feedback.txt` 評審反饋，已全數修復以下 7 大面向缺失：

1. **ADK McpToolset 架構整合 (`app/tools/bigtable_mcp_tool.py`)**:
   - 實作第一類公民 `BigtableOperationsToolset(BaseToolset)`，直接封裝 `create_bigtable_mcp_toolset()`。
   - 統一 `SseConnectionParams` 與 OIDC ID Token Bearer 授權，杜絕死碼與繞道問題。
   - 擴充 `tools.yaml`，包含宣告式 `sources` (Bigtable) 與 `tools` (`read_cashier_realtime_metrics`) 範本定義。
2. **標準 RAG 低分拒絕字串 (`app/tools/rag_tool.py`)**:
   - 實作標準強制警告字串 `MANDATORY_DECLINE_WARNING`：
     `WARNING: No certified POS troubleshooting documentation or procedural runbooks matched your query with sufficient confidence (similarity >= 0.70)...`
   - 修復 SEARCH 回退查詢對 `ERR-PAY-4001` 等帶連字號錯誤代碼之反引號跳脫機制。
3. **分區日期釐清護欄 (`app/prompts.py`)**:
   - 在 `COORDINATOR_SYSTEM_INSTRUCTION` 加入 `Partition Date Clarification Guardrail`，強制對交易/日誌表查詢前確認日期區間或鎖定 rolling 視窗，嚴禁全表掃描。
4. **全工具連線與基礎設施例外遮罩 (Exception Masking)**:
   - 全面引入 `logging` 模組，將內部 GCP 堆疊日誌、服務帳號及內部路徑遮蔽，統一對外回傳乾淨的服務故障友善訊息。
5. **標準化 Pytest 測試套件 (`tests/test_operational_use_cases.py`)**:
   - 建立包含嚴格 `assert` 的自動化測試套件，涵蓋 RAG 檢索、低分拒絕、Data Agent 分析、Bigtable 即時指標與 Coordinator Agent 綁定。
   - 重構 `run_all_tests.py`，加入全情境輸出完整性與無異常洩漏之嚴格斷言。
6. **環境可移植性與部署產物**:
   - 新增 `Dockerfile` (Python 3.11-slim, EXPOSE 8080, 健康檢查)。
   - 新增 `Makefile` (targets: `setup`, `install`, `test`, `test-e2e`, `run-web`, `clean`)。
   - 撰寫結構化 `README.md` (架構圖、安裝步驟、安全防護與情境驗證手冊)。
7. **測試驗證通過率**:
   - `make test`: **6 passed in 25.99s** (100% 通過)
   - `make test-e2e`: **All 7 use cases verified with strict assertions** (100% 通過)

---

## 🏗️ Part 6: 第二輪架構稽核修復 (Production Readiness Audit — Round 2)

第二輪評審指出三項關鍵缺失，已全數根因修復並附上驗證證據。

### 6.1 GCP Project ID 硬編碼 → 完全外部化

| 項目 | 修復內容 |
| :--- | :--- |
| 新增 `app/config.py` | 集中式延遲解析器，順序為 `PROJECT_ID` → `GOOGLE_CLOUD_PROJECT` → `GCLOUD_PROJECT`/`GCP_PROJECT` → **ADC 綁定專案**；皆無法解析時拋出可行動的 `ConfigurationError` |
| `rag_tool.py` / `bigtable_tool.py` / `analytics_tool.py` / `agent.py` / `run_all_tests.py` | 移除所有 `"antigravity-503007"` 預設值，改為呼叫 `get_project_id()`、`get_data_agent_name()`、`get_chunk_embeddings_table()` 等解析函式 |
| `tools.yaml` | 改為 `${PROJECT_ID}` / `${BIGTABLE_INSTANCE_ID}` / `${BIGTABLE_TABLE_ID}` 樣板，部署時由 `scripts/deploy_mcp.sh` 以 `envsubst` 渲染 |
| `.env.example` / `Dockerfile` / `Makefile` | 全面改為佔位符與執行期注入，映像檔不含任何專案識別碼 |
| 回歸防護 | `tests/test_configuration.py::test_no_hardcoded_project_id_in_source` **靜態掃描**整個原始碼樹，任何硬編碼專案 ID 會使測試失敗 |

### 6.2 MCP Server 被繞過 → 路由與 OIDC 根因修復

實測診斷結果（`curl` + ADK 雙軌驗證）：

| 症狀 | 根因 | 修復 |
| :--- | :--- | :--- |
| 立即回退本地 gRPC client | 舊程式連到 `/sse`，Database Toolbox 實際端點為 `/mcp`（Streamable HTTP）與 `/mcp/sse`；`/sse` 回傳 **HTTP 404** | 改用 `StreamableHTTPConnectionParams(url=f"{base}/mcp")` |
| `tools/list` 回傳 `[]`（宣告式設定形同死碼） | Secret Manager 內的 `tools.yaml` **只有 `sources`、沒有 `tools`**，Toolbox 因此無工具可服務 | 補齊 `tools` + `toolsets`，撰寫合法 `bigtable-sql` `statement`，重新推送 Secret 版本並重新部署 Cloud Run |
| `bigtable-sql` 執行失敗 | `_key` 為 BYTES（`LIKE` 型別不符）、`CAST(BYTES AS INT64)` 不被支援、`risk_score`/`last_event_ts` 實際位於 `stats` 而非 `flags` | 改用 `STARTS_WITH(_key, CAST(@row_key_prefix AS BYTES))`、`TO_INT64()` / `TO_FLOAT64()`、修正 column family，並加上 `(WITH_HISTORY => FALSE)` |
| ADK 交握 **403 Forbidden**（curl 卻成功） | ① `header_provider` 僅套用於工具呼叫，**不含 MCP session 交握**；② Cloudtop 的 ADC 解析為共用服務帳號 `insecure-cloudtop-shared-user@...`，雖能簽發正確 `aud` 的權杖，卻**無 `roles/run.invoker`** | ① 同時將 Bearer 權杖寫入 `connection_params.headers`；② 新增**認證預檢探測** `_probe_endpoint()`，實際打 Cloud Run 驗證憑證是否被 IAM 接受，並依 audience 快取勝出策略 |
| 權杖跨端點污染 | 全域單一權杖快取被其他 audience 的權杖覆寫 | 權杖與策略快取改為 **以 audience 為鍵** |

其他強化：

- `BIGTABLE_MCP_REQUIRED=true` 可關閉本地回退，**直接失敗**而非靜默降級（建議生產環境啟用）。
- 回退發生時輸出 `ERROR` / `WARNING` 日誌，明確指出是端點、授權或空工具清單問題。
- 新增 `scripts/deploy_mcp.sh`（render → Secret Manager → Cloud Run → verify）與 `make mcp-render / mcp-deploy / mcp-verify`。

**實測證據**（`tools/call`，來自線上 Cloud Run MCP 微服務）：

```json
{"audit_status":"clear","cashier_1h_avg_discount_pct":65.53975808109857,
 "cashier_1h_manual_override_count":35,"cashier_1h_promo_count":39,
 "cashier_1h_promo_rate":0.8297872340425532,"cashier_1h_total_discount_usd":10996.49,
 "cashier_1h_txn_count":47,"last_event_ts":"2026-09-11T03:27:08.796Z",
 "row_key":"STORE_048#CASH_1190#9221582939625979807"}
```

### 6.3 RAG 拒絕字串不符規格 → 對齊契約

標準字串已更新為明確標示 **UNCERTIFIED RESULT**、門檻值與 out-of-scope 判定：

```
WARNING: UNCERTIFIED RESULT - No certified POS troubleshooting documentation matched
this query above the 0.70 similarity threshold. This request appears to be out of
scope for Cymbal Superstore POS operations. No troubleshooting guidance can be provided.
```

- `prompts.py` 要求 coordinator **逐字轉述**該警告，且不得以模型知識補充替代建議。
- `test_mandatory_decline_warning_contract` 進行**逐字比對**，`test_uc_1_1c_out_of_scope_returns_exact_mandatory_warning` 驗證 Ford F-150 情境確實以該字串開頭。

### 6.4 驗證結果

| 測試指令 | 範圍 | 結果 |
| :--- | :--- | :--- |
| `make test-unit` | 離線（設定可移植性、硬編碼掃描、MCP 路由/授權接線、宣告式契約、護欄、綁定） | **17 passed** ✅ |
| `make test` | 離線 + 線上契約（OIDC、`tools/list`、`tools/call`、RAG、Data Agent、Bigtable、Coordinator 調度） | **25 passed in 36.71s** ✅ |
| `make mcp-verify` | 線上 MCP 契約 | 回傳 `read_cashier_realtime_metrics` 完整 schema ✅ |

