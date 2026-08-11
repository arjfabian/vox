"""Prompt sanitizer and token trimmer.

Strips control characters, removes known boilerplate prefixes,
collapses verbose JSON, and enforces a configurable character
ceiling before any prompt leaves the VOX process.
"""

import json
import re

from .models import SanitizedPrompt

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MULTILINE_BLANK = re.compile(r"\n{3,}")
_BOILERPLATE_PREFIXES = (
    "you are an ai assistant",
    "you are a helpful assistant",
    "you are a large language model",
    "as an ai language model",
)
_TOKEN_ESTIMATE_RATIO = 4.0


def _estimate_tokens(text: str) -> int:
    return int(len(text) / _TOKEN_ESTIMATE_RATIO) or 1


def _strip_boilerplate(text: str) -> str:
    lowered = text.lower().strip()
    for prefix in _BOILERPLATE_PREFIXES:
        if lowered.startswith(prefix):
            tail = text[len(prefix) :].lstrip(",.;:!?\n ")
            return tail
    return text


def _find_json_blocks(text: str) -> list[tuple[int, int]]:
    """Finds start and end indexes in balanced JSON blocks."""
    spans, stack = [], []
    for i, ch in enumerate(text):
        if ch in "{[":
            stack.append(i)
        elif ch in "}]" and stack:
            start = stack.pop()
            if not stack:
                spans.append((start, i + 1))
    return spans


def _collapse_json(text: str) -> str:
    """Collapses verbose JSONs in one line to optimize tokens."""
    spans = _find_json_blocks(text)
    if not spans:
        return text

    out, last = [], 0
    for start, end in spans:
        out.append(text[last:start])
        chunk = text[start:end]
        try:
            # Real compaction, no unnecessary spaces or line breaks
            out.append(json.dumps(json.loads(chunk), separators=(",", ":")))
        except (json.JSONDecodeError, ValueError):
            out.append(chunk)
        last = end
    out.append(text[last:])
    return "".join(out)


async def sanitize(
    text: str,
    max_input_chars: int = 16384,
) -> SanitizedPrompt:
    original_length = len(text)

    cleaned = _CONTROL_CHARS.sub("", text)
    cleaned = _MULTILINE_BLANK.sub("\n\n", cleaned)
    cleaned = _strip_boilerplate(cleaned)
    cleaned = _collapse_json(cleaned)

    truncated = False
    if len(cleaned) > max_input_chars:
        cleaned = cleaned[:max_input_chars].rsplit("\n", 1)[0]
        truncated = True

    trimmed_length = len(cleaned)
    original_tokens = _estimate_tokens(text)
    trimmed_tokens = _estimate_tokens(cleaned)

    return SanitizedPrompt(
        text=cleaned,
        original_length=original_length,
        trimmed_length=trimmed_length,
        tokens_saved=original_tokens - trimmed_tokens,
        truncated=truncated,
    )
