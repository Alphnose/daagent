# Day 4 / Module 3 — 實作與教材說明的差異清單

> 對照教材：`04-module3-deployment-handson-instructions.md`（Day 4 Labs）
> 環境：`antigravity-503007`（專案編號 `423750061893`）、`agents-cli` 1.5.0、ADK Python
> 最後更新：2026-09-11

本文件記錄「照教材字面操作會失敗或得到非預期結果」的每一處，以及實際採用的作法與原因。
共 16 項，依所屬 Challenge 分組。

---

## A. Challenge 2.x — 評測（Evaluation）

### #1 `basic-dataset.json` 是「已灌好的 trace」，不是輸入資料集

教材寫 `agents-cli eval run --dataset tests/eval/datasets/basic-dataset.json`。
實際執行時 10 個 case 全數失敗：

```
Case has both top-level 'prompt' and agent_data.turns; ambiguous.
```

**原因**：`eval/cmd_generate.py::split_case_history` 規定 `prompt` 與 `agent_data.turns`
互斥（XOR）。`basic-dataset.json` 兩者都有，因為它本來就是「已經跑完、含完整
trajectory 的成品」，用途是直接送去評分，不是拿去產生新的 trace。

**採用作法**：

```bash
agents-cli eval grade --traces tests/eval/datasets/basic-dataset.json \
  --metrics tool_use_quality,grounding
```

### #2 內建指標的量尺是 0.0–1.0，不是 1–5

教材的品質門檻寫「>= 4.0 / 5.0」。但 `tool_use_quality_v1`、`grounding_v1` 等
內建指標回傳的是 0.0–1.0 的正規化分數。

**換算**：`4.0 / 5.0` 等同於 `>= 0.80`。

實測結果：`tool_use_quality_v1` **0.9333**（= 4.67/5）、`grounding_v1` **1.0000**（= 5.00/5），
兩項皆通過。

### #3 `grounding_v1` 硬性要求預先填好的 `context` 欄位

`grounding_v1` 只能用在「已經含有 retrieved context 的 trace」上，無法用於
`eval generate` 新產生的 trace（該欄位為空 → 指標直接報錯）。

**採用作法**：Challenge 2.1 的基準閘門仍照教材使用 `grounding`（因為
`basic-dataset.json` 本來就填好了）；自建的評測套件改用
`final_response_quality_v1` 取代。理由詳見 `tests/eval/evaluation_report.md` §2.4。

### #4 `tool_use_quality_v1` 在「沒有工具呼叫」的 case 上必定報錯

```
requires tool calls in the evaluation trace
```

這在本專案是**預期行為**，不是缺陷：套件中有 3–4 個「刻意讓 agent 拒答」的
守則測試 case（prompt injection、超出範圍的請求等），agent 正確拒答就不會呼叫任何工具。

**判讀方式**：單輪套件 11 個 case 中 7 個有效、4 個 error，
`tool_use_quality_v1` 的 0.9079 是那 7 個的平均。error 的 4 個要另外用
`guardrail_compliance`（5.0000 滿分）來看。

### #5 `judge_model` 必須是完整的 model resource name

在 `eval_config.yaml` 寫 `judge_model: gemini-flash-latest` 或 `gemini-2.5-flash`
都會被拒：

```
Invalid autorater model resource name
```

**採用作法**：移除該 key，改用服務端預設的 autorater。

### #6 多輪資料的每一個 turn 都必須顯式帶 `turn_index`

教材沒有提到這件事。若 `agent_data.turns` 中任一 turn 缺少 `turn_index`，
所有 service-side 指標會整批失敗：

```
Required field is not set
```

**注意**：`eval generate` 只會替它自己新產生的那個 turn 補上 `turn_index`，
手寫的歷史 turn 不會自動補。`tests/eval/datasets/eval-data2.json` 的 10 個 turn
全部已手動補齊。

### #7 `multi_turn_general_quality_v1` 其實不是多輪指標

名稱有 `multi_turn` 但拒收多輪資料：

```
Single-turn metric ... received agent_eval_data with 2 turns
```

`safety_v1`、`tool_use_quality_v1` 同樣只支援單輪。

**實際可用的多輪指標**：`multi_turn_tool_use_quality_v1`、
`multi_turn_trajectory_quality_v1`、`multi_turn_task_success_v1`。
這也是為什麼單輪與多輪必須用**兩組不同的 `--metrics`** 分開跑
（見 `eval_config.yaml` 的註解區塊）。

### #8 宣告式 `prompt_template` 指標在服務端渲染，缺一個 placeholder 就整個失敗

自訂指標若用 `prompt_template` 宣告，模板中的每個 `{變數}` 都必須存在。
多輪 case 沒有 `{prompt}` 這個欄位，於是：

