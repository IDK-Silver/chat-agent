"""AX-first GUI: deploy the new gui_manager prompt for the MCP-backed loop."""

from pathlib import Path
import shutil

from .base import Migration


class M0160AxFirstGui(Migration):
    """Switch the GUI loop from vision-worker bboxes to AX-first tools."""

    version = "0.74.18"
    summary = (
        "GUI 任務改為 AX-first 架構：gui_manager 直接驅動本地 OpenComputerUse "
        "MCP server（accessibility tree + 視窗截圖 + 背景輸入），不再透過 "
        "gui_worker 猜座標；gui_worker 僅保留給 screenshot_by_subagent。"
        "gui_task 介面不變。首次啟動會自動編譯 MCP server（需要 swift；"
        "supervisor 的 ax-server-build oneshot 會處理）"
    )

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        src = templates_dir / "agents" / "gui_manager" / "prompts" / "system.md"
        dst = kernel_dir / "agents" / "gui_manager" / "prompts" / "system.md"
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
