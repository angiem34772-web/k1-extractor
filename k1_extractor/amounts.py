"""Parsing and allocating dollar amounts."""

import re

# Matches 1,234 / (1,234) / -1,234 / 1234.56
AMOUNT = r"\(?-?\$?[\d,]+(?:\.\d+)?\)?"


def parse_amount(text: str) -> float:
    """Turn a printed amount into a number. Parentheses mean negative."""
    if text is None:
        raise ValueError("no amount")
    t = text.strip().replace("$", "").replace(",", "")
    if not t:
        raise ValueError("empty amount")
    negative = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    if t.startswith("-"):
        negative = not negative if negative else True
        t = t[1:]
    if not re.fullmatch(r"\d+(?:\.\d+)?", t):
        raise ValueError(f"not an amount: {text!r}")
    value = float(t)
    return -value if negative else value


def format_amount(value: float) -> str:
    """Print an amount the way K-1s do: whole dollars, parentheses for negatives."""
    v = int(round(value))
    return f"({abs(v):,})" if v < 0 else f"{v:,}"


def allocate(total: int, weights: list[float]) -> list[int]:
    """Split a whole-dollar total across weights so the pieces add back exactly
    (largest-remainder rounding)."""
    weight_sum = sum(weights)
    if weight_sum == 0:
        return [0] * len(weights)
    raw = [total * w / weight_sum for w in weights]
    floors = [int(x // 1) for x in raw]
    remainder = total - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in order[:remainder]:
        floors[i] += 1
    return floors