```
Variable prompt is required but not provided
```

**採用作法**：多輪版本改成檔案式自訂指標
`routing_precision_multiturn`（`custom_function_file: routing_precision.py`），
在本地端 Python 內自行組裝上下文。

> 附帶陷阱：`custom_function_file` 的內容會被**內聯後以 `exec` 編譯**，
> 因此檔案內**取不到 `__file__`**。`metrics.py` 與 `routing_precision.py`
> 都改用 `pathlib.Path.cwd()` 往上走來定位共用程式碼。

### #9 宣告式 autorater 會間歇性回傳非法 JSON

11 個 case 中有 3 個回 400，原因是 autorater 輸出了
`{Score: 5 Explanation: ...}` 這種缺引號的偽 JSON。

**採用作法**：在 prompt 中加入明確的 OUTPUT CONTRACT 區塊與一個
格式正確的範例 → 錯誤數降為 **0**。

### #10 `eval_config.yaml` 沒有 `threshold`，也沒有 pass/fail 閘門

`eval_utils.py` 只讀取 `metrics_to_run` 與 `custom_metrics` 兩個 key。
即使所有指標都 0 分，CLI 的 exit code 仍然是 0。

**意義**：品質閘門是**人工判讀**，不是自動化的。教材的「>= 4.0 通過」
需要自己看報表確認。

---

## B. Challenge 3.1 — 部署至 Agent Runtime

### #11 服務名稱 `cymbal_operations_agent`（含底線）

第一次部署誤用了 `cymbal-operations-agent`（連字號）。教材要求的是底線版本。

**修正方式**：透過 v1beta1 reasoningEngines API 直接改 display name：

```
PATCH .../reasoningEngines/512334485392457728?updateMask=displayName
```

**補充**：`agents-cli` 完全不做名稱正規化
（`deploy/_utils.py::resolve_service_name` 只是原封不動回傳 override），
底線是合法的。引擎是靠 **display name** 比對的，所以改完之後
`--update-only` 重新部署才能正確對應到同一個引擎。

---

## C. Challenge 3.2 — 註冊至 Gemini Enterprise

> 這一組是本次耗時最久的部分，四項差異環環相扣。

### #12 Gemini Enterprise 授權是 **location-scoped** 資源

`licenseConfigs` 綁在 location 上，`global` 與 `us` 是**兩個完全獨立**的集合：

| 專案 / 區域 | `licenseConfigs` |
|---|---|
| `antigravity-503007` / `global` | `{}` — 無 |
| `antigravity-503007` / **`us`** | **`free_trial_gemini` ACTIVE，50 seats，至 2026-10-10** |
| `antigravity-503007` / `eu` | `{}` — 無 |

而且 `us` / `eu` **必須使用區域端點**，否則會被誤導：

```bash
# ❌ 用預設端點查 us → INVALID_ARGUMENT: Incorrect API endpoint used
https://discoveryengine.googleapis.com/v1alpha/projects/$P/locations/us/licenseConfigs

# ✅ 正確
https://us-discoveryengine.googleapis.com/v1alpha/projects/$P/locations/us/licenseConfigs
```

**踩坑經過**：只查了 `global`（回 `{}`）就判定「這個專案沒有授權」，
於是 `publish` 一直卡在：

```
FAILED_PRECONDITION: The user cannot create an agent since an active
Gemini Enterprise license is not available.
```

一度誤以為授權在另一個專案 `demochictrip`，還跨專案建了一組 app（後已刪除）。

**排查用指令**（建議日後直接套用）：

```bash
TOKEN=$(gcloud auth print-access-token); P=<project-id>
for L in global us eu; do
  HOST=$([ $L = global ] && echo discoveryengine.googleapis.com || echo $L-discoveryengine.googleapis.com)
  echo "--- $L ---"
  curl -s -H "Authorization: Bearer $TOKEN" -H "X-Goog-User-Project: $P" \
    "https://$HOST/v1alpha/projects/$P/locations/$L/licenseConfigs"
done
```

判讀要點：
- 回 `{}` = **該區域**沒授權，不代表整個專案沒有
- 回 `PERMISSION_DENIED ... API has not been used` = Discovery Engine API 未啟用
- 要有 `"state": "ACTIVE"` 才算數，順便看 `endDate`
- 使用者座位查 `.../locations/$L/userStores/default_user_store/userLicenses`，
  `ASSIGNED` 才有座位；`NO_LICENSE_ATTEMPTED_LOGIN` 表示登入過但沒座位

### #13 教材沒有指明 GE app 要建在哪個 location

教材只寫「create the app `da-adv-elevate-ge` in the console if it does not already exist」。

