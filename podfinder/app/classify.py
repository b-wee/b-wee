"""Classify raw event titles/descriptions into game + format.

Store calendars are free text ("Wednesday Night EDH!", "FNM Draft"), so we
match keywords to figure out what game and format an event is, and whether
it reads as casual or competitive. This is the heart of aggregating messy
feeds into something filterable.
"""

import re
from dataclasses import dataclass
from typing import Optional

# Keyword -> game. Checked in order; first match wins. Keys are regexes
# matched case-insensitively against title + description.
GAME_PATTERNS: list[tuple[str, str]] = [
    (r"\b(commander|edh|cedh|mtg|magic(:| the)? gathering|magic|fnm|friday night magic"
     r"|draft|sealed|modern|standard|pioneer|legacy|pauper|prerelease|pre-release"
     r"|planeswalker|cube)\b", "magic"),
    (r"\b(pok[eé]mon|pokemon tcg)\b", "pokemon"),
    (r"\b(lorcana)\b", "lorcana"),
    (r"\b(yu-?gi-?oh)\b", "yugioh"),
    (r"\b(one piece)\b", "onepiece"),
    (r"\b(flesh and blood|fab)\b", "fleshandblood"),
    (r"\b(star wars unlimited|swu)\b", "starwars"),
    (r"\b(warhammer|40k|age of sigmar|kill team)\b", "warhammer"),
    (r"\b(d&d|dnd|dungeons\s*(&|and)\s*dragons|pathfinder|rpg|adventurers league)\b", "rpg"),
    (r"\b(board ?games?|tabletop)\b", "boardgames"),
]

# Magic-specific formats. First match wins.
MAGIC_FORMAT_PATTERNS: list[tuple[str, str]] = [
    (r"\b(cedh)\b", "cedh"),
    (r"\b(commander|edh)\b", "commander"),
    (r"\b(booster )?draft\b", "draft"),
    (r"\b(sealed|prerelease|pre-release)\b", "sealed"),
    (r"\bmodern\b", "modern"),
    (r"\bstandard\b", "standard"),
    (r"\bpioneer\b", "pioneer"),
    (r"\blegacy\b", "legacy"),
    (r"\bpauper\b", "pauper"),
    (r"\bcube\b", "cube"),
]

# Signals that an event is competitive rather than casual.
COMPETITIVE_PATTERN = re.compile(
    r"\b(rcq|regional championship|qualifier|invitational|1k|2k|5k|10k"
    r"|championship|cedh|competitive|tournament)\b",
    re.IGNORECASE,
)

# Signals that an event is explicitly casual (overrides competitive words).
CASUAL_PATTERN = re.compile(
    r"\b(casual|chill|learn to play|beginner|open play|league night|game night)\b",
    re.IGNORECASE,
)

GAME_LABELS = {
    "magic": "Magic: The Gathering",
    "pokemon": "Pokémon",
    "lorcana": "Disney Lorcana",
    "yugioh": "Yu-Gi-Oh!",
    "onepiece": "One Piece",
    "fleshandblood": "Flesh and Blood",
    "starwars": "Star Wars: Unlimited",
    "warhammer": "Warhammer",
    "rpg": "RPGs / D&D",
    "boardgames": "Board Games",
    "other": "Other",
}

FORMAT_LABELS = {
    "commander": "Commander",
    "cedh": "cEDH",
    "draft": "Draft",
    "sealed": "Sealed / Prerelease",
    "modern": "Modern",
    "standard": "Standard",
    "pioneer": "Pioneer",
    "legacy": "Legacy",
    "pauper": "Pauper",
    "cube": "Cube",
}


@dataclass
class Classification:
    game: str
    format: Optional[str]
    is_casual: bool


def classify(title: str, description: str = "") -> Classification:
    """Guess game, format, and casual-ness from an event's text."""
    text = f"{title} {description or ''}"

    game = "other"
    for pattern, name in GAME_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            game = name
            break

    fmt = None
    if game == "magic":
        for pattern, name in MAGIC_FORMAT_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                fmt = name
                break

    if CASUAL_PATTERN.search(text):
        is_casual = True
    elif COMPETITIVE_PATTERN.search(text):
        is_casual = False
    else:
        # Commander nights and untagged store events are casual by default;
        # that matches how stores actually run them.
        is_casual = fmt not in {"modern", "standard", "pioneer", "legacy"}

    return Classification(game=game, format=fmt, is_casual=is_casual)
