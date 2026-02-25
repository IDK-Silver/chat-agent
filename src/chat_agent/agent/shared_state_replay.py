"""Best-effort shared-state cache rebuild from persisted sessions."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..session.schema import SessionEntry
from .scope import DEFAULT_SCOPE_RESOLVER, ScopeResolver
from .shared_state import SharedStateStore

logger = logging.getLogger(__name__)


@dataclass
class SharedStateReplayStats:
    sessions_scanned: int = 0
    entries_scanned: int = 0
    send_message_calls_seen: int = 0
    send_message_successes_replayed: int = 0
    errors: int = 0


@dataclass
class _PendingSend:
    args: dict[str, Any]
    inbound_channel: str | None
    inbound_sender: str | None
    inbound_metadata: dict[str, Any] | None


def rebuild_shared_state_from_sessions(
    sessions_dir: Path,
    *,
    store: SharedStateStore,
    scope_resolver: ScopeResolver = DEFAULT_SCOPE_RESOLVER,
) -> SharedStateReplayStats:
    """Replay successful send_message tool results into a SharedStateStore."""
    stats = SharedStateReplayStats()
    if not sessions_dir.exists():
        return stats

    for session_dir in sorted(p for p in sessions_dir.iterdir() if p.is_dir()):
        jsonl = session_dir / "messages.jsonl"
        if not jsonl.is_file():
            continue
        stats.sessions_scanned += 1
        try:
            _replay_session_file(jsonl, store=store, scope_resolver=scope_resolver, stats=stats)
        except Exception:
            stats.errors += 1
            logger.warning("shared_state replay failed for %s", jsonl, exc_info=True)
    return stats


def _replay_session_file(
    jsonl: Path,
    *,
    store: SharedStateStore,
    scope_resolver: ScopeResolver,
    stats: SharedStateReplayStats,
) -> None:
    pending: dict[str, _PendingSend] = {}
    current_inbound_channel: str | None = None
    current_inbound_sender: str | None = None
    current_inbound_metadata: dict[str, Any] | None = None

    for raw in jsonl.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            entry = SessionEntry.model_validate_json(line)
        except Exception:
            stats.errors += 1
            continue
        stats.entries_scanned += 1

        if entry.role == "user":
            current_inbound_channel = entry.channel
            current_inbound_sender = entry.sender
            current_inbound_metadata = dict(entry.metadata or {})
            pending.clear()
            continue

        if entry.role == "assistant" and entry.tool_calls:
            for tc in entry.tool_calls:
                if tc.name != "send_message":
                    continue
                stats.send_message_calls_seen += 1
                pending[tc.id] = _PendingSend(
                    args=dict(tc.arguments),
                    inbound_channel=current_inbound_channel,
                    inbound_sender=current_inbound_sender,
                    inbound_metadata=dict(current_inbound_metadata or {}),
                )
            continue

        if entry.role != "tool":
            continue
        if entry.name != "send_message":
            continue
        if entry.tool_call_id is None:
            continue
        pending_call = pending.pop(entry.tool_call_id, None)
        if pending_call is None:
            continue
        if not isinstance(entry.content, str):
            continue
        if not entry.content.startswith("OK: sent"):
            continue
        args = pending_call.args
        channel = str(args.get("channel") or "").strip()
        body = args.get("body")
        if not channel or not isinstance(body, str):
            continue
        scope_id = scope_resolver.outbound(
            channel=channel,
            to=args.get("to"),
            metadata=_reconstruct_send_metadata(channel, args, pending_call.inbound_metadata),
            inbound_channel=pending_call.inbound_channel,
            inbound_sender=pending_call.inbound_sender,
            inbound_metadata=pending_call.inbound_metadata,
        )
        if not scope_id:
            continue
        ts = entry.timestamp if isinstance(entry.timestamp, datetime) else datetime.now(timezone.utc)
        store.record_shared_outbound(
            scope_id=scope_id,
            channel=channel,
            recipient=args.get("to") if isinstance(args.get("to"), str) else pending_call.inbound_sender,
            body=body,
            ts=ts,
        )
        stats.send_message_successes_replayed += 1


def _reconstruct_send_metadata(
    channel: str,
    args: dict[str, Any],
    inbound_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Rebuild minimal outbound metadata needed for scope inference during replay."""
    inbound_meta = inbound_metadata or {}
    metadata: dict[str, Any] = {}
    to = args.get("to")

    # Reply mode: inherit inbound metadata when no explicit recipient.
    if not isinstance(to, str):
        metadata.update(inbound_meta)
        if "subject" in args:
            metadata["subject"] = args.get("subject")
        if "reply_to_message" in args and args.get("reply_to_message") is not None:
            metadata["message_id"] = args.get("reply_to_message")
        return metadata

    if channel == "gmail":
        # No thread_id in tool args; best-effort sender scope fallback.
        metadata["reply_to"] = to
        if "subject" in args:
            metadata["subject"] = args.get("subject")
    elif channel == "discord":
        if to.startswith("#"):
            metadata["channel_id"] = to
        else:
            metadata["reply_to"] = to
        if "reply_to_message" in args and args.get("reply_to_message") is not None:
            metadata["message_id"] = args.get("reply_to_message")
    elif channel == "line":
        metadata["reply_to"] = to
    return metadata

