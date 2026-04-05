"""In-memory metrics cache with incremental JSONL reading."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .pricing import ModelPricing, compute_request_cost
from .session_reader import (
    SessionFiles,
    discover_sessions,
    parse_response_record,
    parse_turn_record,
    read_meta,
    read_new_lines,
)

logger = logging.getLogger(__name__)


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
    cache_read_tokens: int
    cache_write_tokens: int
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
    total_cache_read: int
    total_cache_write: int
    cache_hit_rate: float | None
    peak_prompt_tokens: int


@dataclass
class DashboardSummary:
    date_from: date
    date_to: date
    total_cost: float
    total_turns: int
    total_sessions: int
    total_cache_read: int
    total_cache_write: int
    cache_hit_rate: float | None
    daily_costs: list[dict]  # [{date, cost, turns}]


class MetricsCache:
    """Central in-memory cache refreshed incrementally from JSONL files."""

    def __init__(self, sessions_dir: Path, pricing: dict[str, ModelPricing]) -> None:
        self._sessions_dir = sessions_dir
        self.pricing = pricing
        self._files: dict[str, SessionFiles] = {}
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
                    cache_read_tokens=rec.cache_read_tokens,
                    cache_write_tokens=rec.cache_write_tokens,
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
                if resp is None:
                    continue
                cost = compute_request_cost(
                    provider=rec.provider,
                    model=rec.model,
                    prompt_tokens=resp.prompt_tokens or 0,
                    completion_tokens=resp.completion_tokens or 0,
                    cache_read_tokens=resp.cache_read_tokens,
                    cache_write_tokens=resp.cache_write_tokens,
                    pricing=self.pricing,
                )
                rm = ResponseMetrics(
                    ts=rec.ts,
                    round=rec.round,
                    provider=rec.provider,
                    model=rec.model,
                    prompt_tokens=resp.prompt_tokens or 0,
                    completion_tokens=resp.completion_tokens or 0,
                    cache_read_tokens=resp.cache_read_tokens,
                    cache_write_tokens=resp.cache_write_tokens,
                    latency_ms=rec.latency_ms,
                    cost=cost,
                    turn_id=rec.turn_id,
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

        return changed

    def get_session_summary(self, session_id: str) -> SessionSummary | None:
        sf = self._files.get(session_id)
        if sf is None or sf.meta is None:
            return None
        turns = self._turns.get(session_id, [])
        total_cr = sum(t.cache_read_tokens for t in turns)
        total_cw = sum(t.cache_write_tokens for t in turns)
        costs = [t.total_cost for t in turns if t.total_cost is not None]
        peak = max((t.max_prompt_tokens or 0 for t in turns), default=0)
        hit_rate = total_cr / (total_cr + total_cw) if (total_cr + total_cw) > 0 else None
        return SessionSummary(
            session_id=session_id,
            status=sf.meta.status,
            created_at=sf.meta.created_at,
            updated_at=sf.meta.updated_at,
            turn_count=len(turns),
            total_cost=sum(costs) if costs else None,
            total_cache_read=total_cr,
            total_cache_write=total_cw,
            cache_hit_rate=hit_rate,
            peak_prompt_tokens=peak,
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
        total_cost = 0.0
        total_turns = 0
        total_cr = 0
        total_cw = 0
        daily: dict[date, dict] = {}

        for s in sessions:
            if s.total_cost is not None:
                total_cost += s.total_cost
            total_turns += s.turn_count
            total_cr += s.total_cache_read
            total_cw += s.total_cache_write
            # Daily aggregation by session created date
            d = s.created_at.date()
            if d not in daily:
                daily[d] = {"date": d.isoformat(), "cost": 0.0, "turns": 0, "cache_read": 0, "cache_write": 0}
            if s.total_cost is not None:
                daily[d]["cost"] += s.total_cost
            daily[d]["turns"] += s.turn_count
            daily[d]["cache_read"] += s.total_cache_read
            daily[d]["cache_write"] += s.total_cache_write

        hit_rate = total_cr / (total_cr + total_cw) if (total_cr + total_cw) > 0 else None

        daily_list = sorted(daily.values(), key=lambda x: x["date"])

        return DashboardSummary(
            date_from=date_from,
            date_to=date_to,
            total_cost=total_cost,
            total_turns=total_turns,
            total_sessions=len(sessions),
            total_cache_read=total_cr,
            total_cache_write=total_cw,
            cache_hit_rate=hit_rate,
            daily_costs=daily_list,
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
                "cache_hit_rate": summary.cache_hit_rate,
                "peak_prompt_tokens": summary.peak_prompt_tokens,
                "total_cache_read": summary.total_cache_read,
                "total_cache_write": summary.total_cache_write,
            },
            "turns": [_serialize_turn(t) for t in turns],
        }

    def get_all_requests(
        self, date_from: date, date_to: date
    ) -> list[dict]:
        """Return all response records across sessions in date range, sorted by time."""
        results: list[dict] = []
        for sid, sf in self._files.items():
            if sf.meta is None:
                continue
            created = sf.meta.created_at.date()
            if created < date_from or created > date_to:
                continue
            session_label = sf.meta.created_at.strftime("%m/%d %H:%M")
            for rm in self._responses.get(sid, []):
                results.append({
                    "ts": rm.ts.isoformat(),
                    "session_id": sid,
                    "session_label": session_label,
                    "turn_id": rm.turn_id,
                    "round": rm.round,
                    "provider": rm.provider,
                    "model": rm.model,
                    "prompt_tokens": rm.prompt_tokens,
                    "completion_tokens": rm.completion_tokens,
                    "cache_read_tokens": rm.cache_read_tokens,
                    "cache_write_tokens": rm.cache_write_tokens,
                    "latency_ms": rm.latency_ms,
                    "cost": rm.cost,
                })
        results.sort(key=lambda r: r["ts"])
        return results

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
        "cache_read_tokens": t.cache_read_tokens,
        "cache_write_tokens": t.cache_write_tokens,
        "total_cost": t.total_cost,
        "responses": [
            {
                "round": r.round,
                "provider": r.provider,
                "model": r.model,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "cache_read_tokens": r.cache_read_tokens,
                "cache_write_tokens": r.cache_write_tokens,
                "latency_ms": r.latency_ms,
                "cost": r.cost,
            }
            for r in t.responses
        ],
    }
