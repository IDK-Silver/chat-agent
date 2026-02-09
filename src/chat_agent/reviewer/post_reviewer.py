"""Post-review: validates responder output against trigger rules."""

import json
import logging

from ..llm.base import LLMClient
from ..llm.schema import Message
from .flatten import flatten_for_review
from .json_extract import extract_json_object
from .review_packet import ReviewPacket, render_review_packet
from .schema import PostReviewResult

logger = logging.getLogger(__name__)

_DEFAULT_PARSE_RETRY_PROMPT = (
    "Your previous output was invalid.\n"
    "Return ONLY a JSON object matching keys: "
    "passed, violations, required_actions, retry_instruction, label_signals.\n"
    "Do not output tool calls, markdown fences, or prose."
)


class PostReviewer:
    """Reviews responder output for trigger rule compliance."""

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

    def review(
        self,
        messages: list[Message],
        *,
        review_packet: ReviewPacket | None = None,
    ) -> PostReviewResult | None:
        """Review responder output for rule violations.

        Returns None if review fails or parsing errors occur.
        """
        if review_packet is None:
            flat = flatten_for_review(messages)
        else:
            flat = [
                Message(
                    role="user",
                    content=(
                        "POST_REVIEW_PACKET_JSON\n"
                        + render_review_packet(review_packet)
                    ),
                )
            ]
        base_messages = [
            Message(role="system", content=self.system_prompt),
            *flat,
        ]
        review_messages = base_messages

        # Log message structure for debugging
        for i, m in enumerate(review_messages):
            content_len = len(m.content or "")
            logger.debug(
                "post-review msg[%d] role=%s len=%d: %s",
                i, m.role, content_len, (m.content or "")[:80],
            )

        try:
            for attempt in range(self.parse_retries + 1):
                raw = self.client.chat(review_messages)
                self.last_raw_response = raw
                is_final_attempt = attempt >= self.parse_retries
                result = self._parse_response(
                    raw,
                    final_attempt=is_final_attempt,
                )
                if result is not None:
                    return result

                if attempt < self.parse_retries:
                    review_messages = [
                        *base_messages,
                        Message(role="user", content=self.parse_retry_prompt),
                    ]
            return None
        except Exception as e:
            logger.warning("Post-review failed: %s", e)
            self.last_raw_response = None
            return None

    def _parse_response(
        self,
        raw: str,
        *,
        final_attempt: bool,
    ) -> PostReviewResult | None:
        """Parse JSON from LLM response, handling mixed reasoning output."""
        data = extract_json_object(raw)
        if data is None:
            log = logger.warning if final_attempt else logger.debug
            log("Failed to parse post-review response: %s", raw.strip()[:200])
            return None
        try:
            return PostReviewResult.model_validate(data)
        except ValueError:
            log = logger.warning if final_attempt else logger.debug
            log(
                "Invalid post-review schema: %s",
                json.dumps(data, ensure_ascii=False)[:200],
            )
            return None
