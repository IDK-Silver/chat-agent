"""GUI Worker: single-shot screenshot analysis using Flash LLM."""

import logging
import time
from typing import Any

from pydantic import BaseModel

from ..llm.base import LLMClient
from ..llm.schema import ContentPart, Message
from ..reviewer.json_extract import extract_json_object
from .actions import take_screenshot

logger = logging.getLogger(__name__)

_OBSERVATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "bbox": {
            "type": ["array", "null"],
            "items": {"type": "integer"},
            "minItems": 4,
            "maxItems": 4,
        },
        "found": {"type": "boolean"},
        "mismatch": {"type": ["string", "null"]},
        "obstructed": {"type": ["string", "null"]},
    },
    "required": ["description", "found"],
    "additionalProperties": False,
}


class WorkerObservation(BaseModel):
    """Structured result from a Worker observation."""

    description: str
    bbox: list[int] | None = None  # [ymin, xmin, ymax, xmax] or null
    found: bool = True
    mismatch: str | None = None
    obstructed: str | None = None
    screenshot_sec: float = 0.0
    inference_sec: float = 0.0


class GUIWorker:
    """Single-shot screenshot observer using a vision-capable Flash LLM.

    Each call to observe() is stateless: fresh system + user messages.
    """

    def __init__(
        self,
        client: LLMClient,
        system_prompt: str,
        parse_retries: int = 1,
        screenshot_max_width: int | None = None,
        screenshot_quality: int = 80,
    ):
        self.client = client
        self.system_prompt = system_prompt
        self.parse_retries = parse_retries
        self._screenshot_max_width = screenshot_max_width
        self._screenshot_quality = screenshot_quality

    def observe(self, instruction: str) -> WorkerObservation:
        """Take screenshot, send to LLM with instruction, return observation."""
        t0 = time.monotonic()
        screenshot = take_screenshot(
            max_width=self._screenshot_max_width,
            quality=self._screenshot_quality,
        )
        t1 = time.monotonic()
        user_content: list[ContentPart] = [
            screenshot,
            ContentPart(type="text", text=instruction),
        ]
        messages = [
            Message(role="system", content=self.system_prompt),
            Message(role="user", content=user_content),
        ]
        raw = self.client.chat(messages, response_schema=_OBSERVATION_SCHEMA)
        t2 = time.monotonic()
        obs = self._parse(raw)
        obs.screenshot_sec = t1 - t0
        obs.inference_sec = t2 - t1
        return obs

    def _parse(self, raw: str) -> WorkerObservation:
        """Parse LLM response into WorkerObservation with retries."""
        for attempt in range(self.parse_retries + 1):
            data = extract_json_object(raw)
            if data is not None:
                try:
                    return WorkerObservation.model_validate(data)
                except Exception:
                    pass
            if attempt < self.parse_retries:
                logger.debug("Worker parse retry %d: %s", attempt + 1, raw[:200])

        # Fallback: treat raw text as description
        logger.warning("Worker parse failed, using fallback: %s", raw[:200])
        return WorkerObservation(description=raw.strip(), found=False)
