from __future__ import annotations

from collections.abc import Sequence
from math import sqrt


def cosine_similarity(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    if not left or len(left) != len(right):
        return -1.0
    denominator = sqrt(
        sum(value * value for value in left)
        * sum(value * value for value in right)
    )
    if denominator == 0:
        return -1.0
    return sum(
        left_value * right_value
        for left_value, right_value in zip(left, right, strict=True)
    ) / denominator
