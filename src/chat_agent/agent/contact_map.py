"""Universal contact map: fast sender-to-name resolution cache.

Two-layer sender resolution:
  Layer 1 (this module): Read from .agent/memory/cache/contact_map.json
  Layer 2 (brain LLM):   memory_search + update_contact_mapping tool
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# {"gmail": {"email@addr": "name"}, "line": {"display": "name"}}
ContactMapData = dict[str, dict[str, str]]


class ContactMap:
    """Channel-agnostic sender-to-name cache.

    Reads/writes a JSON file at ``cache_dir/contact_map.json``.
    Tolerates missing or corrupt files (degrades to empty map).
    """

    _FILENAME = "contact_map.json"

    def __init__(self, cache_dir: Path) -> None:
        self._path = cache_dir / self._FILENAME
        self._data: ContactMapData = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = raw
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load contact map %s: %s", self._path, exc)

    def resolve(self, channel: str, sender_key: str) -> str | None:
        """Look up cached name for a sender. Returns None on miss."""
        return self._data.get(channel, {}).get(sender_key)

    def reverse_lookup(self, channel: str, name: str) -> str | None:
        """Find sender_key by name for a given channel. Returns None on miss."""
        for key, val in self._data.get(channel, {}).items():
            if val == name:
                return key
        return None

    def update(self, channel: str, sender_key: str, name: str) -> None:
        """Add or overwrite a mapping and persist to disk."""
        if channel not in self._data:
            self._data[channel] = {}
        self._data[channel][sender_key] = name
        self._persist()

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
