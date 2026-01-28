# Config Pydantic 重構

用 Pydantic v2 重構 config 系統，提供 typed models、驗證、早停機制。

## 背景

目前 config 系統使用 `dict` 傳遞設定，缺少：
- 型別檢查：錯誤只在執行時發現
- 驗證：無法提早發現設定錯誤
- 自動補全：IDE 無法提供提示

## 設計決策

### Schema 定義方式

- **選擇**：Pydantic v2 discriminated union
- **原因**：`provider` 欄位天然適合當 discriminator，可自動推斷正確的 config 類型
- **替代方案**：手動 if-else 判斷（已是現狀，維護成本高）

### 檔案組織

- **選擇**：新增 `core/schema.py` 存放所有 config models
- **原因**：避免 `config.py` 過大，職責分離
- **替代方案**：放在 `config.py`（檔案會變大）

### API key 處理

- **選擇**：保留 `api_key_env` 機制，在載入時解析
- **原因**：避免將 secrets 寫入 yaml 檔
- **替代方案**：直接寫 api_key（安全風險）

## 檔案結構

```
src/chat_agent/
├── core/
│   ├── config.py      # 修改：使用 typed models
│   └── schema.py      # 新增：Pydantic models
└── llm/
    ├── factory.py     # 修改：接收 typed config
    └── providers/
        ├── ollama.py    # 修改：接收 typed config
        ├── openai.py    # 修改：接收 typed config
        ├── anthropic.py # 修改：接收 typed config
        └── gemini.py    # 修改：接收 typed config
```

## 技術設計

### LLM Config Models (`core/schema.py`)

```python
from typing import Annotated, Literal
from pydantic import BaseModel, Field

class OllamaConfig(BaseModel):
    provider: Literal["ollama"] = "ollama"
    model: str
    base_url: str = "http://localhost:11434"

class OpenAIConfig(BaseModel):
    provider: Literal["openai"] = "openai"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    base_url: str = "https://api.openai.com/v1"
    max_tokens: int = 4096

class AnthropicConfig(BaseModel):
    provider: Literal["anthropic"] = "anthropic"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None
    max_tokens: int = 4096

class GeminiConfig(BaseModel):
    provider: Literal["gemini"] = "gemini"
    model: str
    api_key: str | None = None
    api_key_env: str | None = None

LLMConfig = Annotated[
    OllamaConfig | OpenAIConfig | AnthropicConfig | GeminiConfig,
    Field(discriminator="provider")
]

class AgentConfig(BaseModel):
    llm: LLMConfig

class AppConfig(BaseModel):
    agents: dict[str, AgentConfig]
```

### Config 載入 (`core/config.py`)

```python
def resolve_llm_config(llm_path: str) -> LLMConfig:
    """Load and validate LLM config."""
    # 載入 yaml -> 驗證 -> 解析 api_key_env -> 回傳 typed config

def load_config(config_path: str = "basic.yaml") -> AppConfig:
    """Load and validate main config."""
```

### Provider Clients

每個 provider client 改為接收對應的 typed config：

```python
class OllamaClient:
    def __init__(self, config: OllamaConfig): ...

class OpenAIClient:
    def __init__(self, config: OpenAIConfig): ...
```

### Factory

```python
def create_client(config: LLMConfig) -> LLMClient:
    match config:
        case OllamaConfig():
            return OllamaClient(config)
        case OpenAIConfig():
            return OpenAIClient(config)
        ...
```

## 步驟

1. 新增 pydantic 依賴至 `pyproject.toml`
2. 建立 `core/schema.py`，定義所有 config models
3. 修改 `core/config.py`，使用 Pydantic 驗證並回傳 typed config
4. 修改 `llm/factory.py`，使用 pattern matching
5. 修改四個 provider clients，接收 typed config
6. 測試載入各 provider config

## 驗證

- `uv run python -c "from chat_agent.core.config import load_config; print(load_config())"`
- 測試各 provider yaml 能正確載入並識別類型
- 故意寫錯 yaml（如 provider 打錯字），確認驗證失敗並給出清楚錯誤訊息

## 完成條件

- [x] Pydantic 依賴已加入
- [x] `schema.py` 定義完整
- [x] `config.py` 使用 typed models
- [x] 所有 provider clients 接收 typed config
- [x] 現有 yaml 檔案可正常載入
