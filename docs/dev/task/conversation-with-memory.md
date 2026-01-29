# 基礎對話迴圈（含記憶系統）

實作一問一答的對話迴圈，agent 能讀寫 memory。

**狀態**：草稿

## 背景

目前 `cli.py` 有基本對話，但：
- 沒有 system prompt（人格）
- 沒有記憶系統

目標：啟動後能對話，agent 從 memory 載入人格，對話中能讀寫記憶。

見 [bootstrap.md](../memory-system/bootstrap.md) 的設計。

## 設計決策

### Memory 路徑

- **選擇**：`config.yaml` 頂層 `memory_path`
- **預設值**：`~/.chat-agent/memory`

### Bootloader 存放

- **選擇**：模板檔案
- **位置**：`src/chat_agent/prompts/bootloader.md`
- **原因**：prompt 較長，markdown 格式好讀好改

### Memory 讀寫方式

- **選擇**：LLM tool use
- **原因**：agent 有自主權，符合「她會自己維護記憶」的設計理念
- **依賴**：需要先完成 [tool-use.md](tool-use.md)

## 步驟

1. **Config 擴展**
   - AppConfig 新增 `memory_path`
   - 路徑展開（~ → 完整路徑）

2. **Memory 初始化**
   - 首次執行時建立目錄結構
   - 建立基礎檔案（index.md, persona.md 等）
   - persona 包含 agent 自己的資訊（如所在時區），供 tool 呼叫時使用

3. **Bootloader 實作**
   - 定義 bootloader prompt 模板
   - ContextBuilder 支援 bootloader
   - 注入 memory_path

4. **Memory 讀寫工具**
   - 讀取 memory 檔案
   - 寫入 memory 檔案
   - 安全限制（只能存取 memory_path）

5. **對話迴圈整合**
   - CLI 使用 bootloader
   - 整合 memory 工具

## 驗證

- `uv run python -m chat_agent` 啟動對話
- Agent 能載入 persona 並以該人格回應
- Agent 能讀取/寫入 memory
- Agent 能從 persona 讀取自己的資訊（如時區）並用於 tool 呼叫
- 重啟後記憶仍在

## 完成條件

- [ ] Config 支援 memory_path
- [ ] Memory 目錄初始化
- [ ] Bootloader prompt 實作
- [ ] Memory 讀寫功能
- [ ] 對話迴圈整合
- [ ] 測試覆蓋
