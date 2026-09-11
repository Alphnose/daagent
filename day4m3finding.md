# Day 4 / Module 3 — 稽核缺失分析與修正報告

- **專案**：Cymbal Superstore Operations Agent (`elevate-da-adv-day3-labs-agent`)
- **修正 Commit**：`d7b0bd4`（`daagent/main`，前一輪為 `e904818`）
- **日期**：2026-09-11
- **模型**：`gemini-3.6-flash`（Vertex AI，`GOOGLE_CLOUD_LOCATION=global`）

---

## 摘要

第二輪評測報告肯定了專案的結構整潔度、容器建置設定（Dockerfile / Makefile）與 pytest 測試覆蓋，
但指出三項缺失。本文件記錄每一項的**根因**、**修法**與**驗證證據**。

| # | 稽核缺失 | 嚴重度 | 狀態 |
|---|---|---|---|
| 1 | GCP Project ID 硬編碼於多個核心執行腳本與設定中 | Critical | 已修正 |
| 2 | MCP server 實際上被繞過，直接降級為 gRPC 直連掃描 | Critical | 已修正 |
| 3 | 向量檢索工具的 out-of-scope 警告字串與設計規格不符 | Minor | 已修正 |

三項缺失中，**第 2 項實際上是 4 個獨立缺陷疊加**，是本輪工作量最大、也最不直覺的部分。

---

## Finding 1 — Project ID 硬編碼（環境隔離違規）

### 稽核原文

> the GCP Project ID is hardcoded across multiple core execution scripts and configurations,
> violating the environment isolation principle and causing immediate test failures on foreign systems.

### 根因

問題不在「沒有讀環境變數」，而在於**把專案 ID 當成環境變數的預設值**：

```python
PROJECT_ID = os.getenv("PROJECT_ID", "antigravity-503007")  # ← 反模式
```

這個寫法在別台機器上**不會報錯**，而是靜默指向他人的專案，導致：

- 在外部環境跑測試時，權限錯誤 / 找不到資源，但錯誤訊息完全不指向真正原因。
- CI 與本機行為不一致，且違反環境隔離原則。

受影響檔案：`app/tools/rag_tool.py`、`app/tools/bigtable_tool.py`、
`app/tools/analytics_tool.py`（`DATA_AGENT_NAME`）、`app/agent.py`、`run_all_tests.py`、
`tools.yaml`、`Dockerfile`、`Makefile`。

### 修法

**1. 新增集中式設定解析層 `app/config.py`**

以 `lru_cache` 做惰性解析，明確的優先順序，**沒有預設值**：

```
PROJECT_ID → GOOGLE_CLOUD_PROJECT → GCLOUD_PROJECT / GCP_PROJECT
           → google.auth.default() 的 ADC quota project
           → 全部失敗則 raise ConfigurationError（附可操作的修正指引）
```

提供的解析器：

| 函式 | 說明 |
|---|---|
| `get_project_id()` | 依上述順序解析，失敗則 fail-fast |
| `get_region()` | Cloud Run / Vertex 區域 |
| `get_data_agent_name()` | 組出 `projects/{p}/locations/{loc}/dataAgents/{id}` |
| `get_gold_dataset()` / `get_chunk_embeddings_table()` | BigQuery 資產 |
| `get_bigtable_instance_id()` / `get_bigtable_table_id()` | Bigtable 資產 |
| `get_bigtable_mcp_url()` | 未設定時回傳 `None`（供 fail-fast 判斷） |
| `get_gemini_model()` / `get_embedding_endpoint()` | 模型端點 |
| `get_rag_similarity_threshold()` | RAG 門檻（預設 0.70） |
| `reset_cache()` | 測試用，清除快取 |

**2. `tools.yaml` 改為樣板**

```yaml
sources:
  bigtable-source:
    kind: bigtable
    project: ${PROJECT_ID}
    instance: ${BIGTABLE_INSTANCE_ID}
```