- **Console 的 Create app 精靈預設建在 `us`**
- **Discovery Engine API 文件的範例預設是 `global`**

兩者不一致，加上 #12 的授權也綁區域，導致「app 建好了但永遠拿不到授權」。

**採用作法**：全部統一在 **`us`**，與授權所在區域一致。

### #14 `appType: APP_TYPE_INTRANET` 是 GE app 的身分標記，且**不可修改**

用 API 建 engine 時若沒帶 `appType`，建出來的是普通的
「AI Applications / Search app」：

- `ListEngines` API **查得到**它
- 但 Gemini Enterprise Console 的清單**看不到**它（Console 以 `appType` 過濾）

這個組合非常容易誤判成「建好了只是 Console 還沒同步」。

想事後補上會被拒：

```
400 INVALID_ARGUMENT
Field "updateMask" contains an immutable path "app_type".
```

**只能砍掉重建。** 正確的建立 payload：

```json
{
  "displayName": "da-adv-elevate-ge",
  "solutionType": "SOLUTION_TYPE_SEARCH",
  "industryVertical": "GENERIC",
  "appType": "APP_TYPE_INTRANET",
  "searchEngineConfig": {
    "searchTier": "SEARCH_TIER_ENTERPRISE",
    "searchAddOns": ["SEARCH_ADD_ON_LLM"],
    "requiredSubscriptionTier": "SUBSCRIPTION_TIER_SEARCH_AND_ASSISTANT"
  },
  "knowledgeGraphConfig": { "enablePrivateKnowledgeGraph": true }
}
```

**帶對 `appType` 之後，建立流程從三步縮成一步**：

| 步驟 | 沒有 `appType` | 有 `APP_TYPE_INTRANET` |
|---|---|---|
| 先建 data store | **必要**（否則 `At least one data store id or one data store must be present`） | **不需要**，`dataStoreIds` 可省略 |
| 建 engine | 需要 | 需要 |
| 建 `assistants/default_assistant` | **必要**（否則 `publish` 會 404） | **自動生成**，還自帶 `webGroundingType: WEB_GROUNDING_TYPE_GOOGLE_SEARCH` |

另一個確認是真 GE app 的訊號：內建的 **`Deep Research`** agent 會自動出現在
`assistants/default_assistant/agents` 清單中。

### #15 Agent 的「User permissions」不走 IAM，而是 `sharingConfig`

原以為要用 `engines:setIamPolicy`，但：

- `agents:getIamPolicy` / `agents:setIamPolicy` → **404，方法不存在**
- 實際欄位是 Agent 資源自己的 `sharingConfig.scope`，
  enum 為 `SCOPE_UNSPECIFIED` / `RESTRICTED` / `ALL_USERS`

教材要求的「User permissions 讓所有使用者都能發現並對話」對應：

```bash
curl -X PATCH \
  "https://us-discoveryengine.googleapis.com/v1alpha/${AGENT}?updateMask=sharingConfig" \
  -d '{"sharingConfig":{"scope":"ALL_USERS"}}'
```

### 補充：`publish` 的 app id 必須是完整資源名稱

`--gemini-enterprise-app-id` 不接受裸 id，必須寫成：

```
projects/{project_number}/locations/{location}/collections/{collection}/engines/{engine_id}
```

註意是 **project number**（`423750061893`）不是 project id。

---

## D. Challenge 1.x / 4.x — 其他

### #16 `agent_telemetry` dataset 已經存在

Challenge 1.1 在本環境是 no-op。既有的 25 個 `v_*` 檢視表也已自動建立完成，
無需重跑。Challenge 4.1 的資料 agent 直接掛在既有的 26 個物件上。

---

## 最終狀態速查

| 項目 | 值 |
|---|---|
| Agent Runtime | `projects/423750061893/locations/us-central1/reasoningEngines/512334485392457728` |
| Display name | `cymbal_operations_agent` |
| 服務帳號 | `cymbal-sa-data@antigravity-503007.iam.gserviceaccount.com` |
| GE App | `projects/423750061893/locations/us/collections/default_collection/engines/da-adv-elevate-ge` |
| GE App `appType` | `APP_TYPE_INTRANET` |
| 註冊的 Agent | `.../assistants/default_assistant/agents/692931296459343774` |
| Agent 狀態 | `state=ENABLED`、`sharingConfig.scope=ALL_USERS` |
| GE 授權 | `projects/423750061893/locations/us/licenseConfigs/free_trial_gemini`（ACTIVE，50 seats，2026-09-10 ~ 2026-10-10） |
| Console | https://console.cloud.google.com/gemini-enterprise/locations/us/engines/da-adv-elevate-ge/overview/dashboard?project=antigravity-503007 |
