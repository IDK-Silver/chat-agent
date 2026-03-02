#!/usr/bin/env python3
"""
Nano Banana 2 (Gemini 3.1 Flash Image) API Tool
Usage:
  # 文字生圖
  python3 nano_banana.py "a cute cat wearing sunglasses"

  # 圖片編輯
  python3 nano_banana.py input.jpg "change the background to a beach"

  # 指定輸出檔名
  python3 nano_banana.py input.jpg "make it cartoon style" -o output.png

  # 指定 model
  python3 nano_banana.py "a dog" --model gemini-3-pro-image-preview
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

def _load_api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip("\"'")
    print("Error: GEMINI_API_KEY not found in env or .env", file=sys.stderr)
    sys.exit(1)

API_KEY = _load_api_key()
DEFAULT_MODEL = "gemini-3.1-flash-image-preview"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def call_api(model, parts):
    url = f"{API_BASE}/{model}:generateContent?key={API_KEY}"
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        "safetySettings": [
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
        ],
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP Error {e.code}: {body[:500]}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Nano Banana 2 API Tool")
    parser.add_argument("args", nargs="+", help="[image_path] prompt")
    parser.add_argument("-o", "--output", help="Output filename (default: auto)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model ID (default: {DEFAULT_MODEL})")
    parser.add_argument("--no-open", action="store_true", help="Don't auto-open the result")
    opts = parser.parse_args()

    # Parse: if first arg is a file, it's image edit mode
    if len(opts.args) >= 2 and os.path.isfile(opts.args[0]):
        image_path = opts.args[0]
        prompt = " ".join(opts.args[1:])
        mode = "edit"
    else:
        image_path = None
        prompt = " ".join(opts.args)
        mode = "generate"

    print(f"Mode: {mode}")
    print(f"Model: {opts.model}")
    print(f"Prompt: {prompt}")
    if image_path:
        print(f"Input: {image_path}")

    # Build parts
    parts = []
    if image_path:
        mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        parts.append({"inlineData": {"mimeType": mime, "data": img_b64}})
    parts.append({"text": prompt})

    print("Calling API...")
    data = call_api(opts.model, parts)

    if "error" in data:
        print(f"API Error: {json.dumps(data['error'], indent=2)}", file=sys.stderr)
        sys.exit(1)

    candidates = data.get("candidates", [{}])
    if not candidates or "content" not in candidates[0]:
        reason = candidates[0].get("finishReason", "UNKNOWN") if candidates else "NO_CANDIDATES"
        feedback = data.get("promptFeedback", {})
        print(f"Blocked! Reason: {reason}", file=sys.stderr)
        if feedback:
            print(f"Feedback: {json.dumps(feedback, indent=2)}", file=sys.stderr)
        sys.exit(1)

    parts_out = candidates[0]["content"].get("parts", [])
    saved = None
    for part in parts_out:
        if "text" in part:
            print(f"Response: {part['text']}")
        if "inlineData" in part:
            img_data = part["inlineData"]["data"]
            mime = part["inlineData"]["mimeType"]
            ext = "png" if "png" in mime else "jpg"

            if opts.output:
                saved = opts.output
            elif image_path:
                base = os.path.splitext(image_path)[0]
                saved = f"{base}_edited.{ext}"
            else:
                saved = f"output.{ext}"

            with open(saved, "wb") as f:
                f.write(base64.b64decode(img_data))
            size_kb = os.path.getsize(saved) / 1024
            print(f"Saved: {saved} ({size_kb:.0f} KB)")

    if saved and not opts.no_open:
        subprocess.run(["open", saved])


if __name__ == "__main__":
    main()