由 `scripts/deploy_mcp.sh` 以 `envsubst` 渲染到 `build/tools.rendered.yaml` 後才上 Secret Manager。
`build/` 已加入 `.gitignore`。

**3. 建置與環境檔去專案化**

- `Dockerfile`：不再 bake 任何專案專屬環境變數；改用非 root 的 `agent` user、`PYTHONPATH=/srv`、遵循 `$PORT`。
- `Makefile`：所有變數改為由環境解析，新增 `make env` 供檢視目前解析結果。
- `.env.example`：只留佔位符；真實值放在 gitignore 的 `.env`。

**4. 回歸防護（重點）**

`tests/test_configuration.py::test_no_hardcoded_project_id_in_source` 會靜態掃描
`app/**/*.py`、`tests/**/*.py`、`*.py`、`*.yaml`、`Dockerfile`、`Makefile`，
以正規表示式偵測「像 GCP project id 的字串常值」。**一旦有人再寫死就會讓建置失敗。**

### 驗證

- `tests/test_configuration.py` — 7 tests passed（含靜態掃描與解析順序單元測試）。
- `tests/conftest.py` 新增 `gcp_environment` session fixture：在無法解析專案的機器上會 **skip 而非 fail**，
  達成「foreign system 可執行」的要求。

---

## Finding 2 — MCP server 實際被繞過（4 個獨立缺陷）

### 稽核原文

> the Model Context Protocol (MCP) server is bypassed in practice because OIDC authorization and
> path routing discrepancies result in immediate fallbacks to a direct gRPC database client scan,
> rendering the declarative tooling settings dead configuration.

這項稽核完全正確，但真實原因比報告描述的更多。逐一拆解如下。

### 缺陷 2-1：路由錯誤（`/sse` → 404）

部署的映像為 `us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:latest`，
`serverInfo` 回報 **Toolbox 1.11.0**。實際探測端點結果：

| 路徑 | 結果 |
|---|---|
| `/` | 200（已驗證） / 403（匿名） |
| `/sse` | **404 Not Found** |
| `/mcp` | GET 405、**POST 200**（Streamable HTTP JSON-RPC） |
| `/mcp/sse` | 200（legacy SSE） |
| `/api/toolset` | **410 Gone** — "native endpoints are disabled by default" |

原實作連的是 `/sse`，直接 404。

**修法**：`app/tools/bigtable_mcp_tool.py` 定義 `MCP_ROUTE = "/mcp"`，改走 Streamable HTTP。

### 缺陷 2-2：部署的 Secret 只有 `sources:`、完全沒有 `tools:`（真正的 "dead configuration"）

即使修好路由，`tools/list` 仍回傳 **空陣列**。追查發現 Secret Manager 中
`bigtable-mcp-tools-secret` 的內容只有 `sources:` 區段，沒有任何 `tools:` 定義 —— 
這正是稽核所說的 "dead configuration" 的字面意義。

**修法**：`tools.yaml` 補上完整的 `bigtable-sql` 工具與 `cymbal_operations` toolset。

過程中同時修正了多個 **GoogleSQL for Bigtable** 的語法陷阱：

| 陷阱 | 錯誤寫法 | 正確寫法 |
|---|---|---|
| `_key` 是 BYTES | `_key LIKE @prefix`（`No matching signature for operator LIKE`） | `STARTS_WITH(_key, CAST(@row_key_prefix AS BYTES))` |
| BYTES→數值轉型 | `CAST(x AS INT64)`（不合法） | `TO_INT64(x)` / `TO_FLOAT64(x)` |
| BYTES→字串 | — | `CAST(_key AS STRING)` 合法 |
| Column family 歸屬 | 誤以為 `risk_score`/`last_event_ts` 在 `flags` → 全為 null | `flags` 只有 `audit_status`；`stats` 有六個指標 + `risk_score` + `last_event_ts` |
| 歷史版本 | 未指定 | 加上 `(WITH_HISTORY => FALSE)` |

