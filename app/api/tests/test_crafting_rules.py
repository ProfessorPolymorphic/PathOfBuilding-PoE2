"""Consistency checks on the hand-written crafting rules table."""

import re
from pathlib import Path

from crafting_rules import (
    CURRENCIES,
    CURRENCY_BY_NAME,
    ESSENCES,
    MAX_AFFIXES,
    OMEN_TARGETS,
    OMENS,
)

MOD_ITEM = Path(__file__).resolve().parents[3] / "src" / "Data" / "ModItem.lua"


def test_names_are_unique():
    assert len(CURRENCY_BY_NAME) == len(CURRENCIES)
    assert len({o.name for o in OMENS}) == len(OMENS)


def test_every_omen_targets_something_in_the_table():
    for omen in OMENS:
        assert omen.target in OMEN_TARGETS, omen.name


def test_every_entry_quotes_its_source():
    for entry in [*CURRENCIES, *OMENS, *ESSENCES]:
        assert entry.text and entry.source.startswith("https://poe2db.tw/")


def test_greater_and_perfect_tiers_raise_the_floor():
    for base in ("Orb of Transmutation", "Orb of Augmentation", "Regal Orb",
                 "Exalted Orb", "Chaos Orb"):
        plain, greater, perfect = (CURRENCY_BY_NAME[f"{t}{base}"]
                                   for t in ("", "Greater ", "Perfect "))
        assert plain.min_mod_level == 0 < greater.min_mod_level < perfect.min_mod_level
        assert (greater.text, greater.adds, greater.requires) == (
            plain.text, plain.adds, plain.requires)


def test_rarity_changes_respect_affix_limits():
    for c in CURRENCIES:
        if c.result and c.adds:
            prefixes, suffixes = MAX_AFFIXES[c.result]
            assert c.adds <= prefixes + suffixes, c.name


def test_min_mod_levels_match_real_tiers():
    """Each floor should sit inside the range of tier levels the game data has."""
    levels = {int(m) for m in re.findall(r"\blevel = (\d+)", MOD_ITEM.read_text())}
    floors = {c.min_mod_level for c in CURRENCIES if c.min_mod_level}
    assert floors and all(min(levels) < f <= max(levels) for f in floors)
