"""Reasoning module — deprecated.

All provider-specific reasoning logic has been moved to:
  - Validation: each provider's Config.validate_reasoning() in core/schema.py
  - Mapping: each provider's client module in llm/providers/*.py
See docs/dev/provider-api-spec.md for API facts vs adapter rules.

This module is kept as an empty placeholder to avoid import errors from
any code that may still reference it. No functions are exported.
"""