最終語句與 Module 2 講義（`module2-handson-instructions.md` L344–360）的 canonical query 對齊。

> **踩雷紀錄**：曾嘗試用 `google.cloud.bigtable.data.BigtableDataClient(...).execute_query(...)`
> 在本機驗證 SQL，結果**無限期 hang 住**。後續改為直接部署後打 MCP 端點驗證（每輪約 50 秒）。

### 缺陷 2-3：OIDC 握手 403 — ADK 的 `header_provider` 不涵蓋 session handshake

這是最不直覺的一點。ADK 2.8 的 `McpToolset` 同時接受
`connection_params.headers`（靜態）與 `header_provider`（動態 callable）。

**實測結論：`header_provider` 只作用於 tool call，不作用於 MCP session 建立階段。**

| 設定 | 結果 |
|---|---|
| 只給 `header_provider` | session 建立 **403 Forbidden** |
| 給 `connection_params.headers`（靜態） | 成功列出工具 |

**修法**：bearer token 同時放進 `connection_params.headers`。由於靜態 header 無法自動換發，
`BigtableOperationsToolset._ensure_mcp_toolset()` 在 token 超過
`_TOKEN_TTL_SECONDS`（45 分鐘）時**重建整個 `McpToolset`**。

### 缺陷 2-4：Cloudtop ADC 身分沒有 `run.invoker`

`google.auth.default()` / `id_token.fetch_id_token()` 在 Cloudtop 上解析到的是共用服務帳號
`insecure-cloudtop-shared-user@cloudtop-prod-asia-east.iam.gserviceaccount.com`。
這個身分**可以簽出 audience 正確的 ID token，但被 Cloud Run 以 403 拒絕**（缺 `roles/run.invoker`）。

可用的憑證是 `gcloud auth print-identity-token`（`admin@kimilo.altostrat.com`，
`aud=32555940559.apps.googleusercontent.com`）—— 注意它的 audience **不等於**服務 URL，
Cloud Run 仍然接受。

> **關鍵教訓**：以「audience 是否符合服務 URL」來挑選憑證是**錯誤的啟發式**。
> 曾據此實作並失敗。正確做法是實際做一次**已驗證的 preflight 探測**。

**修法**：

- `_id_token_strategies()` 列舉可用策略（ADC / gcloud，各含帶或不帶 audience 的變體）。
- `_probe_endpoint()` 實際帶著 token 打一次端點來判定是否可用；網路例外時回傳 `True`
  （網路錯誤不是授權判決）。
- **快取必須以 audience 為 key**。曾因單一全域快取導致跨測試污染：
  `test_toolset_url_is_built_from_configuration` 針對 `https://example-toolbox.a.run.app`
  建立 toolset（DNS 失敗 → 探測回傳 True → 快取了 ADC token），污染真實端點的請求造成 403。
  已改為 `_token_cache: Dict[str, Tuple[Optional[str], float]]` 與
  `_preferred_strategy: Dict[str, str]`。

### 系統性修正：讓「靜默降級」不可能再發生

稽核之所以能發現這個問題，是因為 fallback 太安靜。因此追加：

