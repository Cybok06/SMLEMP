import re


def normalize_ghana_phone(value):
    """Return Ghana numbers as 233XXXXXXXXX, or None when invalid."""
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 10 and digits.startswith("0"):
        digits = "233" + digits[1:]
    elif len(digits) == 9:
        digits = "233" + digits
    return digits if len(digits) == 12 and digits.startswith("233") else None


def ghana_phone_variants(value):
    normalized = normalize_ghana_phone(value)
    if not normalized:
        return []
    local = "0" + normalized[3:]
    return [normalized, "+" + normalized, local]
