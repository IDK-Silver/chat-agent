"""Click or screenshot with Gemini-style bounding box coordinates.

Usage:
  uv run python scripts/click_test.py <ymin> <xmin> <ymax> <xmax>
  uv run python scripts/click_test.py --screenshot <ymin> <xmin> <ymax> <xmax>

Coordinates are normalized 0-1000 (Gemini bounding box format).
--screenshot: take a screenshot and draw the bounding box instead of clicking.
"""

import sys
import time

import pyautogui
from PIL import ImageDraw, ImageFont


def parse_args():
    args = sys.argv[1:]
    screenshot = False
    if "--screenshot" in args:
        screenshot = True
        args.remove("--screenshot")

    if len(args) != 4:
        print(f"Usage: {sys.argv[0]} [--screenshot] <ymin> <xmin> <ymax> <xmax>")
        print("Coordinates: integers 0-1000 (Gemini normalized format)")
        print("--screenshot: save annotated screenshot instead of clicking")
        sys.exit(1)

    coords = tuple(int(a) for a in args)
    return screenshot, coords


def to_pixels(ymin, xmin, ymax, xmax, screen_w, screen_h):
    px_xmin = xmin / 1000 * screen_w
    px_ymin = ymin / 1000 * screen_h
    px_xmax = xmax / 1000 * screen_w
    px_ymax = ymax / 1000 * screen_h
    cx = (px_xmin + px_xmax) / 2
    cy = (px_ymin + px_ymax) / 2
    return px_xmin, px_ymin, px_xmax, px_ymax, cx, cy


def main():
    screenshot_mode, (ymin, xmin, ymax, xmax) = parse_args()

    screen_w, screen_h = pyautogui.size()
    px_xmin, px_ymin, px_xmax, px_ymax, cx, cy = to_pixels(
        ymin, xmin, ymax, xmax, screen_w, screen_h
    )

    print(f"Screen: {screen_w}x{screen_h}")
    print(f"Box: ymin={ymin} xmin={xmin} ymax={ymax} xmax={xmax}")
    print(f"Pixel box: ({px_xmin:.0f}, {px_ymin:.0f}) - ({px_xmax:.0f}, {px_ymax:.0f})")
    print(f"Center: ({cx:.0f}, {cy:.0f})")

    if screenshot_mode:
        img = pyautogui.screenshot()
        # Use actual image size (Retina physical resolution) for drawing
        img_w, img_h = img.size
        ix_xmin, iy_ymin, ix_xmax, iy_ymax, icx, icy = to_pixels(
            ymin, xmin, ymax, xmax, img_w, img_h
        )
        print(f"Screenshot: {img_w}x{img_h}")

        draw = ImageDraw.Draw(img)

        # Draw bounding box
        draw.rectangle([ix_xmin, iy_ymin, ix_xmax, iy_ymax], outline="red", width=4)

        # Draw crosshair at center
        size = 20
        draw.line([(icx - size, icy), (icx + size, icy)], fill="red", width=3)
        draw.line([(icx, icy - size), (icx, icy + size)], fill="red", width=3)

        # Label with coordinates
        label = f"[{ymin},{xmin},{ymax},{xmax}] -> ({cx:.0f},{cy:.0f})"
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 24)
        except OSError:
            font = ImageFont.load_default()
        text_y = max(iy_ymin - 32, 0)
        draw.text((ix_xmin, text_y), label, fill="red", font=font)

        out = "screenshot_annotated.png"
        img.save(out)
        print(f"Saved: {out}")
    else:
        print("Clicking in 3 seconds...")
        time.sleep(3)
        pyautogui.click(cx, cy)
        print("Done.")


if __name__ == "__main__":
    main()
