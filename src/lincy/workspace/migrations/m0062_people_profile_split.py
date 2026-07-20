"""Split people content: {user_id}/index.md -> {user_id}/basic-info.md.

- Rename each people/{user_id}/index.md to basic-info.md
- Create new stub index.md with navigation links
- Copy updated prompt templates (brain, post_reviewer, shutdown_reviewer)
"""

import shutil
from pathlib import Path

from .base import Migration


class M0062PeopleProfileSplit(Migration):
    """Move people content from index.md to basic-info.md."""

    version = "0.32.0"

    _PROMPT_FILES = [
        ("brain", "system.md"),
        ("brain", "shutdown.md"),
        ("post_reviewer", "system.md"),
        ("shutdown_reviewer", "system.md"),
    ]

    def upgrade(self, kernel_dir: Path, templates_dir: Path) -> None:
        # 1. Copy updated prompt templates.
        for agent, prompt_file in self._PROMPT_FILES:
            src = templates_dir / "agents" / agent / "prompts" / prompt_file
            dst = kernel_dir / "agents" / agent / "prompts" / prompt_file
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        # 2. Rename people/*/index.md -> basic-info.md and create stub index.
        memory_dir = kernel_dir.parent / "memory"
        people_dir = memory_dir / "people"
        if not people_dir.is_dir():
            return

        for user_dir in sorted(people_dir.iterdir()):
            if not user_dir.is_dir():
                continue
            old_index = user_dir / "index.md"
            basic_info = user_dir / "basic-info.md"

            if not old_index.exists() or basic_info.exists():
                continue

            # Rename content file
            shutil.move(str(old_index), str(basic_info))

            # Build stub index.md with links to all sibling .md files
            siblings = sorted(
                f.name for f in user_dir.iterdir()
                if f.is_file() and f.suffix == ".md"
            )
            # Extract display name from first heading of basic-info.md
            name = user_dir.name
            try:
                first_line = basic_info.read_text(encoding="utf-8").split("\n", 1)[0]
                if first_line.startswith("# "):
                    name = first_line[2:].strip()
            except Exception:
                pass

            links = [f"- [{s.removesuffix('.md')}]({s})" for s in siblings]
            stub = f"# {name}\n\n" + "\n".join(links) + "\n"
            old_index.write_text(stub, encoding="utf-8")
