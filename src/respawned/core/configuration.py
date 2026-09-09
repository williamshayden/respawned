"""Small, shared validators for policy and evaluator configuration."""

from collections.abc import Collection, Mapping
from decimal import Decimal, InvalidOperation


def strict_mapping(
    value: object,
    field: str,
    required: Collection[str] = (),
    optional: Collection[str] = (),
) -> Mapping[object, object]:
    value = mapping(value, field)
    keys = set(value)
    if missing := set(required) - keys:
        raise ValueError(f"{field} is missing {sorted(missing)!r}")
    if unknown := keys - set(required) - set(optional):
        raise ValueError(f"{field} has unknown fields {sorted(unknown, key=str)!r}")
    return value


def mapping(value: object, field: str) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return value


def number(
    value: object,
    field: str,
    *,
    positive: bool = False,
    maximum: Decimal | None = None,
) -> Decimal:
    try:
        result = Decimal(str(value)) if not isinstance(value, bool) else Decimal("NaN")
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite number")
    if result < 0 or (positive and result == 0):
        bound = "positive" if positive else "non-negative"
        raise ValueError(f"{field} must be {bound}")
    if maximum is not None and result > maximum:
        raise ValueError(f"{field} must be at most {maximum}")
    return result


def integer(value: object, field: str, *, positive: bool = False) -> int:
    result = number(value, field, positive=positive)
    if result != result.to_integral_value():
        raise ValueError(f"{field} must be an integer")
    return int(result)


def nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")
    return value.strip()
