from app.geo import haversine_miles, parse_fee_cents, parse_location, zip_to_latlon


def test_haversine_known_distance():
    # Springfield, IL to Decatur, IL is roughly 36 miles.
    distance = haversine_miles(39.7817, -89.6501, 39.8403, -88.9548)
    assert 34 <= distance <= 39


def test_haversine_zero():
    assert haversine_miles(39.78, -89.65, 39.78, -89.65) == 0


def test_zip_lookup():
    result = zip_to_latlon("62701")  # Springfield, IL
    assert result is not None
    lat, lon = result
    assert 39 <= lat <= 41
    assert -91 <= lon <= -88


def test_zip_lookup_unknown():
    assert zip_to_latlon("00000") is None


def test_parse_location_zip():
    assert parse_location("62701") is not None
    assert parse_location(" 62701-1234 ") is not None


def test_parse_location_latlon():
    assert parse_location("39.78,-89.65") == (39.78, -89.65)
    assert parse_location("39.78, -89.65") == (39.78, -89.65)


def test_parse_location_rejects_garbage():
    assert parse_location("") is None
    assert parse_location(None) is None
    assert parse_location("not a place") is None
    assert parse_location("999,-999") is None


def test_parse_fee_cents():
    assert parse_fee_cents("Free") == 0
    assert parse_fee_cents("$15") == 1500
    assert parse_fee_cents("$7.50") == 750
    assert parse_fee_cents("5 dollars") == 500
    assert parse_fee_cents(None) is None
    assert parse_fee_cents("TBD") is None
