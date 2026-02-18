> **歸檔日期**：2026-02-18

# Migration 系統基礎架構

建立類似 Alembic 的 kernel 版本遷移系統，支持多次版本升級。

## 背景

現有的 `upgrade_kernel()` 直接刪除舊 kernel 並複製新的，無法處理複雜的升級邏輯。需要一個可擴展的遷移系統，讓每次升級成為獨立的腳本。

## 設計決策

### 遷移執行方式

- **選擇**：順序執行所有 pending migrations
- **原因**：簡單可靠，每個 migration 只需關注單一版本升級
- **替代方案**：直接跳到最新版（無法處理需要中間步驟的情況）

### 版本比較

- **選擇**：字串比較（如 "0.1.3" < "0.2.0"）
- **原因**：語義化版本的字串比較足夠簡單場景
- **替代方案**：使用 packaging.version（依賴更重，目前不需要）

## 檔案結構

```
src/chat_agent/workspace/
├── migrations/
│   ├── __init__.py       # 導出 ALL_MIGRATIONS
│   ├── base.py           # Migration 抽象基類
│   ├── m0001_initial.py  # 初始版本 0.1.3（no-op）
│   └── m0002_agents_structure.py  # 0.2.0 結構變更
├── migrator.py           # Migrator 類
├── manager.py            # （現有）
└── initializer.py        # 修改使用 migrator
```

## 技術設計

### Migration 基類

```python
from abc import ABC, abstractmethod
from pathlib import Path

class Migration(ABC):
    """Base class for kernel migrations."""

    version: str  # Target version after this migration

    @abstractmethod
    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        """Execute the migration."""
        pass

    @property
    def name(self) -> str:
        return self.__class__.__name__
```

### Migrator

```python
class Migrator:
    def __init__(self, kernel_dir: Path, templates_dir: Path):
        self.kernel_dir = kernel_dir
        self.templates_dir = templates_dir

    def get_current_version(self) -> str:
        """Read version from kernel/info.yaml."""

    def get_pending_migrations(self) -> list[Migration]:
        """Return migrations with version > current."""

    def needs_migration(self) -> bool:
        """Check if any migrations are pending."""

    def run_migrations(self) -> list[str]:
        """Run all pending migrations, return applied versions."""
```

### 修改 WorkspaceInitializer

```python
class WorkspaceInitializer:
    @property
    def migrator(self) -> Migrator:
        """Lazy-loaded migrator instance."""

    def needs_upgrade(self) -> bool:
        return self.migrator.needs_migration()

    def upgrade_kernel(self) -> list[str]:
        return self.migrator.run_migrations()
```

## 步驟

1. 建立 `migrations/` 目錄
2. 建立 `migrations/base.py` - Migration 基類
3. 建立 `migrations/m0001_initial.py` - 初始版本（no-op）
4. 建立 `migrations/__init__.py` - 導出 ALL_MIGRATIONS
5. 建立 `migrator.py` - Migrator 類
6. 修改 `initializer.py` 使用 Migrator
7. 更新 `workspace/__init__.py` 導出

## 驗證

- `from chat_agent.workspace import Migrator` 可正常導入
- 建立測試 workspace，執行 `migrator.run_migrations()` 無錯誤
- `needs_migration()` 對已是最新版本返回 False

## 完成條件

- [ ] migrations/ 目錄結構建立
- [ ] Migration 基類可繼承使用
- [ ] Migrator 可正確識別 pending migrations
- [ ] WorkspaceInitializer.upgrade_kernel() 使用 migrator
