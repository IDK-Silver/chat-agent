"""Post-review: validates responder output against trigger rules."""

import json
import logging

from ..llm.base import LLMClient
from ..llm.schema import Message
from .flatten import flatten_for_review
from .schema import PostReviewResult

logger = logging.getLogger(__name__)


class PostReviewer:
    """Reviews responder output for trigger rule compliance."""

    def __init__(self, client: LLMClient, system_prompt: str):
        self.client = client
        self.system_prompt = system_prompt
        self.last_raw_response: str | None = None

    def review(self, messages: list[Message]) -> PostReviewResult | None:
        """Review responder output for rule violations.

        Returns None if review fails or parsing errors occur.
        """
        flat = flatten_for_review(messages)
        review_messages = [
            Message(role="system", content=self.system_prompt),
            *flat,
        ]

        # Log message structure for debugging
        for i, m in enumerate(review_messages):
            content_len = len(m.content or "")
            logger.debug(
                "post-review msg[%d] role=%s len=%d: %s",
                i, m.role, content_len, (m.content or "")[:80],
            )

        try:
            raw = self.client.chat(review_messages)
            self.last_raw_response = raw
            return self._parse_response(raw)
        except Exception:
            logger.exception("Post-review failed")
            self.last_raw_response = None
            return None

    def _parse_response(self, raw: str) -> PostReviewResult | None:
        """Parse JSON from LLM response, handling markdown code blocks."""
        text = raw.strip()
        # Strip markdown code block if present
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
            return PostReviewResult.model_validate(data)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse post-review response: %s", text[:200])
            return None
