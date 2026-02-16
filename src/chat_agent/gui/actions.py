"""Desktop action primitives: screenshot, click, type, key press.

All coordinate conversion uses Gemini normalized coordinates (0-1000).
PyAutoGUI is lazy-imported so this module can be imported without a GUI.
"""

import base64
import io
import subprocess
import sys
import time

from ..llm.schema import ContentPart

# Gemini bounding box: [ymin, xmin, ymax, xmax], 0-1000 range.
GeminiBBox = list[int]


def bbox_to_center_pixels(
    bbox: GeminiBBox,
    screen_w: float,
    screen_h: float,
) -> tuple[float, float]:
    """Convert Gemini normalized bbox to pixel center point.

    Uses logical screen size (not Retina physical resolution).
    """
    ymin, xmin, ymax, xmax = bbox
    cx = (xmin + xmax) / 2 / 1000 * screen_w
    cy = (ymin + ymax) / 2 / 1000 * screen_h
    return cx, cy


def take_screenshot(
    *,
    max_width: int | None = None,
    quality: int = 80,
) -> ContentPart:
    """Take a screenshot and return as base64 JPEG ContentPart.

    Args:
        max_width: Resize proportionally if image is wider. None = no resize.
        quality: JPEG quality (1-100).
    """
    import pyautogui
    from PIL import Image

    img = pyautogui.screenshot()

    if max_width is not None and img.width > max_width:
        ratio = max_width / img.width
        new_h = int(img.height * ratio)
        img = img.resize((max_width, new_h), Image.LANCZOS)

    # JPEG requires RGB (no alpha channel)
    if img.mode != "RGB":
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return ContentPart(
        type="image",
        media_type="image/jpeg",
        data=b64,
        width=img.width,
        height=img.height,
    )


def click_at_bbox(bbox: GeminiBBox) -> str:
    """Click at the center of a Gemini bounding box."""
    import pyautogui

    screen_w, screen_h = pyautogui.size()
    cx, cy = bbox_to_center_pixels(bbox, screen_w, screen_h)
    pyautogui.click(cx, cy)
    return f"Clicked at pixel ({cx:.0f}, {cy:.0f})"


def right_click_at_bbox(bbox: GeminiBBox) -> str:
    """Right-click at the center of a Gemini bounding box."""
    import pyautogui

    screen_w, screen_h = pyautogui.size()
    cx, cy = bbox_to_center_pixels(bbox, screen_w, screen_h)
    pyautogui.click(cx, cy, button="right")
    return f"Right-clicked at pixel ({cx:.0f}, {cy:.0f})"


def type_text(text: str) -> str:
    """Type text via clipboard paste. Supports Unicode."""
    import pyautogui

    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    pyautogui.hotkey("command", "v")
    return f"Typed: {text!r}"


def capture_screenshot_to_temp(temp_path: str) -> str:
    """Capture the screen to a temporary file (does not touch clipboard)."""
    subprocess.run(["screencapture", "-x", temp_path], check=True)
    return "Screenshot captured to temp file."


def paste_screenshot_from_temp(temp_path: str) -> str:
    """Copy a previously captured screenshot from temp file to clipboard."""
    import os

    if not os.path.isfile(temp_path):
        return "Error: No screenshot captured yet. Call capture_screenshot first."
    subprocess.run([
        "osascript", "-e",
        f'set the clipboard to (read POSIX file "{temp_path}" as '
        '\u00ABclass PNGf\u00BB)',
    ], check=True)
    return "Screenshot copied to clipboard. Use Cmd+V to paste."


def activate_app(name: str) -> str:
    """Open or switch to an application by name.

    macOS: mdfind to locate .app bundles, then open (activates existing).
    Windows: AppActivate for running apps, Get-StartApps + explorer for launching.
    """
    if sys.platform == "darwin":
        return _activate_app_macos(name)
    if sys.platform == "win32":
        return _activate_app_windows(name)
    raise OSError(f"Unsupported platform: {sys.platform}")


