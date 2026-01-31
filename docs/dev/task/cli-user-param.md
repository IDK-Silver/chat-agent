# CLI 指定當前對話用戶

CLI 啟動時指定當前用戶，讓 Agent 知道跟誰對話。

## 背景

目前 Agent 不知道跟誰對話：
- 記憶會混亂（跟 A 說的話可能被當成跟 B 說的）
- 無法載入正確的用戶記憶（`people/user-{user_id}.md`）
- 初始化時資訊都跑到 `persona.md`，沒有區分 agent 人格 vs 用戶資訊

## 設計決策

### 與 init 子命令的關係

- **選擇**：`init` 不需要 `--user`；`--user` 只在「進入對話模式」時必填
- **原因**：`init` 只負責建立/升級 workspace，與特定人無關
- **行為**：
  - `uv run python -m chat_agent init`：正常執行
  - `uv run python -m chat_agent --user alice`：進入對話
  - `uv run python -m chat_agent init --user alice`：報錯（避免誤用）

### 用戶識別方式

- **選擇**：CLI `--user` 參數（`user_id`），在對話模式必填
- **原因**：明確、簡單、避免錯誤；同時讓多人記憶在檔案層級可隔離
- **替代方案**：
  - Config 設 default_user（不夠明確，可能誤用）
  - 互動詢問（增加啟動複雜度）

### user_id 規格（格式限制 + 正規化）

`user_id` 會用來組合檔名（例如 `memory/people/user-{user_id}.md`），所以必須限制為「安全且穩定的識別字」，避免檔名混亂或路徑問題。

- **正規化**：
  - 去除前後空白
  - 轉為小寫
- **允許字元**：`a-z`, `0-9`, `_`, `-`
- **正則**：`^[a-z][a-z0-9_-]{0,31}$`
- **例子**：
  - 合法：`alice`, `bob_2026`, `yufeng`
  - 會被拒絕：含空白、`/`、`.`、`@` 等

### 使用者輸入（模糊輸入）

CLI 的 `--user` 允許輸入「`user_id` 或人名」：

- 若輸入符合 `user_id` 正則：視為 `user_id`
- 否則：視為人名（display name），啟動時嘗試從 `memory/people/index.md` 找到對應的 `user_id`
  - 找到：使用該 `user_id`
  - 找不到：自動產生新的 `user_id`，並新增到 `memory/people/index.md`

目標：使用者可以輸入人名，但系統內部永遠用穩定的 `user_id` 做檔名與索引。

### 注入方式

- **選擇**：在 system prompt 中注入 `{current_user}` placeholder
- **原因**：沿用現有的 `{working_dir}` 注入機制，一致性高
- **替代方案**：環境變數（需額外處理）

### short-term.md 與 people/ 的分工

這裡需要把 2 件事講清楚，避免多人（不同 `user_id`）時混淆：

1. `memory/short-term.md` 是 Agent 的 **global working memory**：用來做 context window 的壓縮摘要，包含「最近狀態」與「近期對話摘要（要標 user_id）」
2. `memory/people/` 是「針對特定人的長期記憶（canonical）」：所有「關於某個人的長期資訊」都應該寫到 `people/user-{user_id}.md`

因此本任務對 `brain.md` 的建議是：
- 明確告訴 Agent：目前正在跟 `{current_user}` 對話
- 人的記憶檔案是 `memory/people/user-{current_user}.md`
- 將「關於這個人的長期資訊」寫入該檔案（偏好、背景、關係里程碑等），避免把人資訊寫進 `short-term.md`

## 檔案結構

```
src/chat_agent/
├── __main__.py                     # 修改：加入 --user 參數解析
├── cli/
│   └── app.py                      # 修改：接收 user 參數
└── workspace/
    ├── manager.py                  # 修改：get_system_prompt() 支援 current_user
    └── templates/kernel/system-prompts/
        └── brain.md                # 修改：加入 Current Session 區塊
```

## 技術設計

### CLI 參數

```bash
uv run python -m chat_agent --user alice
```

沒指定 `--user` 時報錯退出。

### WorkspaceManager.get_system_prompt()

```python
def get_system_prompt(self, agent_name: str, current_user: str | None = None) -> str:
    content = prompt_path.read_text()
    content = content.replace("{working_dir}", str(self.working_dir))
    if "{current_user}" in content:
        if not current_user:
            raise ValueError("current_user is required for this system prompt")
        content = content.replace("{current_user}", current_user)
    return content
```

### brain.md 模板新增

```markdown
## Current Session

Talking to: {current_user}
Their long-term memory: memory/people/user-{current_user}.md

Write stable, user-specific information to this file (preferences, background, relationship milestones).
Do not dump raw conversation logs here.
```

## 步驟

1. 修改 `__main__.py`：加入 argparse 解析 `--user` 參數
2. 修改 `cli/app.py`：`main()` 接收 user 參數，傳給 `get_system_prompt()`
3. 修改 `workspace/manager.py`：`get_system_prompt()` 支援 `current_user` 參數
4. 修改 `brain.md` 模板：加入 Current Session 區塊

## 驗證

```bash
# 測試必填
uv run python -m chat_agent
# 預期：報錯 "Error: --user is required"

# init 不需要 user
uv run python -m chat_agent init
# 預期：正常執行（不要求 --user）

# 測試正常啟動
uv run python -m chat_agent --user alice
# 預期：啟動成功，system prompt 包含 "Talking to: alice"

# 模糊輸入（人名）
uv run python -m chat_agent --user "Alice Chen"
# 預期：啟動成功；people/index.md 會新增一筆映射；people/user-<resolved_id>.md 會存在

# user_id 格式限制
uv run python -m chat_agent --user "../x"
# 預期：報錯（invalid user_id）
```

## 完成條件

- [x] `--user` 參數可用
- [x] 沒指定時報錯
- [x] `init` 不需要 `--user`（且誤用會報錯）
- [x] `user_id` 會正規化且驗證格式（避免不安全檔名）
- [x] brain.md 包含當前用戶資訊
- [x] Agent 知道目前對話對象的記憶位置（`people/user-{user_id}.md`；不存在則建立）
