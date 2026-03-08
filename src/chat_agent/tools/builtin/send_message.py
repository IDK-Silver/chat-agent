"""send_message tool: explicit outbound message delivery.

All outbound messages must go through this tool.  LLM text output
without calling this tool is treated as inner thoughts (console only).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...llm.schema import ToolDefinition, ToolParameter
from ...tools.security import is_path_allowed

if TYPE_CHECKING:
    from ...agent.adapters.protocol import ChannelAdapter
    from ...agent.contact_map import ContactMap
    from ...agent.scope import ScopeResolver
    from ...agent.shared_state import SharedStateStore
    from ...agent.turn_context import TurnContext

logger = logging.getLogger(__name__)

SEND_MESSAGE_DEFINITION = ToolDefinition(
    name="send_message",
    description=(
        "Send a message to a channel. This is the ONLY way to deliver "
        "messages to users. Text output without this tool is inner "
        "thoughts visible only on the operator console. "
        "To send multiple messages, include all send_message calls in "
        "one response instead of splitting across rounds."
    ),
    parameters={
        "channel": ToolParameter(
            type="string",
            description=(
                "Target channel name (e.g. 'cli', 'gmail', 'discord')."
            ),
        ),
        "body": ToolParameter(
            type="string",
            description="Message text content.",
        ),
        "attachments": ToolParameter(
            type="array",
            description="File paths to attach to the message.",
            items={"type": "string"},
        ),
        "to": ToolParameter(
            type="string",
            description=(
                "Recipient person name. "
                "Omit ONLY when replying to the person who just "
                "messaged you on the same channel. Required when "
                "no one messaged you (scheduled wake-ups), sending "
                "to a different channel, or targeting a Discord guild "
                "channel (use '#channel @ guild' format)."
            ),
        ),
        "subject": ToolParameter(
            type="string",
            description=(
                "Email subject (Gmail only). "
                "Omit when replying to keep the original subject."
            ),
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
    shared_state_store: SharedStateStore | None = None,
    scope_resolver: ScopeResolver | None = None,
    pending_scope_check: Callable[[str], bool] | None = None,
) -> Callable[..., str]:
    """Create a send_message function bound to adapters and turn context."""
    from ...agent.schema import OutboundMessage

    _allowed = allowed_paths or []
    _base_dir = agent_os_dir or Path(".")

    def _validate_attachments(attachments: object) -> tuple[list[str] | None, str | None]:
        if attachments is None:
            return [], None
        if not isinstance(attachments, list):
            return None, "Error: 'attachments' must be a list of file paths"
        validated: list[str] = []
        for path in attachments:
            if not isinstance(path, str):
                return None, "Error: each attachment must be a file path string"
            p = Path(path)
            if not p.is_file():
                return None, f"Error: attachment not found: {path}"
            if not is_path_allowed(path, _allowed, _base_dir):
                return None, f"Error: attachment path not allowed: {path}"
            validated.append(str(p.resolve()))
        return validated, None

    def _resolve_route(
        *,
        channel: str,
        to: str | None,
        subject: str | None,
        reply_to_message: str | None,
    ) -> tuple[dict[str, Any] | None, str | None, str | None]:
        # Determine if this is a reply (same channel, no explicit recipient)
        is_reply = channel == turn_context.channel and to is None

        metadata: dict[str, Any] = {}
        recipient_display: str | None = None

        if is_reply:
            # Reply mode: inherit thread metadata from inbound
            metadata = dict(turn_context.metadata)
            # Gmail needs message_id (RFC 2822 Message-ID) for
            # In-Reply-To header; without it the recipient sees a
            # new thread instead of a reply.
            # Discord should NOT inherit it (would create an unwanted
            # visible reply reference on every response).
            if channel != "gmail":
                metadata.pop("message_id", None)
            recipient_display = turn_context.sender
            if subject is not None:
                metadata["subject"] = subject
            if reply_to_message is not None:
                metadata["message_id"] = reply_to_message
            return metadata, recipient_display, None

        if to is not None:
            # Explicit recipient: resolve via ContactMap
            identifier = contact_map.reverse_lookup(channel, to)
            if identifier is None:
                return None, None, (
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
                    dm_identifier = identifier
                    # Discord DM sending requires a numeric user ID. If ContactMap
                    # contains a chained alias (e.g. numeric_id -> username and
                    # username -> nickname), resolve one extra hop.
                    if not str(dm_identifier).isdigit():
                        second_hop = contact_map.reverse_lookup(channel, str(dm_identifier))
                        if second_hop is not None:
                            dm_identifier = second_hop
                    metadata["reply_to"] = dm_identifier
                if reply_to_message is not None:
                    metadata["message_id"] = reply_to_message
            return metadata, recipient_display, None

        # Cross-channel or no inbound sender — 'to' is required
        reason = (
            "no one messaged you this turn (scheduled wake-up)"
            if turn_context.sender is None
            else f"the message came from {turn_context.channel}/{turn_context.sender}, not {channel}"
        )
        if channel == "gmail":
            return None, None, (
                f"Error: 'to' is required for Gmail — {reason}. "
                "Specify a person name (e.g. to='...')."
            )
        if channel == "discord":
            return None, None, (
                f"Error: 'to' is required for Discord — {reason}. "
                "Specify a person name or '#channel @ guild'."
            )
        return {}, None, None

    def _record_shared_state(
        *,
        channel: str,
        to: str | None,
        metadata: dict[str, Any],
        recipient_display: str | None,
        body: str,
    ) -> None:
        if shared_state_store is None or scope_resolver is None:
            return
        try:
            scope_id = scope_resolver.outbound(
                channel=channel,
                to=to,
                metadata=metadata,
                inbound_channel=turn_context.channel,
                inbound_sender=turn_context.sender,
                inbound_metadata=turn_context.metadata,
            )
            if scope_id:
                shared_state_store.record_shared_outbound(
                    scope_id=scope_id,
                    channel=channel,
                    recipient=recipient_display,
                    body=body,
                )
                shared_state_store.save()
        except Exception:
            logger.warning("send_message: shared_state update failed", exc_info=True)

    def _dedup_hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def send_message(
        channel: str,
        body: str,
        attachments: list[str] | None = None,
        to: str | None = None,
        subject: str | None = None,
        reply_to_message: str | None = None,
        **kwargs: Any,
    ) -> str:
        adapter = adapters.get(channel)
        if adapter is None:
            return f"Error: unknown channel '{channel}'"

        if not isinstance(body, str) or not body.strip():
            return "Error: 'body' must be a non-empty string"

        validated_attachments, att_error = _validate_attachments(attachments)
        if att_error:
            return att_error
        assert validated_attachments is not None

        metadata, recipient_display, route_error = _resolve_route(
            channel=channel,
            to=to,
            subject=subject,
            reply_to_message=reply_to_message,
        )
        if route_error:
            return route_error
        assert metadata is not None

        outbound_scope_id = None
        if scope_resolver is not None:
            outbound_scope_id = scope_resolver.outbound(
                channel=channel,
                to=to,
                metadata=metadata,
                inbound_channel=turn_context.channel,
                inbound_sender=turn_context.sender,
                inbound_metadata=turn_context.metadata,
            )
        if (
            turn_context.channel == "system"
            and outbound_scope_id is not None
            and pending_scope_check is not None
            and pending_scope_check(outbound_scope_id)
        ):
            from ...agent.turn_context import ProactiveYieldState

            turn_context.proactive_yield = ProactiveYieldState(
                scope_id=outbound_scope_id,
            )
            return (
                "Error: yielded proactive send because a newer inbound is pending "
                f"for scope {outbound_scope_id}"
            )

        dedup_key = _build_dedup_key(
            channel,
            to,
            body,
            validated_attachments,
            subject=subject,
            reply_to_message=reply_to_message,
        )
        dedup_hash = _dedup_hash(dedup_key)
        if dedup_hash in turn_context.sent_hashes:
            return (
                "Already sent. Do not call send_message again "
                "with the same content."
            )

        from ...agent.turn_context import PendingOutbound
        turn_context.pending_outbound.append(
            PendingOutbound(
                channel=channel,
                recipient=recipient_display,
                body=body,
                attachments=validated_attachments,
            ),
        )

        if channel != "cli":
            try:
                adapter.send(OutboundMessage(
                    channel=channel,
                    content=body,
                    metadata=metadata,
                    attachments=validated_attachments,
                ))
            except Exception:
                logger.exception("send_message: adapter.send failed on %s", channel)
                return (
                    f"Error: failed to deliver message to {channel}. "
                    "The channel may be down or the token may have expired."
                )

        turn_context.sent_hashes.add(dedup_hash)
        _record_shared_state(
            channel=channel,
            to=to,
            metadata=metadata,
            recipient_display=recipient_display,
            body=body,
        )

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
