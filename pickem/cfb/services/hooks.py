from decimal import Decimal


def apply_forced_hook(spread: Decimal) -> Decimal:
    """Add a half point away from zero when ``spread`` is a whole number."""
    value = Decimal(str(spread))

    if value != value.to_integral_value() or value == 0:
        return value

    hook = Decimal("0.5")
    return value + hook if value > 0 else value - hook
