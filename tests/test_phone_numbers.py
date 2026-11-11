from services.phone_numbers import ghana_phone_variants, normalize_ghana_phone


def test_ghana_phone_equivalent_formats_normalize_identically():
    expected = "233530393625"
    assert normalize_ghana_phone("0530393625") == expected
    assert normalize_ghana_phone("+233530393625") == expected
    assert normalize_ghana_phone("233530393625") == expected
    assert normalize_ghana_phone("+233 53 039 3625") == expected


def test_invalid_phone_is_rejected():
    assert normalize_ghana_phone("") is None
    assert normalize_ghana_phone("12345") is None
    assert normalize_ghana_phone("+1 202 555 0100") is None


def test_variants_include_local_and_international_forms():
    assert ghana_phone_variants("0530393625") == ["233530393625", "+233530393625", "0530393625"]
