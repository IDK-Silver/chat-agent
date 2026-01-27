# Ollama Cloud

## 目的

評估 Ollama Cloud 作為 LLM provider 的可行性。Ollama Cloud 讓沒有強大本地 GPU 的使用者也能透過雲端執行大型模型，同時保持與本地 Ollama 一致的 API 體驗。

## 評估重點

- API 相容性：是否與現有 Ollama 本地 API 完全相容
- 使用限制：每小時/每週限制對實際使用的影響
- 功能支援：tool calling、streaming、structured outputs 等進階功能
- 隱私考量：資料上傳至 Ollama 伺服器的風險評估
- 穩定性：preview 階段的功能變動風險

## 狀態

待測試

## 測試紀錄

### 核心概念

雲端模型的行為與本地模型相同，可以使用 `ls`、`run`、`pull`、`cp` 等指令操作。模型名稱帶有 `-cloud` 後綴：
- `gpt-oss:120b-cloud`
- `deepseek-v3.1:671b-cloud`
- `qwen3-coder:480b-cloud`

### 使用方式

需要 Ollama v0.12+，先登入帳號：

```bash
ollama signin
ollama run gpt-oss:120b-cloud
```

也可以直接透過 ollama.com 的 API 存取，需要先建立 API key：

```python
import os
from ollama import Client

client = Client(
    host="https://ollama.com",
    headers={'Authorization': 'Bearer ' + os.environ.get('OLLAMA_API_KEY')}
)
response = client.chat('gpt-oss:120b', messages=[...])
```

### 限制與定價

有每小時和每週的使用限制，未來會提供按用量計費。Premium model requests 用於更大的模型（如 Gemini 3 Pro Preview），不計入一般限制：
- Free: 5 premium requests/月
- Pro: 20 premium requests/月
- Max: 100 premium requests/月

### 功能支援

支援 tool calling、thinking traces、streaming、structured outputs、vision 等進階功能。

### Trade-offs

**優點**：
- 統一的 API/CLI 體驗，本地與雲端無縫切換
- Ollama 不保留你的資料
- 可執行本地硬體跑不動的大模型

**缺點**：
- 有使用量限制
- 需要網路連線
- 資料需上傳至 Ollama 伺服器（雖然官方聲稱不保留）
- 目前仍是 preview，功能可能變動

## 結論

待定（需實際測試後填寫）
