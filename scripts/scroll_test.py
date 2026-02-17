"""Manual scroll test: multiple small scroll events to avoid acceleration issues."""

import time

import pyautogui

# Simulate config
INVERT = True  # Set to True if your scroll direction is reversed


def scroll_down(amount: int, x: int, y: int):
    """Scroll down using multiple small events."""
    click = 1 if INVERT else -1
    for _ in range(amount):
        pyautogui.scroll(click, x=x, y=y)
        time.sleep(0.05)


def scroll_up(amount: int, x: int, y: int):
    """Scroll up using multiple small events."""
    click = -1 if INVERT else 1
    for _ in range(amount):
        pyautogui.scroll(click, x=x, y=y)
        time.sleep(0.05)


def main():
    print(f"Scroll invert: {INVERT}")
    print("Move your mouse to the scrollable area in Chrome.")
    print("Starting in 5 seconds...")
    time.sleep(5)

    x, y = pyautogui.position()
    print(f"Mouse at: ({x}, {y})")

    for amount in [3, 5, 10]:
        print(f"\n--- scroll_down(amount={amount}) ---")
        scroll_down(amount, x, y)
        time.sleep(3)

    for amount in [3, 5, 10]:
        print(f"\n--- scroll_up(amount={amount}) ---")
        scroll_up(amount, x, y)
        time.sleep(3)

    print("\nDone. First 3 should be DOWN, last 3 should be UP.")


if __name__ == "__main__":
    main()
