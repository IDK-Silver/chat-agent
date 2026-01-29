# Tool Use 系統

實作 LLM tool use 抽象層，讓 agent 能呼叫工具。

**狀態**：已完成

## 背景

Agent 需要能執行動作（讀寫 memory、搜尋等），這需要 tool use 能力。

各 LLM provider 的 tool use API 不同：
- OpenAI: function calling (`tool_calls` 欄位)
- Anthropic: tool use (`tool_use` content block)
- Gemini: function calling (`FunctionCall`)
- Ollama: 支援 OpenAI 相容端點

需要建立統一的抽象層。

## 設計決策

### Tool 定義格式

- **選擇**：自定義 Pydantic model
- **原因**：型別安全、驗證、易於轉換成各 provider 格式
- **替代方案**：直接用 JSON Schema dict（無型別安全）

### Response 格式

- **選擇**：統一 `LLMResponse` model，包含 `content` 和 `tool_calls`
- **原因**：上層程式碼不需知道各 provider 的回傳格式差異
- **替代方案**：各 provider 回傳不同格式（增加上層複雜度）

### Protocol 設計

- **選擇**：新增 `chat_with_tools()` 方法，保留原 `chat()` 向後相容
- **原因**：不破壞現有程式碼，漸進式升級
- **替代方案**：修改 `chat()` 加入 optional tools 參數（改動較大）

### Ollama 實作

- **選擇**：改用 OpenAI 相容端點 `/v1/chat/completions`
- **原因**：可直接復用 OpenAI 的 tool use schema，減少重複程式碼
- **替代方案**：用原生 `/api/chat`（需另外實作 Ollama 原生 tool 格式）

## 檔案結構

```
src/chat_agent/
├── llm/
│   ├── base.py .............. LLMClient Protocol（擴展 chat_with_tools）
│   ├── schema.py ............ Pydantic models（新增 Tool 相關）
│   └── providers/
│       ├── anthropic.py ..... 支援 tool use
│       ├── openai.py ........ 支援 tool use
│       ├── gemini.py ........ 支援 tool use
│       └── ollama.py ........ 改用 OpenAI 相容端點
├── tools/
│   ├── __init__.py
│   ├── registry.py .......... ToolRegistry 管理工具註冊與執行
│   └── builtin.py ........... 內建工具（get_current_time 等）
└── cli.py ................... 對話迴圈整合 tool call 處理
```

## 技術設計

### Tool 定義 Schema

```python
class ToolParameter(BaseModel):
    type: str  # "string", "number", "boolean", "object", "array"
    description: str
    enum: list[str] | None = None

class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, ToolParameter]
    required: list[str] = []
```

### 統一 Response

```python
class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict

class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = []

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0
```

### Message 擴展

```python
class Message(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None  # assistant
    tool_call_id: str | None = None  # tool result
```

### LLMClient Protocol

```python
class LLMClient(Protocol):
    def chat(self, messages: list[Message]) -> str:
        """Backward compatible: text only."""
        ...

    def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[ToolDefinition]
    ) -> LLMResponse:
        """New method: supports tool use."""
        ...
```

### ToolRegistry

```python
class ToolRegistry:
    def register(self, name: str, func: Callable, schema: ToolDefinition): ...
    def execute(self, name: str, arguments: dict) -> str: ...
    def get_definitions(self) -> list[ToolDefinition]: ...
```

## 步驟

1. **Schema 擴展** (`llm/schema.py`)
   - 新增 `ToolParameter`, `ToolDefinition`
   - 新增 `ToolCall`, `LLMResponse`
   - 擴展 `Message` 支援 `tool` role 和相關欄位

2. **Protocol 擴展** (`llm/base.py`)
   - 新增 `chat_with_tools()` 方法到 Protocol

3. **Anthropic Provider** (`llm/providers/anthropic.py`)
   - 更新 `AnthropicRequest` 加入 `tools` 欄位
   - 新增 `AnthropicToolUseBlock` 解析 tool use response
   - 實作 `chat_with_tools()`

4. **OpenAI Provider** (`llm/providers/openai.py`)
   - 更新 `OpenAIRequest` 加入 `tools` 欄位
   - 更新 `OpenAIResponseMessage` 解析 `tool_calls`
   - 實作 `chat_with_tools()`

5. **Gemini Provider** (`llm/providers/gemini.py`)
   - 更新 `GeminiRequest` 加入 `tools`（FunctionDeclarations）
   - 新增 `GeminiFunctionCall` 解析 function call response
   - 實作 `chat_with_tools()`

6. **Ollama Provider** (`llm/providers/ollama.py`)
   - 改用 OpenAI 相容端點 `/v1/chat/completions`
   - 復用 OpenAI 的 tool use schema
   - 實作 `chat_with_tools()`

7. **Tool Registry** (`tools/`)
   - 建立 `tools/` 模組
   - 實作 `ToolRegistry` class
   - 建立範例 tool `get_current_time`

8. **對話迴圈整合** (`cli.py`)
   - 檢測 `LLMResponse.has_tool_calls()`
   - 執行 tool 並回傳結果
   - 繼續對話直到取得最終文字回應

9. **測試**
   - Tool schema 單元測試
   - Provider tool use 測試（mock API）
   - Tool registry 測試
   - 整合測試

## 驗證

```bash
# 單元測試
uv run pytest tests/

# 手動測試
uv run python -m chat_agent
# 輸入 "現在幾點？"
# 預期：agent 呼叫 get_current_time tool，回傳時間
```

## 完成條件

- [x] Tool schema 定義完成
- [x] LLMClient Protocol 擴展
- [x] Anthropic provider 支援 tool use
- [x] OpenAI provider 支援 tool use
- [x] Gemini provider 支援 tool use
- [x] Ollama provider 支援 tool use（OpenAI 相容端點）
- [x] ToolRegistry 實作
- [x] 對話迴圈整合
- [x] 測試覆蓋