- **`BIGTABLE_MCP_REQUIRED=true`** fail-fast 開關：設定後，MCP 不可用即拋錯，不再降級。
- fallback 路徑改為輸出 **ERROR / WARNING** 等級 log，明確標示已離開 MCP 路徑。
- 新增 [`scripts/deploy_mcp.sh`](file:///usr/local/google/home/kimilo/dev/elevate-da-adv/elevate-da-adv-day3-labs-agent/scripts/deploy_mcp.sh)：
  `render` → `publish_secret` → `gcloud run deploy` → **`verify`（確認 `tools/list` 非空）**，
  支援 `--render` / `--verify`，並掛到 `make mcp-render|mcp-deploy|mcp-verify`。

> **部署注意**：Secret 以 `key: latest` 掛載於 `/app/tools.yaml`，
> 因此**新增 Secret 版本後必須部署新的 Cloud Run revision** 才會生效。

### 驗證

新增 `tests/test_mcp_contract.py`（9 tests），分為結構測試與 live 整合測試：

- **關鍵斷言**：fallback 的 `FunctionTool` 與 MCP 工具**同名**（都叫 `read_cashier_realtime_metrics`），
  因此測試以 `isinstance(t, McpTool)` 判定，才能真正證明走的是 MCP 路徑。
- 實際發出 JSON-RPC `tools/call` 取得即時資料：

```json
{
  "row_key": "STORE_048#CASH_1190#9221582939625979807",
  "cashier_1h_txn_count": 47,
  "cashier_1h_promo_count": 39,
  "cashier_1h_promo_rate": 0.8297872340425532,
  "cashier_1h_manual_override_count": 35,
  "cashier_1h_total_discount_usd": 10996.49,
  "cashier_1h_avg_discount_pct": 65.53975808109857,
  "risk_score": 9.3e-06,
  "audit_status": "clear",
  "last_event_ts": "2026-09-11T03:27:08.796Z"
}
```

- **端對端證據**：`run_all_tests.py` 的 UC 1.3 與 UC 2.2 trace 顯示呼叫
  `read_cashier_realtime_metrics({'row_key_prefix': 'STORE_048#CASH_1190'})`。
  `row_key_prefix` 是 **MCP 工具的簽章**；fallback 的簽章是 `store_id` / `cashier_id`。
  參數形狀本身即可證明走的是 MCP。

> **測試限制**：`McpTool.run_async(args=..., tool_context=None)` 會拋
> `'NoneType' object has no attribute '_invocation_context'` —— MCP 工具無法在 agent invocation
> 之外執行。測試必須走 `Runner`，或直接發 JSON-RPC（本專案採後者）。

---

## Finding 3 — Out-of-scope 警告字串偏離規格

### 稽核原文

> minor compliance deviations exist in the vector repair search tool, which uses an incorrect
> out-of-scope warning string that diverges from design specifications.

### 根因

有兩層問題：

1. **字串不符**：原本輸出的是自訂措辭，未使用規格要求的認證警告契約字串。
2. **更嚴重的行為偏差**：agent 對於「設備 / 硬體 / 維修」類問題會**直接拒答**，
   根本沒有呼叫 `pos_troubleshooting_rag_tool`。這代表「先查認證文件、查不到才拒答」的
   設計意圖被繞過 —— 拒答訊息宣稱「無認證文件符合」，但其實從未查過。

### 修法

**1. 契約字串**（`app/tools/rag_tool.py` 的 `MANDATORY_DECLINE_WARNING`）：

```
WARNING: UNCERTIFIED RESULT - No certified POS troubleshooting documentation matched
this query above the 0.70 similarity threshold. This request appears to be out of scope
for Cymbal Superstore POS operations. No troubleshooting guidance can be provided.
```

**2. Prompt 規則**（`app/prompts.py`）新增：

- **certified-documentation-first**：任何設備 / 硬體 / 維修相關提問，**必須先呼叫
  `pos_troubleshooting_rag_tool`**，才可以拒答。
- **verbatim relay**：`UNCERTIFIED` 警告必須**原文照抄為回覆的第一行**，不得改寫或摘要。

### 驗證

- `test_mandatory_decline_warning_contract` — 逐字比對常數。
- `test_uc_1_1c_out_of_scope_returns_exact_mandatory_warning` — 斷言工具輸出以該字串**開頭**。
- `run_all_tests.py` UC 1.1c 的斷言已強化：`tool_names` 中**必須包含**
  `pos_troubleshooting_rag_tool`。實際 trace 顯示 agent 現在會先呼叫
  `pos_troubleshooting_rag_tool({'query': 'Ford F-150 engine oil replacement maintenance'})`
  才拒答 —— 行為已修正。

---

## 其他強化（非稽核項目，順帶處理）

| 項目 | 說明 |
|---|---|
| 動態分區守則 | `prompts.py` 明確要求以 `CURRENT_DATE()` / `DATE_SUB(...)` 表達時間窗，**禁止寫死日期**，且必須在回覆中揭露所套用的時間窗 |
| 錯誤遮罩 | `rag_tool` / `bigtable_tool` 加入 3 次指數退避重試與錯誤訊息遮罩；`tests/test_operational_use_cases.py` 以 `FORBIDDEN_LEAKS` + `assert_no_infrastructure_leak()` 驗證不外洩基礎設施細節 |
| BigQuery SEARCH 跳脫 | `SEARCH()` 遇到連字號會 `token recognition error at: '-'`，錯誤碼需以反引號包覆（`` `ERR-PAY-4001` ``），保留於 `_build_search_term()` |
| 可測試性重構 | `bigtable_tool.py` 抽出 `normalize_row_key_prefix()`、`_decode_float`/`_decode_int`，語意與 `TO_FLOAT64`/`TO_INT64` 對齊 |
| 測試基礎建設 | 新增 `pytest.ini`（`asyncio_mode = auto`、`--strict-markers`、註冊 `integration` marker）與 `tests/conftest.py` |
| 文件 | `README.md` 重寫（mermaid 架構圖、可攜性保證、MCP 部署與驗證流程、守則契約、單元／整合測試切分）；`day3lab303.md` 追加 Part 6 |

---

## 驗證總表

| 測試 | 指令 | 結果 |
|---|---|---|
| 單元測試 | `PYTHONPATH=. pytest -m "not integration"` | **17 passed** |
| 完整套件（含 live MCP） | `PYTHONPATH=. pytest` | **25 passed, 62.10s** |
| 端對端 7 個 use case | `PYTHONPATH=. python run_all_tests.py` | **EXIT=0** — "ALL 7 USE CASES VERIFIED WITH STRICT ASSERTIONS ON GEMINI-3.6-FLASH" |

---

## 環境備忘（供後續維護參考）

- **`gcloud` 不在 PATH**：位於 `/usr/local/google/home/kimilo/google-cloud-sdk/bin/gcloud`，
  執行前需 `export PATH=$PATH:/usr/local/google/home/kimilo/google-cloud-sdk/bin`。
- **Cloud Run 服務**：`mcp-toolbox-bigtable` @ `us-central1`，
  `--no-allow-unauthenticated`，SA 為 `423750061893-compute@developer.gserviceaccount.com`。
- **Bigtable**：instance `operations-db`，table `cashier_realtime_alerts`，
  row key 格式 `STORE_xxx#CASH_yyyy#<reverse_timestamp>`；`risk_score >= 0.7` 視為告警。
- **已知無害警告**：ADK 會輸出
  `mTLS was requested but AsyncAuthorizedSession channel is not mTLS` 與
  `GOOGLE_GENAI_USE_VERTEXAI is deprecated`。

---

## 後續建議

1. ~~重新提交評測伺服器：`https://elevate-evaluation-preprod.aishprabhat.demo.altostrat.com/?track=data`（`track=data`）。~~
   **已於 2026-09-11 由使用者自行完成提交。**
2. ~~未追蹤的筆記 `day3lab3.md`、`day3lab302.md` 目前未納入 commit，可視需要一併加入。~~
   **已納入版控。**
3. 若要在 CI 執行，設定 `BIGTABLE_MCP_REQUIRED=true` 可確保 MCP 路徑失效時直接讓建置失敗，
   而非靜默降級。

---

## 相關文件

- **`day4m3-divergences.md`** — Day 4 / Module 3 實作與教材說明的 16 項差異清單
  （評測 harness 行為、Agent Runtime 命名、Gemini Enterprise 授權與 `appType` 陷阱等），
  每一項都附上根因與可直接套用的指令。
- `tests/eval/evaluation_report.md` — Challenge 2.2 的評測報告，含 3 個由評測套件
  實際攔截到的 agent 缺陷與 4 個指標誤判的校正過程。
