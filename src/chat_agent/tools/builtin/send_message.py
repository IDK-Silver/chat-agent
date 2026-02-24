"""send_message tool: explicit outbound message delivery.

All outbound messages must go through this tool.  LLM text output
without calling this tool is treated as inner thoughts (console only).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...llm.schema import ToolDefinition, ToolParameter
from ...tools.security import is_path_allowed

if TYPE_CHECKING:
    from ...agent.adapters.protocol import ChannelAdapter
    from ...agent.contact_map import ContactMap
    from ...agent.turn_context import TurnContext

logger = logging.getLogger(__name__)

SEND_MESSAGE_DEFINITION = ToolDefinition(
    name="send_message",
    description=(
        "Send a message to a channel. This is the ONLY way to deliver "
        "messages to users. Text output without this tool is inner "
        "thoughts visible only on the operator console."
    ),
    parameters={
        "channel": ToolParameter(
            type="string",
            description=(
                "Target channel name (e.g. 'cli', 'gmail'). "
                "Must match a registered adapter."
            ),
        ),
        "body": ToolParameter(
            type="string",
            description="Message content to send.",
        ),
        "to": ToolParameter(
            type="string",
            description=(
                "Recipient person name (resolved via ContactMap). "
                "Omit to reply to the current sender on the same channel."
            ),
        ),
        "subject": ToolParameter(
            type="string",
            description=(
                "Email subject (Gmail only). "
                "Omit in reply mode to keep the original subject."
            ),
        ),
        "attachments": ToolParameter(
            type="array",
            description=(
                "List of absolute file paths to attach. "
                "Files must exist and be within allowed directories."
            ),
            items={"type": "string"},
        ),
        "reply_to_message": ToolParameter(
            type="string",
            description=(
                "Message ID to reply to (Discord only). "
                "Creates a reply reference to a specific message."
            ),
        ),
    },
    required=["channel", "body"],
)


def create_send_message(
    adapters: dict[str, ChannelAdapter],
    turn_context: TurnContext,
    contact_map: ContactMap,
    *,
    allowed_paths: list[str] | None = None,
    agent_os_dir: Path | None = None,
) -> Callable[..., str]:
    """Create a send_message function bound to adapters and turn context."""
    from ...agent.schema import OutboundMessage

    _allowed = allowed_paths or []
    _base_dir = agent_os_dir or Path(".")

    def send_message(
        channel: str,
        body: str,
        to: str | None = None,
        subject: str | None = None,
        attachments: list[str] | None = None,
        reply_to_message: str | None = None,
    ) -> str:
        adapter = adapters.get(channel)
        if adapter is None:
            return f"Error: unknown channel '{channel}'"

        if not body.strip():
            return "Error: body must not be empty"

        # Validate attachments (channel-agnostic security check)
        validated_attachments: list[str] = []
        for path in attachments or []:
            p = Path(path)
            if not p.is_file():
                return f"Error: attachment not found: {path}"
            if not is_path_allowed(path, _allowed, _base_dir):
                return f"Error: attachment path not allowed: {path}"
            validated_attachments.append(str(p.resolve()))

        # Dedup: prevent identical send_message in the same turn
        dedup_key = _build_dedup_key(
            channel,
            to,
            body,
            validated_attachments,
            subject=subject,
            reply_to_message=reply_to_message,
        )
        if turn_context.check_sent_dedup_raw(dedup_key):
            return (
                "Already sent. Do not call send_message again "
                "with the same content."
            )

        # Determine if this is a reply (same channel, no explicit recipient)
        is_reply = channel == turn_context.channel and to is None

        metadata: dict[str, Any] = {}
        recipient_display: str | None = None

        if is_reply:
            # Reply mode: inherit thread metadata from inbound
            metadata = dict(turn_context.metadata)
            recipient_display = turn_context.sender
            if subject is not None:
                metadata["subject"] = subject
            if reply_to_message is not None:
                metadata["message_id"] = reply_to_message
        elif to is not None:
            # Explicit recipient: resolve via ContactMap
            identifier = contact_map.reverse_lookup(channel, to)
            if identifier is None:
                return (
                    f"Error: no {channel} address found for '{to}' "
                    f"in contact map. Use update_contact_mapping first."
                )
            recipient_display = to
            if channel == "gmail":
                metadata["reply_to"] = identifier
                metadata["subject"] = subject  # None = adapter decides (thread continuation)
            elif channel == "discord":
                if to.startswith("#"):
                    metadata["channel_id"] = identifier
                else:
                    metadata["reply_to"] = identifier
                if reply_to_message is not None:
                    metadata["message_id"] = reply_to_message
        else:
            # Cross-channel without explicit recipient (e.g. send to cli)
            if channel == "gmail":
                return "Error: 'to' is required for Gmail messages outside reply mode"
            if channel == "discord":
                return "Error: 'to' is required for Discord messages outside reply mode"
            recipient_display = None

        # Buffer for deferred display (shown after inner thoughts)
        from ...agent.turn_context import PendingOutbound
        turn_context.pending_outbound.append(
            PendingOutbound(
                channel=channel,
                recipient=recipient_display,
                body=body,
                attachments=validated_attachments,
            ),
        )

        # Deliver via adapter (CLI adapter.send is no-op; display above)
        if channel != "cli":
            adapter.send(OutboundMessage(
                channel=channel,
                content=body,
                metadata=metadata,
                attachments=validated_attachments,
            ))

        n_att = len(validated_attachments)
        logger.info(
            "send_message: channel=%s, to=%s, chars=%d, attachments=%d",
            channel, recipient_display, len(body), n_att,
        )
        target = f" ({recipient_display})" if recipient_display else ""
        att_info = f", {n_att} attachment(s)" if n_att else ""
        return f"OK: sent to {channel}{target}{att_info}"

    return send_message


def _build_dedup_key(
    channel: str,
    to: str | None,
    body: str,
    attachments: list[str],
    *,
    subject: str | None = None,
    reply_to_message: str | None = None,
) -> str:
    """Build a dedup key string including attachments."""
    parts = [channel, to or "", body, subject or "", reply_to_message or ""]
    if attachments:
        parts.extend(sorted(attachments))
    return "\0".join(parts)
