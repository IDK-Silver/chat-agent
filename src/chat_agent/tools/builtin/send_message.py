"""send_message tool: explicit outbound message delivery.

All outbound messages must go through this tool.  LLM text output
without calling this tool is treated as inner thoughts (console only).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
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
        "segments": ToolParameter(
            type="array",
            description=(
                "Ordered message segments. Each segment must provide "
                "'body' and can include per-segment attachments."
            ),
            items={
                "type": "object",
                "properties": {
                    "body": {"type": "string"},
                    "attachments": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["body"],
            },
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
        "reply_to_message": ToolParameter(
            type="string",
            description=(
                "Message ID to reply to (Discord only). "
                "Creates a reply reference to a specific message."
            ),
        ),
    },
    required=["channel", "segments"],
)


@dataclass(frozen=True)
class _SegmentPayload:
    body: str
    attachments: list[str]


def create_send_message(
    adapters: dict[str, ChannelAdapter],
    turn_context: TurnContext,
    contact_map: ContactMap,
    *,
    allowed_paths: list[str] | None = None,
    agent_os_dir: Path | None = None,
    shared_state_store: SharedStateStore | None = None,
    scope_resolver: ScopeResolver | None = None,
) -> Callable[..., str]:
    """Create a send_message function bound to adapters and turn context."""
    from ...agent.schema import OutboundMessage

    _allowed = allowed_paths or []
    _base_dir = agent_os_dir or Path(".")

    def _validate_segment_attachments(attachments: object) -> list[str] | None:
        if attachments is None:
            return []
        if not isinstance(attachments, list):
            return None
        validated_attachments: list[str] = []
        for path in attachments:
            if not isinstance(path, str):
                return None
            p = Path(path)
            if not p.is_file():
                return None
            if not is_path_allowed(path, _allowed, _base_dir):
                return None
            validated_attachments.append(str(p.resolve()))
        return validated_attachments

    def _parse_segments(segments: object) -> tuple[list[_SegmentPayload] | None, str | None]:
        if not isinstance(segments, list) or not segments:
            return None, "Error: 'segments' must be a non-empty list"

        parsed: list[_SegmentPayload] = []
        for idx, segment in enumerate(segments, start=1):
            if not isinstance(segment, dict):
                return None, f"Error: segments[{idx}] must be an object"
            body = segment.get("body")
            if not isinstance(body, str) or not body.strip():
                return None, f"Error: segments[{idx}].body must be a non-empty string"
            validated_attachments = _validate_segment_attachments(segment.get("attachments"))
            if validated_attachments is None:
                return None, (
                    f"Error: segments[{idx}].attachments must be a list of existing "
                    "allowed file paths"
                )
            parsed.append(
                _SegmentPayload(
                    body=body,
                    attachments=validated_attachments,
                )
            )
        return parsed, None

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
            # Do not inherit inbound message_id as reply target;
            # only set when agent explicitly provides reply_to_message.
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

        # Cross-channel without explicit recipient (e.g. send to cli)
        if channel == "gmail":
            return None, None, "Error: 'to' is required for Gmail messages outside reply mode"
        if channel == "discord":
            return None, None, "Error: 'to' is required for Discord messages outside reply mode"
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
        segments: list[dict[str, Any]],
        to: str | None = None,
        subject: str | None = None,
        reply_to_message: str | None = None,
        body: str | None = None,
        attachments: list[str] | None = None,
    ) -> str:
        adapter = adapters.get(channel)
        if adapter is None:
            return f"Error: unknown channel '{channel}'"

        if body is not None or attachments is not None:
            return (
                "Error: top-level 'body'/'attachments' are no longer supported. "
                "Use 'segments' with per-segment 'body' and optional 'attachments'."
            )

        parsed_segments, parse_error = _parse_segments(segments)
        if parse_error:
            return parse_error
        assert parsed_segments is not None

        metadata, recipient_display, route_error = _resolve_route(
            channel=channel,
            to=to,
            subject=subject,
            reply_to_message=reply_to_message,
        )
        if route_error:
            return route_error
        assert metadata is not None

        # Gmail is always delivered as a single message.
        if channel == "gmail":
            merged_body = "\n\n".join(seg.body for seg in parsed_segments)
            merged_attachments: list[str] = []
            for seg in parsed_segments:
                for path in seg.attachments:
                    if path not in merged_attachments:
                        merged_attachments.append(path)
            dedup_key = _build_dedup_key(
                channel,
                to,
                merged_body,
                merged_attachments,
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
                    body=merged_body,
                    attachments=merged_attachments,
                ),
            )

            if channel != "cli":
                try:
                    adapter.send(OutboundMessage(
                        channel=channel,
                        content=merged_body,
                        metadata=metadata,
                        attachments=merged_attachments,
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
                body=merged_body,
            )

            n_att = len(merged_attachments)
            logger.info(
                "send_message: channel=%s, to=%s, chars=%d, attachments=%d, segments=%d",
                channel, recipient_display, len(merged_body), n_att, len(parsed_segments),
            )
            target = f" ({recipient_display})" if recipient_display else ""
            att_info = f", {n_att} attachment(s)" if n_att else ""
            return f"OK: sent to {channel}{target}{att_info}"

        segment_hashes: list[str] = []
        hash_first_pos: dict[str, int] = {}
        for pos, seg in enumerate(parsed_segments, start=1):
            key = _build_dedup_key(
                channel,
                to,
                seg.body,
                seg.attachments,
                subject=subject,
                reply_to_message=reply_to_message,
            )
            h = _dedup_hash(key)
            if h in hash_first_pos:
                first = hash_first_pos[h]
                return (
                    f"Error: segments[{pos}] duplicates segments[{first}] in the same call. "
                    "Merge or remove duplicate segments."
                )
            hash_first_pos[h] = pos
            segment_hashes.append(h)

        pending_indices = [
            i for i, h in enumerate(segment_hashes)
            if h not in turn_context.sent_hashes
        ]
        skipped_already_sent = len(segment_hashes) - len(pending_indices)
        if not pending_indices:
            return (
                "Already sent. Do not call send_message again "
                "with the same content."
            )

        from ...agent.turn_context import PendingOutbound
        sent_count = 0
        sent_attachment_total = 0
        for idx in pending_indices:
            seg = parsed_segments[idx]
            segment_pos = idx + 1
            turn_context.pending_outbound.append(
                PendingOutbound(
                    channel=channel,
                    recipient=recipient_display,
                    body=seg.body,
                    attachments=seg.attachments,
                ),
            )

            if channel != "cli":
                try:
                    adapter.send(OutboundMessage(
                        channel=channel,
                        content=seg.body,
                        metadata=metadata,
                        attachments=seg.attachments,
                    ))
                except Exception:
                    logger.exception(
                        "send_message: adapter.send failed on %s at segment %d",
                        channel,
                        segment_pos,
                    )
                    return (
                        f"Error: failed to deliver message to {channel} at segments[{segment_pos}]. "
                        "Some earlier segments may already be sent; retrying the same segments "
                        "will continue with remaining unsent segments."
                    )

            turn_context.sent_hashes.add(segment_hashes[idx])
            _record_shared_state(
                channel=channel,
                to=to,
                metadata=metadata,
                recipient_display=recipient_display,
                body=seg.body,
            )
            sent_count += 1
            sent_attachment_total += len(seg.attachments)

        logger.info(
            "send_message: channel=%s, to=%s, segments=%d, attachments=%d, chars=%d",
            channel,
            recipient_display,
            sent_count,
            sent_attachment_total,
            sum(len(seg.body) for seg in parsed_segments),
        )
        target = f" ({recipient_display})" if recipient_display else ""
        att_info = f", {sent_attachment_total} attachment(s)" if sent_attachment_total else ""
        skipped_info = (
            f", skipped {skipped_already_sent} already-sent segment(s)"
            if skipped_already_sent > 0
            else ""
        )
        if sent_count == 1:
            return f"OK: sent to {channel}{target}{att_info}{skipped_info}"
        if sent_count > 1:
            return f"OK: sent {sent_count} messages to {channel}{target}{att_info}{skipped_info}"
        # Should not happen (pending_indices is non-empty), keep safe fallback.
        if skipped_already_sent > 0:
            return f"OK: sent to {channel}{target}{att_info}{skipped_info}"
        return "Error: no segments were sent"

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
