"""Warm up macOS permission prompts for Apple app tools."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import subprocess
import sys
from typing import Sequence


@dataclass(frozen=True)
class WarmupTask:
    """One safe permission probe."""

    name: str
    app_name: str
    script_body: str


@dataclass(frozen=True)
class WarmupResult:
    """Permission probe result."""

    name: str
    app_name: str
    ok: bool
    detail: str


def _wrap_jxa(body: str) -> str:
    return f"""
function main() {{
{body}
}}
JSON.stringify(main());
"""


def _build_tasks() -> list[WarmupTask]:
    return [
        WarmupTask(
            name="calendar",
            app_name="Calendar",
            script_body="""
const app = Application("Calendar");
return { ok: true, count: app.calendars().length };
""",
        ),
        WarmupTask(
            name="reminders",
            app_name="Reminders",
            script_body="""
const app = Application("Reminders");
return { ok: true, count: app.lists().length };
""",
        ),
        WarmupTask(
            name="notes",
            app_name="Notes",
            script_body="""
const app = Application("Notes");
return { ok: true, count: app.accounts().length };
""",
        ),
        WarmupTask(
            name="photos",
            app_name="Photos",
            script_body="""
const app = Application("Photos");
return { ok: true, count: app.albums().length };
""",
        ),
        WarmupTask(
            name="mail",
            app_name="Mail",
            script_body="""
const app = Application("Mail");
return {
  ok: true,
  account_count: app.accounts().length,
  inbox_exists: app.inbox.exists(),
};
""",
        ),
    ]


def _run_task(task: WarmupTask, *, timeout: float) -> WarmupResult:
    try:
        completed = subprocess.run(
            ["osascript", "-l", "JavaScript"],
            input=_wrap_jxa(task.script_body),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return WarmupResult(
            name=task.name,
            app_name=task.app_name,
            ok=False,
            detail=f"timed out after {timeout:.1f}s",
        )

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        return WarmupResult(
            name=task.name,
            app_name=task.app_name,
            ok=False,
            detail=detail or "osascript failed",
        )

    output = completed.stdout.strip()
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        payload = {"raw": output}
    detail = ", ".join(f"{key}={value}" for key, value in sorted(payload.items()))
    return WarmupResult(
        name=task.name,
        app_name=task.app_name,
        ok=True,
        detail=detail or "ok",
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="permissions-warmup",
        description="Trigger safe macOS permission prompts for chat-agent Apple app tools.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Seconds to wait for each app permission probe. Default: 60.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List permission probes without running them.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    tasks = _build_tasks()

    if args.list:
        for task in tasks:
            print(f"{task.name}: {task.app_name}")
        return 0

    if sys.platform != "darwin":
        print("Error: permissions-warmup is only supported on macOS.", file=sys.stderr)
        return 2

    print("macOS permissions warmup")
    print("Approve the permission dialogs for the apps you want chat-agent to use.")
    print("This command only reads small metadata; it does not write, send, or delete.")
    print()

    results = [_run_task(task, timeout=args.timeout) for task in tasks]
    for result in results:
        status = "ok" if result.ok else "failed"
        print(f"{result.app_name}: {status} ({result.detail})")

    failed = [result for result in results if not result.ok]
    if failed:
        print()
        print("Some permissions did not complete.")
        print("Open System Settings > Privacy & Security and allow the app running this command.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
