"""Mechanical checks for one Stage D value; no model or network calls.

The placeholder and numeric rules are the plan-actionability ones (config/plan-actionability.yaml),
so a value accepted here is not rejected later by the actionability assessment for the same reason.
"""
from __future__ import annotations

import re
from typing import Any

from .actionability import is_placeholder

QUANTITY = re.compile(r'^\s*([0-9]+(?:\.[0-9]+)?)\s*([^\s0-9][^\n]{0,19})\s*$')
DIGIT = re.compile(r'[0-9０-９]')


def sentence_count(text: str) -> int:
    """Japanese 。！？ always end a sentence; ASCII . ! ? end one before whitespace and a letter or at the end."""
    stripped = re.sub(r'「[^」]*」|『[^』]*』|“[^”]*”|"[^"]*"', '', text.strip())
    count = len(re.findall(r'[。！？]+(?=.)', stripped))
    count += len(re.findall(r'(?<![0-9A-Z])[.!?]+\s+(?=[A-Za-z぀-ヿ㐀-鿿])', stripped))
    return count + 1


def quantity(text: str) -> dict[str, str] | None:
    match = QUANTITY.match(text.replace('　', ' '))
    if not match:
        return None
    return {'value': match.group(1), 'unit': match.group(2).strip()}


def check_value(request: dict[str, Any], value: Any, policy: dict[str, Any]) -> list[dict[str, str]]:
    fmt = request['answer_format']
    if not isinstance(value, str) or not value.strip():
        return [{'check': 'non_empty', 'reason': 'Return a non-empty value.'}]
    failures = []
    if fmt['type'] == 'choice':
        if value not in fmt['choices']:
            failures.append({'check': 'one_of', 'reason': 'Choose one of the listed values exactly.'})
        return failures
    if len(value) < fmt.get('min_chars', 1):
        failures.append({'check': f"min_chars:{fmt['min_chars']}", 'reason': f"Use at least {fmt['min_chars']} characters."})
    if len(value) > fmt['max_chars']:
        failures.append({'check': f"max_chars:{fmt['max_chars']}", 'reason': f"Use at most {fmt['max_chars']} characters."})
    for check in request['checks']:
        ok, reason = True, ''
        if check == 'not_placeholder':
            ok = not is_placeholder(value, policy)
            reason = 'Write the actual content, not a placeholder such as 未記入, TBD or なし.'
        elif check == 'one_sentence':
            ok = '\n' not in value.strip() and sentence_count(value) <= 1
            reason = 'Return one sentence only.'
        elif check == 'has_number':
            ok = bool(DIGIT.search(value))
            reason = 'State the value with a number and its unit.'
        elif check.startswith('quantity:'):
            parsed = quantity(value)
            units = check.split(':', 1)[1].split(',')
            ok = parsed is not None and parsed['unit'] in units
            reason = 'Return the quantity as "<number> <unit>" with one of these units: ' + ', '.join(units) + '.'
        elif check.startswith('not_same_as:'):
            others = request['inputs'].get(check.split(':', 1)[1]) or []
            others = others if isinstance(others, list) else [others]
            ok = all(' '.join(value.split()) != ' '.join(str(other).split()) for other in others)
            reason = 'Write a value for this item; do not repeat an earlier answer.'
        if not ok:
            failures.append({'check': check, 'reason': reason})
    return failures
