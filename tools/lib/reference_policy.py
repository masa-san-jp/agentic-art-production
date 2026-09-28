"""Shared source-reference access reason rules for plan generation and gates."""

from __future__ import annotations

from typing import Any


REASON_CODES = frozenset({
    "LEGACY_UNSPECIFIED",
    "CATEGORY_NOT_MAPPED",
    "INTERNAL_RECORD",
    "NO_SOURCE_FOR_CATEGORY",
    "SOURCE_HAS_NO_PUBLIC_URL",
    "URL_NOT_PERMANENT",
})

# These are the only reason codes Research may put in a new source-ref index.
# LEGACY_UNSPECIFIED is synthesized below only when an old handoff omitted the
# reason field entirely.
RESEARCH_REASON_CODES = frozenset(REASON_CODES - {"LEGACY_UNSPECIFIED"})


def reference_reason(reference: dict[str, Any]) -> str | None:
    """Return the explicit reason, or the legacy marker for old handoffs."""

    if reference.get("access_url") is not None:
        return None
    reason = reference.get("access_url_reason")
    return str(reason) if isinstance(reason, str) and reason else "LEGACY_UNSPECIFIED"


def category_is_required(policy: dict[str, Any], category: str) -> bool:
    return any(
        isinstance(item, dict) and str(item.get("id")) == category and item.get("required") is True
        for item in policy.get("categories", [])
    )


def missing_access_is_blocking(
    policy: dict[str, Any],
    category: str,
    reason: str,
    *,
    has_permanent_url: bool = False,
) -> bool:
    """Apply the configured reason rule only when the plan depends on category."""

    if not category_is_required(policy, category):
        return False
    if reason == "INTERNAL_RECORD" and has_permanent_url:
        return False
    rules = policy.get("missing_url_reasons", {})
    rule = rules.get(reason) if isinstance(rules, dict) else None
    if not isinstance(rule, dict):
        return True
    return bool(rule.get("blocking_if_required", True))
