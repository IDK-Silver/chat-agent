"""Desktop action primitives: screenshot, click, type, key press.

All coordinate conversion uses Gemini normalized coordinates (0-1000).
PyAutoGUI is lazy-imported so this module can be imported without a GUI.
"""

import base64
import io
import subprocess

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


def take_screenshot() -> ContentPart:
    """Take a screenshot and return as base64 PNG ContentPart."""
    import pyautogui

    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return ContentPart(
        type="image",
        media_type="image/png",
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


def type_text(text: str) -> str:
    """Type text. ASCII uses typewrite; non-ASCII uses pbcopy + Cmd+V."""
    import pyautogui

    if all(ord(c) < 128 for c in text):
        pyautogui.typewrite(text, interval=0.02)
    else:
        # macOS clipboard paste for Unicode
        subprocess.run(
            ["pbcopy"],
            input=text.encode("utf-8"),
            check=True,
        )
        pyautogui.hotkey("command", "v")
    return f"Typed: {text!r}"


def press_key(key: str) -> str:
    """Press a key or key combo (e.g. 'enter', 'command+a', 'tab')."""
    import pyautogui

    if "+" in key:
        keys = [k.strip() for k in key.split("+")]
        pyautogui.hotkey(*keys)
    else:
        pyautogui.press(key)
    return f"Pressed: {key}"