def _activate_app_macos(name: str) -> str:
    safe = name.replace("'", "\\'")
    query = (
        "kMDItemContentType == com.apple.application-bundle && "
        f"kMDItemFSName == '*{safe}*'cd"
    )
    r = subprocess.run(
        ["mdfind", query],
        capture_output=True, text=True,
    )
    matches = [l for l in r.stdout.strip().splitlines() if l]
    if not matches:
        return f"No application matching '{name}' found."

    # Post-filter: prefer exact name match over substring
    name_lower = name.lower().removesuffix(".app")
    exact = [m for m in matches
             if m.rsplit("/", 1)[-1].removesuffix(".app").lower() == name_lower]
    if exact:
        matches = exact

    if len(matches) == 1:
        subprocess.run(["open", matches[0]], check=True)
        return f"Activated: {matches[0].rsplit('/', 1)[-1]}"
    names = [m.rsplit("/", 1)[-1] for m in matches]
    return f"Multiple matches: {', '.join(names)}"


def _activate_app_windows(name: str) -> str:
    import json as _json

    # Try to activate a running app by window title
    safe_name = name.replace('"', '`"')
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f'(New-Object -ComObject WScript.Shell).AppActivate("{safe_name}")'],
        capture_output=True, text=True,
    )
    if r.stdout.strip() == "True":
        return f"Activated: {name}"

    # Search Start Menu apps
    r2 = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f'Get-StartApps -Name "*{safe_name}*" | '
         'Select-Object Name, AppID | ConvertTo-Json -Compress'],
        capture_output=True, text=True,
    )
    try:
        data = _json.loads(r2.stdout)
    except (ValueError, _json.JSONDecodeError):
        return f"No application matching '{name}' found."

    if isinstance(data, dict):
        data = [data]
    if not data:
        return f"No application matching '{name}' found."
    if len(data) == 1:
        subprocess.run(
            ["explorer.exe", f"shell:AppsFolder\\{data[0]['AppID']}"],
        )
        return f"Activated: {data[0]['Name']}"
    names_list = [d["Name"] for d in data]
    return f"Multiple matches: {', '.join(names_list)}"


def get_active_app() -> str:
    """Return the name of the frontmost application.

    macOS: AppleScript via osascript.
    Windows: ctypes + tasklist to resolve foreground window PID.
    """
    if sys.platform == "darwin":
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of '
             'first application process whose frontmost is true'],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(
            hwnd, ctypes.byref(pid),
        )
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid.value}",
             "/FO", "CSV", "/NH"],
            capture_output=True, text=True,
        )
        line = result.stdout.strip()
        if line:
            return line.split(",")[0].strip('"').removesuffix(".exe")
        return f"unknown (PID:{pid.value})"
    raise OSError(f"Unsupported platform: {sys.platform}")


def wait(seconds: float) -> str:
    """Sleep for a given number of seconds."""
    seconds = min(max(seconds, 0.1), 10.0)
    time.sleep(seconds)
    return f"Waited {seconds:.1f}s"


def press_key(key: str) -> str:
    """Press a key or key combo (e.g. 'enter', 'command+a', 'tab')."""
    import pyautogui

    if "+" in key:
        keys = [k.strip() for k in key.split("+")]
        pyautogui.hotkey(*keys)
    else:
        pyautogui.press(key)
    return f"Pressed: {key}"


def maximize_window(app_name: str) -> str:
    """Maximize the frontmost window of the given application (macOS only)."""
    import pyautogui

    screen_w, screen_h = pyautogui.size()
    safe = app_name.replace('"', '\\"')
    script = (
        f'tell application "{safe}"\n'
        f"    activate\n"
        f"    delay 0.3\n"
        f"    set bounds of front window to {{0, 25, {screen_w}, {screen_h}}}\n"
        f"end tell"
    )
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return f"Error: {result.stderr.strip()}"
    return f"Maximized {app_name} to {screen_w}x{screen_h}"
