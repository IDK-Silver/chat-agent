"""Progress-review: monitors visible text chunks before user display."""

import json
import logging
from typing import Any

from ..llm.base import LLMClient
from ..llm.content import content_to_text
from ..llm.schema import Message
from .json_extract import extract_json_object
from .progress_schema import ProgressReviewResult

logger = logging.getLogger(__name__)

_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "violations": {"type": "array", "items": {"type": "string"}},
        "block_instruction": {"type": "string"},
    },
    "required": ["passed", "violations"],
    "additionalProperties": False,
}

_DEFAULT_PARSE_RETRY_PROMPT = (
    "Your previous output was invalid.\n"
    "Return ONLY a JSON object matching keys: "
    "passed, violations, block_instruction.\n"
    "Do not output markdown fences, tool calls, or prose."
)


def _latest_user_text(messages: list[Message]) -> str:
    """Return latest user text from the provided message list."""
    for message in reversed(messages):
        if message.role != "user" or message.content is None:
            continue
        text = content_to_text(message.content).strip()
        if text:
            return text
    return ""


class ProgressReviewer:
    """Reviews intermediate assistant text for advisory safety/compliance signals."""

    def __init__(
        self,
        client: LLMClient,
        system_prompt: str,
        parse_retries: int = 1,
        parse_retry_prompt: str | None = None,
    ):
        self.client = client
        self.system_prompt = system_prompt
        self.parse_retries = max(0, parse_retries)
        self.parse_retry_prompt = parse_retry_prompt or _DEFAULT_PARSE_RETRY_PROMPT
        self.last_raw_response: str | None = None
        self.last_error: str | None = None

    def review(
        self,
        messages: list[Message],
        *,
        candidate_reply: str,
    ) -> ProgressReviewResult | None:
        """Review one candidate visible text chunk.

        Returns None if review/parsing fails.
        """
        packet = {
            "latest_user_turn": _latest_user_text(messages),
            "candidate_assistant_reply": candidate_reply,
        }
        base_messages = [
            Message(role="system", content=self.system_prompt),
            Message(
                role="user",
                content=(
                    "PROGRESS_REVIEW_PACKET_JSON\n"
                    + json.dumps(packet, ensure_ascii=False, indent=2)
                ),
            ),
        ]
        review_messages = base_messages

        try:
            for attempt in range(self.parse_retries + 1):
                raw = self.client.chat(review_messages, response_schema=_REVIEW_SCHEMA)
                self.last_raw_response = raw
                is_final_attempt = attempt >= self.parse_retries
                result = self._parse_response(raw, final_attempt=is_final_attempt)
                if result is not None:
                    return result
                if attempt < self.parse_retries:
                    review_messages = [
                        *base_messages,
                        Message(role="user", content=self.parse_retry_prompt),
                    ]
            return None
        except Exception as e:
            logger.warning("Progress-review failed: %s", e)
            self.last_raw_response = None
            self.last_error = str(e)
            return None

    def _parse_response(
        self,
        raw: str,
        *,
        final_attempt: bool,
    ) -> ProgressReviewResult | None:
        """Parse JSON from LLM response, handling mixed reasoning output."""
        data = extract_json_object(raw)
        if data is None:
            log = logger.warning if final_attempt else logger.debug
            log("Failed to parse progress-review response: %s", raw.strip()[:200])
            return None
        try:
            return ProgressReviewResult.model_validate(data)
        except ValueError:
            log = logger.warning if final_attempt else logger.debug
            log(
                "Invalid progress-review schema: %s",
                json.dumps(data, ensure_ascii=False)[:200],
            )
            return None
