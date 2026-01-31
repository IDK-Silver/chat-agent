# 維護機制

## 歸檔觸發條件

### 知識歸檔 (knowledge/)

| 條件 | 操作 |
|------|------|
| 檔案超過 500 行 | 按主題拆分，舊檔移至 archive/ |
| 內容不再常用 | 移至 archive/knowledge/ |

### 思考歸檔 (thoughts/)

| 條件 | 操作 |
|------|------|
| 月份結束 | 建立新月份檔案 |
| 檔案過大 | 按週拆分，舊檔移至 archive/ |

### 經歷歸檔 (experiences/)

| 條件 | 操作 |
|------|------|
| 超過 30 天無互動 | 移至 archive/{user_id}/ |

### 用戶記憶歸檔 (people/)

| 條件 | 操作 |
|------|------|
| 對話結束 | 記錄至 archive/{user_id}/{date}.md |
| 超過 90 天無互動 | user-{user_id}.md 移至 archive/{user_id}/ |

## 載入規則

### 啟動載入流程

```
啟動
  ↓
memory/short-term.md（工作記憶快照）
  ↓
memory/agent/index.md
  ↓
├─ persona.md (必須)
├─ config.md (必須)
├─ knowledge/index.md → 載入相關 knowledge
├─ thoughts/index.md → 載入當前月份 thoughts
├─ experiences/index.md → 載入最近接觸的 experiences
└─ skills/index.md → 載入所有 skills
      ↓
根據當前對話人 → people/user-{user_id}.md
```

### 動態載入

| 情境 | 載入內容 |
|------|---------|
| 問及特定主題 | knowledge/{topic}.md |
| 問及特定時間 | thoughts/{month}.md |
| 問及特定人物 | experiences/{user_id}.md 或 people/user-{user_id}.md |

## Grep 檢索流程

### 檢索流程

```python
def retrieve_memory(query: str, context: dict) -> list[str]:
    """
    使用 Grep 檢索相關記憶
    """
    # 1. 確定檢索範圍
    search_paths = determine_search_scope(context)

    # 2. 執行 Grep 檢索
    results = []
    for path in search_paths:
        # 使用 Grep 工具檢索關鍵字
        matches = grep(query, path)
        results.extend(matches)

    # 3. 如果結果不足或模糊，使用 LLM 輔助
    if len(results) < 3 or is_ambiguous(results, query):
        expanded_queries = llm_expand_query(query)
        for expanded in expanded_queries:
            matches = grep(expanded, search_paths)
            results.extend(matches)

    # 4. 去重並排序
    results = deduplicate(results)
    results = rank_by_relevance(results, query)

    return results[:10]  # 返回最相關的 10 筆
```

### 檢索範圍決定

```python
def determine_search_scope(context: dict) -> list[str]:
    """
    根據對話情境決定檢索範圍
    """
    scope = []

    # 基礎範圍
    scope.append("memory/agent/knowledge/")
    scope.append("memory/agent/skills/")

    # 根據情境擴充
    if "user_id" in context:
        scope.append(f"memory/people/user-{context['user_id']}.md")
        scope.append(f"memory/agent/experiences/{context['user_id']}.md")

    if "time_period" in context:
        scope.append(f"memory/agent/thoughts/{context['time_period']}.md")

    return scope
```

### LLM 輔助擴展查詢

```python
def llm_expand_query(query: str) -> list[str]:
    """
    使用 LLM 擴展查詢關鍵字
    """
    prompt = f"""
    擴展以下查詢，生成相關的中文關鍵字列表（最多 5 個）：
    查詢: {query}

    格式: 關鍵字1, 關鍵字2, ...
    """
    # 呼叫 LLM
    response = call_llm(prompt)
    return parse_keywords(response)
```

## Agent 自我維護流程

### 定期維護

```python
def periodic_maintenance():
    """
    定期維護任務
    """
    # 1. 檢查檔案大小
    oversized_files = find_oversized_files("memory/", max_lines=500)
    for file in oversized_files:
        split_and_archive(file)

    # 2. 檢查過期內容
    stale_content = find_stale_content("memory/", days=30)
    for content in stale_content:
        archive_content(content)

    # 3. 更新索引
    update_all_indices()

    # 4. 壓縮短期工作記憶
    compact_short_term_memory()
```

### 對話後維護

```python
def post_conversation_maintenance(conversation: dict):
    """
    對話結束後的維護
    """
    person = conversation["person"]

    # 1. 歸檔對話
    archive_conversation(conversation)

    # 2. 更新用戶記憶
    update_user_memory(person, conversation)

    # 3. 更新 Agent 經歷
    update_agent_experience(person, conversation)

    # 4. 更新索引
    update_indices()
```

## 檔案大小控制策略

| 類型 | 建議行數 | 拆分策略 |
|------|---------|---------|
| knowledge/ | 200-400 行 | 按子主題拆分 |
| thoughts/ | 300-500 行 | 按週拆分 |
| experiences/ | 200-300 行 | 按事件拆分 |
| user-{user_id}.md | 100-200 行 | 只保留摘要 |
| short-term.md | < 200 行 | 只保留近期摘要（過長就再壓縮） |

## 索引更新

### 更新觸發條件

- 新增或刪除檔案
- 歸檔操作後
- 對話結束後

### 更新內容

- 檔案列表
- 行數統計
- 最後更新時間
