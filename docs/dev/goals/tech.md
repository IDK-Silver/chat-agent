# 技術方向

- LLM：抽象層，支援多種 provider（Claude、OpenAI 等）
- 介面：先做 Terminal CLI
- 記憶：.md 檔案系統，類似 Claude skills 的動態載入
  - 詳細設計見 [memory-system/](memory-system/index.md)
  - 使用 Grep 檢索，不使用 RAG
  - Agent 自我維護（歸檔、更新索引）
