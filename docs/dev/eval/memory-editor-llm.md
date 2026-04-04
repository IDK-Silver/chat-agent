# Memory Editor LLM 模型評估

## 目的

Memory editor planner 使用 `replace_block` 操作時，需要精確複製檔案中的文字作為 `old_block`。
原先使用的 Qwen 3.5 397B 在處理中日文混排文本時，會將半形標點正規化為全形（如 `:` → `：`），
導致 exact match 失敗，回傳 `block_not_found`。

評估替代模型的目標：
1. `old_block` 精確複製（不改標點寬度、空白）
2. 遵守 JSON schema（`replace_block` 使用 `old_block`/`new_block`，非 `payload_text`）
3. 正確 routing 到 `long-term.md` 的對應 section
4. 保留 HTML comment 格式提示
5. 成本合理

## 評估重點

- exact text reproduction（全形/半形標點保真度）
- JSON schema 遵守度
- section routing 正確性（`## 約定` vs `## 重要記錄`）
- 格式正確性（checkbox、日期、保留 comment）
- OpenRouter 價格

## 狀態

完成

## 測試方法

使用 `scripts/repro_memory_edit.py` 復現。

測試情境：對空的 `long-term.md` template 發送指令，要求在 `## 約定` section 新增一條禁用顏文字的規則。
該 template 包含混合全形/半形標點的 HTML comment（`對象: 內容`，半形冒號），是 Qwen 最容易出錯的場景。

## 測試紀錄

### 測試日期：2026-04-05

測試指令：
```
Add a new rule to long-term.md: Do not use kaomoji like (.|heart|.|) in messages.
User (Yu-Feng) said it looks weird.
This is a behavioral constraint (should go to the agreements section).
```

| Model | $/M (in/out) | old_block 正確 | schema 正確 | section routing | 保留 comment | 總評 |
|-------|-------------|---------------|------------|----------------|-------------|------|
| qwen/qwen3.5-397b-a17b (thinking) | $0.39/$2.34 | NO | YES | YES | YES | **FAIL** |
| google/gemini-2.5-flash-lite (no-thinking) | $0.10/$0.40 | — | NO (3 次 retry 後勉強) | NO (`append_entry` 到檔尾) | — | **FAIL** |
| google/gemini-2.5-flash-lite (thinking) | $0.10/$0.40 | — | NO (parse 全失敗) | — | — | **FAIL** |
| openai/gpt-4o-mini | $0.15/$0.60 | YES | YES | YES | NO (刪掉 comment) | 勉強 |
| openai/gpt-5.4-mini | $0.75/$4.50 | YES | YES | YES | YES | **PASS** |
| anthropic/claude-haiku-4.5 | $1.00/$5.00 | YES | YES | YES | YES | **PASS** |
| anthropic/claude-sonnet-4.6 | $3.00/$15.00 | YES | YES | YES | YES | **PASS** |

### 失敗分析

**Qwen 3.5 397B**：半形冒號 `:` 被正規化為全形 `：`，是 CJK 語言模型常見行為。
檔案實際內容 `對象: 內容`，LLM 輸出 `對象：內容`，exact match 失敗。

**Gemini 2.5 Flash Lite**：連 JSON schema 都無法穩定遵守。
`replace_block` 要求 `old_block`/`new_block`，但它用 `payload_text`。
Thinking 模式更差，3 次 retry 全部 parse 失敗。

**GPT-4o-mini**：能正確複製 block，但會刪掉 HTML comment（格式提示），
後續新增條目時就沒有格式參考了。

## 結論

採用 **openai/gpt-5.4-mini** 作為 memory_editor 主 LLM，**anthropic/claude-haiku-4.5** 作為 fallback。

決策理由：
- gpt-5.4-mini 是價格/品質最佳平衡點（$0.75/M input），所有測試項目全過
- claude-haiku-4.5 同樣全過，作為 fallback 確保兩層都不會出現 block_not_found
- 比原先 qwen3.5-397b ($0.39/M) 貴約 2 倍，但消除了 CJK 標點正規化問題
- Gemini 2.5 Flash Lite 雖然最便宜 ($0.10/M)，但品質完全不夠格
