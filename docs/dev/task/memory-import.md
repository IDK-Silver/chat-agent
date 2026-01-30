# Memory 匯入（ChatGPT/Claude/Line）

實作對話歷史匯入功能，讓 agent 能「腦袋搬家」。

**狀態**：草稿

## 背景

用戶可能有過去與其他 AI 的對話歷史（ChatGPT、Claude、Line 聊天機器人等），希望新的 agent 能繼承這些記憶。

這需要：
- 解析不同平台的匯出格式
- 轉換為 memory 系統的格式
- 讓 agent 「消化」這些記憶

## 設計決策

### 匯入架構

<!-- TODO: 待設計 -->

- **位置**：`src/chat_agent/tools/memory/importers/`
- **格式**：每個平台一個 importer

### 支援的平台

<!-- TODO: 待確認優先順序 -->

1. ChatGPT（JSON 匯出）
2. Claude（JSON 匯出）
3. Line（文字匯出）

## 步驟

<!-- TODO: 待規劃 -->

1. 設計 importer 介面
2. ChatGPT importer
3. Claude importer
4. Line importer
5. CLI 整合（`chat-agent import`）

## 驗證

<!-- TODO: 待規劃 -->

## 完成條件

- [ ] Importer 介面設計
- [ ] ChatGPT importer
- [ ] Claude importer
- [ ] Line importer
- [ ] CLI import 命令
- [ ] 測試覆蓋
