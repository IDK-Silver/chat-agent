# Python uv 環境管理

本文件說明使用 uv 管理 Python 專案環境的流程。

## 初始化專案

### 建立新專案

```bash
uv init
```

此命令會建立：
- `pyproject.toml` - 專案設定檔
- `.python-version` - Python 版本設定

### 設定 src layout

1. 建立套件目錄結構：
   ```bash
   mkdir -p src/{package_name}
   ```

2. 建立 `src/{package_name}/__init__.py`：
   ```python
   """Package description."""

   __version__ = "0.1.0"
   ```

3. 更新 `pyproject.toml`，加入 build-system：
   ```toml
   [build-system]
   requires = ["hatchling"]
   build-backend = "hatchling.build"

   [tool.hatch.build.targets.wheel]
   packages = ["src/{package_name}"]
   ```

4. 同步環境：
   ```bash
   uv sync
   ```

## 套件管理

### 新增依賴

```bash
uv add {package_name}
```

範例：
```bash
uv add requests
uv add pytest --dev  # 開發依賴
```

### 移除依賴

```bash
uv remove {package_name}
```

### 同步環境

```bash
uv sync
```

## 執行指令

所有 Python 相關指令都透過 `uv run` 執行：

```bash
uv run python script.py
uv run pytest
uv run python -m module_name
```

## 驗證環境

```bash
uv run python -c "import {package_name}; print({package_name}.__version__)"
```
