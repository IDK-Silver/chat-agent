"""In-memory metrics cache with incremental JSONL reading."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .pricing import ModelPricing, compute_request_cost, get_pricing_metadata
from .session_reader import (
    FileReadState,
    SessionFiles,
    discover_sessions,
    parse_response_record,
    parse_turn_record,
    read_request_record,
    read_meta,
    read_new_lines,
)

logger = logging.getLogger(__name__)

_READ_CACHE_MEASURABLE_PROVIDERS = frozenset(
    {
        "anthropic",
        "claude_code",
        "codex",
        "copilot",
        "deepseek",
        "openai",
        "openrouter",
    }
)
_WRITE_CACHE_MEASURABLE_PROVIDERS = frozenset({"anthropic", "claude_code", "openrouter"})


def _compute_read_cache_rate(
    prompt_tokens: int,
    cache_read_tokens: int,
    *,
    measurable: bool,
) -> float | None:
    if not measurable or prompt_tokens <= 0:
        return None
    return cache_read_tokens / prompt_tokens


def _is_read_cache_measurable(provider: str | None) -> bool:
    return provider in _READ_CACHE_MEASURABLE_PROVIDERS


def _is_write_cache_measurable(provider: str | None) -> bool:
    return provider in _WRITE_CACHE_MEASURABLE_PROVIDERS


def _aggregate_read_cache_measurable(
    rows: list[ResponseMetrics],
) -> bool:
    usage_rows = _usage_rows(rows)
    return bool(usage_rows) and all(
        _is_read_cache_measurable(row.provider) for row in usage_rows
    )


def _aggregate_write_cache_measurable(
    rows: list[ResponseMetrics],
) -> bool:
    usage_rows = _usage_rows(rows)
    return bool(usage_rows) and all(
        _is_write_cache_measurable(row.provider) for row in usage_rows
    )


def _aggregate_pricing_sources(rows: list[ResponseMetrics]) -> list[dict]:
    counts: dict[tuple[str, str | None, bool], int] = {}
    for row in rows:
        if row.pricing_source is None:
            continue
        key = (row.pricing_source, row.pricing_source_url, row.pricing_stale)
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "source": source,
            "source_url": source_url,
            "stale": stale,
            "count": count,
        }
        for (source, source_url, stale), count in sorted(counts.items())
    ]


def _has_stale_pricing(rows: list[ResponseMetrics]) -> bool:
    return any(row.pricing_stale for row in rows)


def _usage_rows(rows: list["ResponseMetrics"]) -> list["ResponseMetrics"]:
    return [row for row in rows if row.usage_available and row.prompt_tokens > 0]


def _count_images(raw_messages: list[Any]) -> int:
    count = 0
    for message in raw_messages:
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
        if not isinstance(content, list):
            continue
        for part in content:
            part_type = (
                part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
            )
            if part_type == "image":
                count += 1
    return count


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _request_metrics_from_raw(raw: dict[str, Any]) -> "RequestMetrics" | None:
    try:
        messages = raw.get("messages") or []
        tools = raw.get("tools") or []
        image_count = _count_images(messages)
        return RequestMetrics(
            ts=_parse_ts(raw["ts"]),
            request_id=raw["request_id"],
            turn_id=raw.get("turn_id"),
            round=raw.get("round"),
            client_label=raw["client_label"],
            provider=raw.get("provider"),
            model=raw.get("model"),
            call_type=raw["call_type"],
            message_count=len(messages),
            tool_count=len(tools),
            image_count=image_count,
            has_response_schema=raw.get("response_schema") is not None,
            temperature=raw.get("temperature"),
        )
    except Exception:
        logger.warning("Failed to parse request metadata: %s", raw.get("request_id"))
        return None


def _read_new_request_metrics(path: Path, state: FileReadState) -> list["RequestMetrics"]:
    """Read request metadata without keeping full request payloads in cache."""
    if not path.exists():
        return []
    size = path.stat().st_size
    if size <= state.byte_offset:
        return []

    results: list[RequestMetrics] = []
    with open(path, "r", encoding="utf-8") as fh:
        fh.seek(state.byte_offset)
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed JSON line in %s", path)
                continue
            metrics = _request_metrics_from_raw(raw)
            if metrics is not None:
                results.append(metrics)
        state.byte_offset = fh.tell()
    return results


@dataclass
class RequestMetrics:
    ts: datetime
    request_id: str
    turn_id: str | None
    round: int | None
    client_label: str
    provider: str | None
    model: str | None
    call_type: str
    message_count: int
    tool_count: int
    image_count: int
    has_response_schema: bool
    temperature: float | None


@dataclass
class ResponseMetrics:
    ts: datetime
    round: int | None
    provider: str | None
    model: str | None
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_ms: int
    cost: float | None
    turn_id: str | None
    request_id: str | None = None
    client_label: str | None = None
    call_type: str | None = None
    usage_available: bool = True
    response_text: str | None = None
    error: str | None = None
    pricing_source: str | None = None
    pricing_source_url: str | None = None
    pricing_stale: bool = False


@dataclass
class TurnMetrics:
    turn_id: str
    ts_started: datetime
    ts_finished: datetime
    channel: str
    sender: str | None
    status: str
    llm_rounds: int
    max_prompt_tokens: int | None
    total_prompt_tokens: int
    read_cache_rate: float | None
    cache_read_tokens: int
    cache_write_tokens: int
    write_cache_measurable: bool
    total_cost: float | None
    responses: list[ResponseMetrics] = field(default_factory=list)


@dataclass
class SessionSummary:
    session_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    turn_count: int
    total_cost: float | None
    total_prompt_tokens: int
    read_cache_rate: float | None
    total_cache_read: int
    total_cache_write: int
    cache_hit_rate: float | None
    write_cache_measurable: bool
    peak_prompt_tokens: int
    pricing_sources: list[dict]
    pricing_stale: bool


@dataclass
class DashboardSummary:
    date_from: date
    date_to: date
    total_cost: float
    total_turns: int
    total_sessions: int
    total_prompt_tokens: int
    read_cache_rate: float | None
    total_cache_read: int
    total_cache_write: int
    cache_hit_rate: float | None
    write_cache_measurable: bool
    daily_costs: list[dict]  # [{date, cost, turns}]
    pricing_sources: list[dict]
    pricing_stale: bool


class MetricsCache:
    """Central in-memory cache refreshed incrementally from JSONL files."""

    def __init__(self, sessions_dir: Path, pricing: dict[str, ModelPricing]) -> None:
        self._sessions_dir = sessions_dir
        self.pricing = pricing
        self._files: dict[str, SessionFiles] = {}
        self._requests: dict[str, list[RequestMetrics]] = {}
        self._turns: dict[str, list[TurnMetrics]] = {}
        # responses indexed by (session_id, turn_id) for linking
        self._responses: dict[str, list[ResponseMetrics]] = {}

    def refresh_all(self) -> set[str]:
        """Discover new sessions and refresh all. Returns changed session IDs."""
        changed: set[str] = set()
        for sid in discover_sessions(self._sessions_dir):
            if self.refresh_session(sid):
                changed.add(sid)
        return changed

    def refresh_session(self, session_id: str) -> bool:
        """Read new data for one session. Returns True if anything changed."""
        session_dir = self._sessions_dir / session_id
        if not session_dir.is_dir():
            return False

        sf = self._files.get(session_id)
        if sf is None:
            sf = SessionFiles(session_dir=session_dir)
            self._files[session_id] = sf

        changed = False

        # Refresh meta if needed
        meta_path = session_dir / "meta.json"
        if meta_path.exists():
            mtime = meta_path.stat().st_mtime
            if mtime != sf.meta_mtime:
                sf.meta = read_meta(session_dir)
                sf.meta_mtime = mtime
                changed = True

        # Read new requests. Keep only lightweight metadata in memory; detail
        # endpoints read requests.jsonl lazily so large prompts/images stay cold.
        requests_path = session_dir / "requests.jsonl"
        new_requests = _read_new_request_metrics(requests_path, sf.requests_state)
        if new_requests:
            changed = True
            if session_id not in self._requests:
                self._requests[session_id] = []
            self._requests[session_id].extend(new_requests)

        # Read new turns
        turns_path = session_dir / "turns.jsonl"
        new_turn_lines = read_new_lines(turns_path, sf.turns_state)
        if new_turn_lines:
            changed = True
            if session_id not in self._turns:
                self._turns[session_id] = []
            for raw in new_turn_lines:
                rec = parse_turn_record(raw)
                if rec is None:
                    continue
                tm = TurnMetrics(
                    turn_id=rec.turn_id,
                    ts_started=rec.ts_started,
                    ts_finished=rec.ts_finished,
                    channel=rec.channel,
                    sender=rec.sender,
                    status=rec.status,
                    llm_rounds=rec.llm_rounds,
                    max_prompt_tokens=rec.max_prompt_tokens,
                    total_prompt_tokens=0,
                    read_cache_rate=None,
                    cache_read_tokens=rec.cache_read_tokens,
                    cache_write_tokens=rec.cache_write_tokens,
                    write_cache_measurable=False,
                    total_cost=None,
                )
                self._turns[session_id].append(tm)

        # Read new responses
        resp_path = session_dir / "responses.jsonl"
        new_resp_lines = read_new_lines(resp_path, sf.responses_state)
        if new_resp_lines:
            changed = True
            if session_id not in self._responses:
                self._responses[session_id] = []
            for raw in new_resp_lines:
                rec = parse_response_record(raw)
                if rec is None:
                    continue
                resp = rec.response
                pricing_meta = get_pricing_metadata(
                    rec.provider,
                    rec.model,
                    self.pricing,
                )
                usage_available = bool(resp and resp.usage_available)
                prompt_tokens = (resp.prompt_tokens or 0) if resp else 0
                completion_tokens = (resp.completion_tokens or 0) if resp else 0
                cache_read_tokens = resp.cache_read_tokens if resp else 0
                cache_write_tokens = resp.cache_write_tokens if resp else 0
                cost = None
                if resp is not None and usage_available:
                    cost = compute_request_cost(
                        provider=rec.provider,
                        model=rec.model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        cache_read_tokens=cache_read_tokens,
                        cache_write_tokens=cache_write_tokens,
                        pricing=self.pricing,
                    )
                rm = ResponseMetrics(
                    ts=rec.ts,
                    request_id=rec.request_id,
                    round=rec.round,
                    provider=rec.provider,
                    model=rec.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                    latency_ms=rec.latency_ms,
                    cost=cost,
                    turn_id=rec.turn_id,
                    client_label=rec.client_label,
                    call_type=rec.call_type,
                    usage_available=usage_available,
                    response_text=rec.response_text,
                    error=rec.error,
                    pricing_source=pricing_meta.source if pricing_meta else None,
                    pricing_source_url=(
                        pricing_meta.source_url if pricing_meta else None
                    ),
                    pricing_stale=pricing_meta.stale if pricing_meta else False,
                )
                self._responses[session_id].append(rm)

        # Link responses to turns and compute turn costs
        if changed and session_id in self._turns:
            resp_by_turn: dict[str, list[ResponseMetrics]] = {}
            for rm in self._responses.get(session_id, []):
                if rm.turn_id:
                    resp_by_turn.setdefault(rm.turn_id, []).append(rm)
            for tm in self._turns[session_id]:
                linked = resp_by_turn.get(tm.turn_id, [])
                tm.responses = linked
                costs = [r.cost for r in linked if r.cost is not None]
                tm.total_cost = sum(costs) if costs else None
                tm.total_prompt_tokens = sum(r.prompt_tokens for r in linked)
                tm.read_cache_rate = _compute_read_cache_rate(
                    tm.total_prompt_tokens,
                    tm.cache_read_tokens,
                    measurable=_aggregate_read_cache_measurable(linked),
                )
                tm.write_cache_measurable = _aggregate_write_cache_measurable(linked)

        return changed

    def get_session_summary(self, session_id: str) -> SessionSummary | None:
        sf = self._files.get(session_id)
        if sf is None or sf.meta is None:
            return None
        turns = self._turns.get(session_id, [])
        responses = self._responses.get(session_id, [])
        total_cr = sum(r.cache_read_tokens for r in responses)
        total_cw = sum(r.cache_write_tokens for r in responses)
        total_prompt = sum(r.prompt_tokens for r in responses)
        costs = [t.total_cost for t in turns if t.total_cost is not None]
        peak = max(
            [t.max_prompt_tokens or 0 for t in turns]
            + [r.prompt_tokens for r in responses],
            default=0,
        )
        hit_rate = total_cr / (total_cr + total_cw) if (total_cr + total_cw) > 0 else None
        read_cache_measurable = _aggregate_read_cache_measurable(responses)
        return SessionSummary(
            session_id=session_id,
            status=sf.meta.status,
            created_at=sf.meta.created_at,
            updated_at=sf.meta.updated_at,
            turn_count=len(turns),
            total_cost=sum(costs) if costs else None,
            total_prompt_tokens=total_prompt,
            read_cache_rate=_compute_read_cache_rate(
                total_prompt,
                total_cr,
                measurable=read_cache_measurable,
            ),
            total_cache_read=total_cr,
            total_cache_write=total_cw,
            cache_hit_rate=hit_rate,
            write_cache_measurable=_aggregate_write_cache_measurable(responses),
            peak_prompt_tokens=peak,
            pricing_sources=_aggregate_pricing_sources(responses),
            pricing_stale=_has_stale_pricing(responses),
        )

    def get_sessions_in_range(
        self, date_from: date, date_to: date
    ) -> list[SessionSummary]:
        results: list[SessionSummary] = []
        for sid, sf in self._files.items():
            if sf.meta is None:
                continue
            created = sf.meta.created_at.date()
            if created < date_from or created > date_to:
                continue
            summary = self.get_session_summary(sid)
            if summary:
                results.append(summary)
        results.sort(key=lambda s: s.created_at, reverse=True)
        return results

    def get_dashboard(self, date_from: date, date_to: date) -> DashboardSummary:
        sessions = self.get_sessions_in_range(date_from, date_to)
        all_responses: list[ResponseMetrics] = []
        total_cost = 0.0
        total_turns = 0
        total_prompt = 0
        total_cr = 0
        total_cw = 0
        daily: dict[date, dict] = {}

        for s in sessions:
            session_responses = self._responses.get(s.session_id, [])
            all_responses.extend(session_responses)
            if s.total_cost is not None:
                total_cost += s.total_cost
            total_turns += s.turn_count
            total_prompt += s.total_prompt_tokens
            total_cr += s.total_cache_read
            total_cw += s.total_cache_write
            # Daily aggregation by session created date
            d = s.created_at.date()
            if d not in daily:
                daily[d] = {
                    "date": d.isoformat(),
                    "cost": 0.0,
                    "turns": 0,
                    "prompt_tokens": 0,
                    "cache_read": 0,
                    "cache_write": 0,
                    "read_cache_measurable": True,
                    "write_cache_measurable": True,
                    "pricing_stale": False,
                }
            if s.total_cost is not None:
                daily[d]["cost"] += s.total_cost
            daily[d]["turns"] += s.turn_count
            daily[d]["prompt_tokens"] += s.total_prompt_tokens
            daily[d]["cache_read"] += s.total_cache_read
            daily[d]["cache_write"] += s.total_cache_write
            daily[d]["read_cache_measurable"] = (
                daily[d]["read_cache_measurable"]
                and _aggregate_read_cache_measurable(self._responses.get(s.session_id, []))
            )
            daily[d]["write_cache_measurable"] = (
                daily[d]["write_cache_measurable"] and s.write_cache_measurable
            )
            daily[d]["pricing_stale"] = (
                daily[d]["pricing_stale"] or s.pricing_stale
            )

        hit_rate = total_cr / (total_cr + total_cw) if (total_cr + total_cw) > 0 else None

        daily_list = sorted(daily.values(), key=lambda x: x["date"])
        for row in daily_list:
            row["read_cache_rate"] = _compute_read_cache_rate(
                row["prompt_tokens"],
                row["cache_read"],
                measurable=row["read_cache_measurable"],
            )

        dashboard_read_cache_measurable = all(
            _aggregate_read_cache_measurable(self._responses.get(s.session_id, []))
            for s in sessions
        ) if sessions else False

        return DashboardSummary(
            date_from=date_from,
            date_to=date_to,
            total_cost=total_cost,
            total_turns=total_turns,
            total_sessions=len(sessions),
            total_prompt_tokens=total_prompt,
            read_cache_rate=_compute_read_cache_rate(
                total_prompt,
                total_cr,
                measurable=dashboard_read_cache_measurable,
            ),
            total_cache_read=total_cr,
            total_cache_write=total_cw,
            cache_hit_rate=hit_rate,
            write_cache_measurable=all(
                s.write_cache_measurable for s in sessions
            ) if sessions else False,
            daily_costs=daily_list,
            pricing_sources=_aggregate_pricing_sources(all_responses),
            pricing_stale=_has_stale_pricing(all_responses),
        )

    def get_session_detail(self, session_id: str) -> dict | None:
        sf = self._files.get(session_id)
        if sf is None or sf.meta is None:
            return None
        summary = self.get_session_summary(session_id)
        if summary is None:
            return None
        turns = self._turns.get(session_id, [])
        return {
            "session_id": session_id,
            "meta": {
                "status": sf.meta.status,
                "created_at": sf.meta.created_at.isoformat(),
                "updated_at": sf.meta.updated_at.isoformat(),
            },
            "summary": {
                "total_cost": summary.total_cost,
                "turn_count": summary.turn_count,
                "read_cache_rate": summary.read_cache_rate,
                "cache_hit_rate": summary.cache_hit_rate,
                "peak_prompt_tokens": summary.peak_prompt_tokens,
                "total_cache_read": summary.total_cache_read,
                "total_cache_write": summary.total_cache_write,
                "write_cache_measurable": summary.write_cache_measurable,
                "pricing_sources": summary.pricing_sources,
                "pricing_stale": summary.pricing_stale,
            },
            "turns": [_serialize_turn(t) for t in turns],
        }

    def get_all_requests(
        self,
        date_from: date,
        date_to: date,
        *,
        client_label: str | None = None,
    ) -> list[dict]:
        """Return request rows across sessions in date range, sorted newest first."""
        results: list[dict] = []
        for sid, sf in self._files.items():
            if sf.meta is None:
                continue
            session_label = sf.meta.created_at.strftime("%m/%d %H:%M")
            response_by_request = self._responses_by_request_id(sid)
            for req in self._requests.get(sid, []):
                request_date = req.ts.date()
                if request_date < date_from or request_date > date_to:
                    continue
                if client_label is not None and req.client_label != client_label:
                    continue
                rm = response_by_request.get(req.request_id)
                status = _request_status(rm)
                usage_available = bool(rm and rm.usage_available)
                results.append({
                    "ts": req.ts.isoformat(),
                    "session_id": sid,
                    "session_label": session_label,
                    "request_id": req.request_id,
                    "turn_id": req.turn_id,
                    "round": req.round,
                    "client_label": req.client_label,
                    "provider": req.provider,
                    "model": req.model,
                    "call_type": req.call_type,
                    "message_count": req.message_count,
                    "tool_count": req.tool_count,
                    "image_count": req.image_count,
                    "has_image": req.image_count > 0,
                    "has_response_schema": req.has_response_schema,
                    "temperature": req.temperature,
                    "status": status,
                    "usage_available": usage_available,
                    "prompt_tokens": rm.prompt_tokens if usage_available and rm else None,
                    "completion_tokens": (
                        rm.completion_tokens if usage_available and rm else None
                    ),
                    "read_cache_rate": (
                        _compute_read_cache_rate(
                            rm.prompt_tokens,
                            rm.cache_read_tokens,
                            measurable=_is_read_cache_measurable(rm.provider),
                        )
                        if usage_available and rm
                        else None
                    ),
                    "cache_read_tokens": (
                        rm.cache_read_tokens if usage_available and rm else None
                    ),
                    "cache_write_tokens": (
                        rm.cache_write_tokens if usage_available and rm else None
                    ),
                    "write_cache_measurable": (
                        _is_write_cache_measurable(rm.provider)
                        if usage_available and rm
                        else False
                    ),
                    "latency_ms": rm.latency_ms if rm else None,
                    "cost": rm.cost if rm else None,
                    "error": rm.error if rm else None,
                    "pricing_source": rm.pricing_source if rm else None,
                    "pricing_source_url": rm.pricing_source_url if rm else None,
                    "pricing_stale": rm.pricing_stale if rm else False,
                })
        results.sort(key=lambda r: r["ts"], reverse=True)
        return results

    def get_client_labels_in_range(self, date_from: date, date_to: date) -> list[str]:
        labels: set[str] = set()
        for reqs in self._requests.values():
            for req in reqs:
                request_date = req.ts.date()
                if date_from <= request_date <= date_to:
                    labels.add(req.client_label)
        return sorted(labels)

    def get_request_detail(self, session_id: str, request_id: str) -> dict | None:
        sf = self._files.get(session_id)
        if sf is None or sf.meta is None:
            return None
        request = read_request_record(sf.session_dir, request_id)
        if request is None:
            return None
        response = self._responses_by_request_id(session_id).get(request_id)
        return {
            "session_id": session_id,
            "request_id": request.request_id,
            "ts": request.ts.isoformat(),
            "turn_id": request.turn_id,
            "round": request.round,
            "client_label": request.client_label,
            "provider": request.provider,
            "model": request.model,
            "call_type": request.call_type,
            "temperature": request.temperature,
            "response_schema": request.response_schema,
            "messages": [_serialize_request_message(message) for message in request.messages],
            "tools": [
                tool.model_dump(mode="json", exclude_none=True)
                for tool in request.tools or []
            ],
            "response": _serialize_response_summary(response),
        }

    def get_live_status(self, soft_limit: int) -> dict | None:
        """Return token position for the most recent active session."""
        active: SessionFiles | None = None
        for sf in self._files.values():
            if sf.meta is None or sf.meta.status != "active":
                continue
            if active is None or sf.meta.updated_at > active.meta.updated_at:
                active = sf
        if active is None or active.meta is None:
            return None

        sid = active.meta.session_id
        turns = self._turns.get(sid, [])
        last_prompt = 0
        if turns:
            last_prompt = turns[-1].max_prompt_tokens or 0

        # Resolve hard limit from pricing
        hard_limit = 200_000  # default
        responses = self._responses.get(sid, [])
        if responses:
            last_resp = responses[-1]
            from .pricing import resolve_model_key

            model_key = resolve_model_key(last_resp.provider, last_resp.model)
            if model_key and model_key in self.pricing:
                ml = self.pricing[model_key].max_input_tokens
                if ml:
                    hard_limit = ml

        return {
            "active": True,
            "session_id": sid,
            "prompt_tokens": last_prompt,
            "soft_limit": soft_limit,
            "hard_limit": hard_limit,
        }

    def _responses_by_request_id(self, session_id: str) -> dict[str, ResponseMetrics]:
        results: dict[str, ResponseMetrics] = {}
        for response in self._responses.get(session_id, []):
            if response.request_id is None:
                continue
            results[response.request_id] = response
        return results


def _serialize_turn(t: TurnMetrics) -> dict:
    return {
        "turn_id": t.turn_id,
        "ts_started": t.ts_started.isoformat(),
        "ts_finished": t.ts_finished.isoformat(),
        "channel": t.channel,
        "sender": t.sender,
        "status": t.status,
        "llm_rounds": t.llm_rounds,
        "max_prompt_tokens": t.max_prompt_tokens,
        "total_prompt_tokens": t.total_prompt_tokens,
        "read_cache_rate": t.read_cache_rate,
        "cache_read_tokens": t.cache_read_tokens,
        "cache_write_tokens": t.cache_write_tokens,
        "write_cache_measurable": t.write_cache_measurable,
        "total_cost": t.total_cost,
        "pricing_sources": _aggregate_pricing_sources(t.responses),
        "pricing_stale": _has_stale_pricing(t.responses),
        "responses": [
            {
                "request_id": r.request_id,
                "round": r.round,
                "provider": r.provider,
                "model": r.model,
                "usage_available": r.usage_available,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "read_cache_rate": _compute_read_cache_rate(
                    r.prompt_tokens,
                    r.cache_read_tokens,
                    measurable=(
                        r.usage_available and _is_read_cache_measurable(r.provider)
                    ),
                ),
                "cache_read_tokens": r.cache_read_tokens,
                "cache_write_tokens": r.cache_write_tokens,
                "write_cache_measurable": (
                    r.usage_available and _is_write_cache_measurable(r.provider)
                ),
                "latency_ms": r.latency_ms,
                "cost": r.cost,
                "client_label": r.client_label,
                "status": _request_status(r),
                "error": r.error,
                "pricing_source": r.pricing_source,
                "pricing_source_url": r.pricing_source_url,
                "pricing_stale": r.pricing_stale,
            }
            for r in t.responses
        ],
    }


def _request_status(response: ResponseMetrics | None) -> str:
    if response is None:
        return "pending"
    if response.error:
        return "failed"
    return "completed"


def _serialize_response_summary(response: ResponseMetrics | None) -> dict | None:
    if response is None:
        return None
    return {
        "ts": response.ts.isoformat(),
        "status": _request_status(response),
        "latency_ms": response.latency_ms,
        "usage_available": response.usage_available,
        "prompt_tokens": response.prompt_tokens if response.usage_available else None,
        "completion_tokens": (
            response.completion_tokens if response.usage_available else None
        ),
        "cache_read_tokens": (
            response.cache_read_tokens if response.usage_available else None
        ),
        "cache_write_tokens": (
            response.cache_write_tokens if response.usage_available else None
        ),
        "cost": response.cost,
        "response_text": response.response_text,
        "error": response.error,
        "pricing_source": response.pricing_source,
        "pricing_source_url": response.pricing_source_url,
        "pricing_stale": response.pricing_stale,
    }


def _serialize_request_message(message: Any) -> dict:
    result = message.model_dump(
        mode="json",
        exclude={
            "content",
            "codex_compaction_encrypted_content",
        },
        exclude_none=True,
    )
    encrypted = getattr(message, "codex_compaction_encrypted_content", None)
    if encrypted:
        result["codex_compaction_encrypted_content_chars"] = len(encrypted)
    result["content"] = _serialize_message_content(getattr(message, "content", None))
    return result


def _serialize_message_content(content: Any) -> list[dict]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return [{"type": "unknown", "repr": repr(content)}]

    parts: list[dict] = []
    for part in content:
        part_type = getattr(part, "type", None)
        if part_type == "text":
            text_part = part.model_dump(
                mode="json",
                exclude={"data"},
                exclude_none=True,
            )
            parts.append(text_part)
            continue
        if part_type == "image":
            parts.append(_serialize_image_part(part))
            continue
        parts.append(part.model_dump(mode="json", exclude={"data"}, exclude_none=True))
    return parts


def _serialize_image_part(part: Any) -> dict:
    data = getattr(part, "data", None)
    decoded_size = _base64_size(data)
    result = part.model_dump(
        mode="json",
        exclude={"data"},
        exclude_none=True,
    )
    result["data_size_bytes"] = decoded_size
    thumbnail = _thumbnail_data_url(data)
    if thumbnail is not None:
        result["thumbnail_data_url"] = thumbnail
    return result


def _base64_size(data: str | None) -> int | None:
    if not data:
        return None
    try:
        import base64

        return len(base64.b64decode(data))
    except Exception:
        return None


def _thumbnail_data_url(data: str | None) -> str | None:
    if not data:
        return None
    try:
        import base64
        import io

        from PIL import Image

        raw = base64.b64decode(data)
        img = Image.open(io.BytesIO(raw))
        img.thumbnail((320, 320))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=75)
        encoded = base64.b64encode(out.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    except Exception:
        return None
