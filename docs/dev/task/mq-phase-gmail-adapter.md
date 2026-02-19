# Gmail Adapter + Contact Map

Gmail adapter + 通用 sender 解析快取。

## 背景

詳見設計文件 [message-queue.md](message-queue.md)。

Agent 擁有自己的 Gmail 帳號（hibikikafuu@gmail.com），透過 Gmail API 收發信。不使用 `google-api-python-client`，改用 `httpx`（專案既有依賴）直接呼叫 REST API。

## 元件

### Gmail Adapter

`src/chat_agent/agent/adapters/gmail.py`

- `_GmailClient`：httpx + OAuth2 refresh token 的薄 wrapper
- `GmailAdapter`：ChannelAdapter 實作，polling thread 每 45 秒查一次 inbox
- Email 解析：text/plain 提取、引用/簽名剝離
- 處理完自動 archive

### Contact Map（通用）

`src/chat_agent/agent/contact_map.py`

所有 channel 共用的 sender→name 快取：
- 檔案：`.agent/memory/cache/contact_map.json`
- Layer 1：程式自動查詢（零成本）
- Layer 2：brain 用 `memory_search` 識別 → `update_contact_mapping` 更新快取

### update_contact_mapping Tool

`src/chat_agent/tools/builtin/contact_mapping.py`

Brain 識別陌生 sender 後呼叫，快取到 contact map。

## 設定

### OAuth2 credentials

見 [gmail-oauth-setup.md](../gmail-oauth-setup.md)。三個環境變數存在 `.env`。

### config.yaml

```yaml
channels:
  gmail:
    enabled: true           # false 可暫停 adapter（保留 credentials）
    poll_interval: 45       # 秒，最低 10
```

不寫 `channels` 區塊也能跑（全走預設值）。

## 後續規劃

### 主動寄信（`send_message` tool）

Brain 可透過通用 `send_message(channel, to, body, subject)` tool 主動發信，`to` 使用內部人名（ContactMap 反查地址）。詳見 [message-queue.md](message-queue.md) 的「主動發訊息」段落。依賴 System Adapter / idle 偵測機制。

### CLI 顯示干擾

Gmail 回應 print 到 stdout 時會干擾 prompt_toolkit 的 input prompt。需要用 `patch_stdout` 或類似機制解決。這是所有非 CLI adapter 共通的問題。

## 完成條件

- [x] ContactMap 模組 + 測試
- [x] update_contact_mapping tool + 測試
- [x] GmailAdapter + 測試
- [x] App startup wiring
- [x] Brain system prompt 更新
- [x] Migration M0074（v0.43.0）
- [x] OAuth2 設定教學 + auth script
- [x] 全部測試通過（656 tests）
