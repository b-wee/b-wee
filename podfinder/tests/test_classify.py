from app.classify import classify


def test_commander_night_is_magic_commander_casual():
    result = classify("Wednesday Night Commander")
    assert result.game == "magic"
    assert result.format == "commander"
    assert result.is_casual is True


def test_edh_alias():
    result = classify("Monday EDH Night")
    assert result.game == "magic"
    assert result.format == "commander"


def test_fnm_draft():
    result = classify("FNM Booster Draft")
    assert result.game == "magic"
    assert result.format == "draft"


def test_rcq_is_competitive():
    result = classify("Standard RCQ Qualifier")
    assert result.game == "magic"
    assert result.format == "standard"
    assert result.is_casual is False


def test_cedh_is_competitive():
    result = classify("cEDH Tournament")
    assert result.format == "cedh"
    assert result.is_casual is False


def test_casual_keyword_overrides_competitive():
    result = classify("Casual Modern Tournament")
    assert result.is_casual is True


def test_pokemon():
    result = classify("Pokemon TCG Casual Play")
    assert result.game == "pokemon"
    assert result.format is None


def test_board_games():
    result = classify("Board Game Open Play")
    assert result.game == "boardgames"
    assert result.is_casual is True


def test_unknown_falls_back_to_other():
    result = classify("Mystery Meetup")
    assert result.game == "other"
