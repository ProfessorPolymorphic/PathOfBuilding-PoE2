"""What PoE2's crafting items do to a piece of equipment, as data.

The game files hold the mod pool (src/Data/ModItem.lua: tiers, item level, prefix or
suffix, which bases can roll them) and essence results (src/Data/Essence.lua). They do not
hold what each currency does, so this table writes those rules down by hand. Each entry
quotes the in-game text as poe2db shows it for patch 0.5 (Runes of Aldur), checked
2026-11-24:

  https://poe2db.tw/us/Stackable_Currency
  https://poe2db.tw/us/Omen
  https://poe2db.tw/us/Essence

Scope: crafting on equipment (weapons, armour, jewellery). Waystones, gems, flasks,
logbooks, vendors and gambling are left out.

Not modelled yet:
  - Mod weights. They are not in the game files; poe2db and Craft of Exile use community
    estimates (https://poe2db.tw/us/weightings). Until we have them, a random roll means
    "any eligible mod", not a probability.
  - Desecration (Abyssal bones, Well of Souls). Only second-hand guides were readable, so
    the bones themselves are not listed. The omens that steer a desecration are, because
    their text was read from poe2db.
  - Affix limits (MAX_AFFIXES) are standard game knowledge, not taken from the pages above.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Rarity = Literal["normal", "magic", "rare"]
Side = Literal["any", "prefix", "suffix"]

SOURCES = {
    "currency": "https://poe2db.tw/us/Stackable_Currency",
    "omen": "https://poe2db.tw/us/Omen",
    "essence": "https://poe2db.tw/us/Essence",
}

# Most prefixes and suffixes an item of each rarity can hold.
MAX_AFFIXES: dict[Rarity, tuple[int, int]] = {
    "normal": (0, 0),
    "magic": (1, 1),
    "rare": (3, 3),
}


@dataclass(frozen=True)
class Currency:
    """One crafting item applied directly to an item.

    `adds` and `removes` count explicit modifiers. `min_mod_level` is the lowest mod
    level (ModItem.lua `level`) an added mod may have: the Greater and Perfect tiers.
    """

    name: str
    text: str
    requires: tuple[Rarity, ...]
    result: Rarity | None = None  # rarity afterwards; None means unchanged
    adds: int = 0
    removes: int = 0
    min_mods: int = 0  # explicit mods the item must already have
    min_mod_level: int = 0
    rerolls_values: bool = False
    fractures: bool = False
    corrupts: bool = False
    random_outcome: bool = False  # effect is not a fixed add or remove (Vaal, Chance)
    note: str = ""
    source: str = SOURCES["currency"]


@dataclass(frozen=True)
class Omen:
    """Changes the next use of `target` while it sits in the inventory.

    Fields left at None leave the target's behaviour alone.
    """

    name: str
    text: str
    target: str  # Currency name, "Perfect or Corrupted Essence", or "Desecration"
    side: Side | None = None
    adds: int | None = None
    removes: int | None = None
    removes_lowest_level: bool = False
    same_type_as_existing: bool = False
    note: str = ""
    source: str = SOURCES["omen"]


@dataclass(frozen=True)
class EssenceTier:
    """How an essence tier applies. Which mod it forces comes from Essence.lua."""

    tiers: tuple[str, ...]
    text: str
    requires: tuple[Rarity, ...]
    result: Rarity
    adds: int
    removes: int
    source: str = SOURCES["essence"]


def _tiered(name: str, text: str, levels: tuple[int, int], **kw) -> list[Currency]:
    """The base orb plus its Greater and Perfect tiers, which only raise min_mod_level."""
    greater, perfect = levels
    return [
        Currency(name, text, **kw),
        Currency(f"Greater {name}", text, min_mod_level=greater, **kw),
        Currency(f"Perfect {name}", text, min_mod_level=perfect, **kw),
    ]


CURRENCIES: list[Currency] = [
    *_tiered(
        "Orb of Transmutation",
        "Upgrades a Normal item to a Magic item with 1 modifier",
        (44, 70),
        requires=("normal",), result="magic", adds=1,
    ),
    *_tiered(
        "Orb of Augmentation",
        "Augments a Magic item with a new random modifier",
        (44, 70),
        requires=("magic",), adds=1,
    ),
    *_tiered(
        "Regal Orb",
        "Upgrades a Magic item to a Rare item, adding 1 modifier",
        (35, 50),
        requires=("magic",), result="rare", adds=1,
    ),
    *_tiered(
        "Exalted Orb",
        "Augments a Rare item with a new random modifier",
        (35, 50),
        requires=("rare",), adds=1,
    ),
    *_tiered(
        "Chaos Orb",
        "Removes a random modifier and augments a Rare item with a new random modifier",
        (35, 50),
        requires=("rare",), adds=1, removes=1, min_mods=1,
    ),
    Currency(
        "Orb of Alchemy",
        "Upgrades a Normal or Magic item to a Rare item with 4 random modifiers",
        requires=("normal", "magic"), result="rare", adds=4,
        note="Unverified whether a Magic item's existing mods are kept.",
    ),
    Currency(
        "Orb of Annulment",
        "Removes a random modifier from an item",
        requires=("magic", "rare"), removes=1, min_mods=1,
    ),
    Currency(
        "Divine Orb",
        "Randomises the numeric values of modifiers on an item",
        requires=("magic", "rare"), rerolls_values=True,
    ),
    Currency(
        "Fracturing Orb",
        "Fracture a random modifier on a rare item with at least 4 modifiers, locking it in place.",
        requires=("rare",), min_mods=4, fractures=True,
        note="Desecrated mods cannot be fractured (community guides, not poe2db).",
    ),
    Currency(
        "Vaal Orb",
        "Modifies an item unpredictably and Corrupts it",
        requires=("normal", "magic", "rare"), corrupts=True, random_outcome=True,
    ),
    Currency(
        "Orb of Chance",
        "Unpredictably either upgrades a Normal item to Unique rarity or destroys it",
        requires=("normal",), random_outcome=True,
    ),
    Currency(
        "Hinekora's Lock",
        "Allows an item to foresee the result of the next Currency item used on it. "
        "Modifying the item in any way removes the ability to foresee",
        requires=("normal", "magic", "rare"),
    ),
    Currency(
        "Artificer's Orb",
        "Adds an Augment Socket to a Martial Weapon, wand, staff or Armour",
        requires=("normal", "magic", "rare"),
        note="Socket count is capped by the base's socketLimit (Bases/*.lua).",
    ),
    Currency(
        "Armourer's Scrap",
        "Improves the quality of an armour",
        requires=("normal", "magic", "rare"),
    ),
    Currency(
        "Blacksmith's Whetstone",
        "Improves the quality of a martial weapon",
        requires=("normal", "magic", "rare"),
    ),
    Currency(
        "Arcanist's Etcher",
        "Improves the quality of a wand, staff or sceptre",
        requires=("normal", "magic", "rare"),
    ),
]


def _omen(name: str, effect: str, target: str, **kw) -> Omen:
    prefix = "While this item is active in your inventory "
    return Omen(name, prefix + effect, target, **kw)


OMENS: list[Omen] = [
    _omen("Omen of Sinistral Exaltation",
          "your next Exalted Orb will add only prefix modifiers",
          "Exalted Orb", side="prefix"),
    _omen("Omen of Dextral Exaltation",
          "your next Exalted Orb will add only suffix modifiers",
          "Exalted Orb", side="suffix"),
    _omen("Omen of Greater Exaltation",
          "your next Exalted Orb will add two random modifiers",
          "Exalted Orb", adds=2),
    _omen("Omen of Homogenising Exaltation",
          "your next Exalted Orb will add a Modifier of the same type as an existing "
          "Modifier on the Item",
          "Exalted Orb", same_type_as_existing=True),
    _omen("Omen of Catalysing Exaltation",
          "your next Exalted Orb will consume all Catalyst Quality to increase the chance "
          "of the corresponding type of Modifier",
          "Exalted Orb", note="Biases weights; not modelled without mod weights."),
    _omen("Omen of Sinistral Coronation",
          "your next Regal Orb will add only prefix modifiers",
          "Regal Orb", side="prefix"),
    _omen("Omen of Dextral Coronation",
          "your next Regal Orb will add only suffix modifiers",
          "Regal Orb", side="suffix"),
    _omen("Omen of Homogenising Coronation",
          "your next Regal Orb will add a Modifier of the same type as an existing "
          "Modifier on the Item",
          "Regal Orb", same_type_as_existing=True),
    _omen("Omen of Sinistral Erasure",
          "your next Chaos Orb will remove only prefix modifiers",
          "Chaos Orb", side="prefix",
          note="Text restricts only the removal (our reading); the added mod side is unstated."),
    _omen("Omen of Dextral Erasure",
          "your next Chaos Orb will remove only suffix modifiers",
          "Chaos Orb", side="suffix",
          note="Text restricts only the removal (our reading); the added mod side is unstated."),
    _omen("Omen of Whittling",
          "your next Chaos Orb will remove the lowest level modifier",
          "Chaos Orb", removes_lowest_level=True),
    _omen("Omen of Sinistral Annulment",
          "your next Orb of Annulment will remove only prefix modifiers",
          "Orb of Annulment", side="prefix"),
    _omen("Omen of Dextral Annulment",
          "your next Orb of Annulment will remove only suffix modifiers",
          "Orb of Annulment", side="suffix"),
    _omen("Omen of Greater Annulment",
          "your next Orb of Annulment will remove two modifiers",
          "Orb of Annulment", removes=2),
    _omen("Omen of Light",
          "your next Orb of Annulment will remove only Desecrated modifiers",
          "Orb of Annulment"),
    _omen("Omen of Sinistral Alchemy",
          "your next Orb of Alchemy will result in the maximum number of prefix modifiers",
          "Orb of Alchemy", side="prefix"),
    _omen("Omen of Dextral Alchemy",
          "your next Orb of Alchemy will result in the maximum number of suffix modifiers",
          "Orb of Alchemy", side="suffix"),
    _omen("Omen of Corruption",
          "your next Vaal Orb will always result in change",
          "Vaal Orb"),
    _omen("Omen of the Blessed",
          "your next Divine Orb will only reroll Implicit Modifiers",
          "Divine Orb"),
    _omen("Omen of Sanctification",
          "your next Divine Orb used on a Rare item will Sanctify it",
          "Divine Orb"),
    _omen("Omen of Chance",
          "your next Orb of Chance will not destroy the Item",
          "Orb of Chance"),
    _omen("Omen of the Ancients",
          "your next Orb of Chance will upgrade the Item to a random Unique of the same "
          "Item Class",
          "Orb of Chance"),
    _omen("Omen of Sinistral Crystallisation",
          "your next Perfect or Corrupted Essence will remove only Prefix modifiers",
          "Perfect or Corrupted Essence", side="prefix"),
    _omen("Omen of Dextral Crystallisation",
          "your next Perfect or Corrupted Essence will remove only Suffix modifiers",
          "Perfect or Corrupted Essence", side="suffix"),
    _omen("Omen of Sinistral Necromancy",
          "your next Desecration attempt will add only prefix modifiers",
          "Desecration", side="prefix"),
    _omen("Omen of Dextral Necromancy",
          "your next Desecration attempt will add only suffix modifiers",
          "Desecration", side="suffix"),
    _omen("Omen of Abyssal Echoes",
          "the next time you reveal Desecrated modifiers you can reroll the options once",
          "Desecration"),
    _omen("Omen of the Sovereign",
          "your next Weapon or Jewellery Desecration attempt will guarantee a random "
          "Ulaman modifier",
          "Desecration"),
    _omen("Omen of the Liege",
          "your next Weapon or Jewellery Desecration attempt will guarantee a random "
          "Amanamu modifier",
          "Desecration"),
    _omen("Omen of the Blackblooded",
          "your next Weapon or Jewellery Desecration attempt will guarantee a random "
          "Kurgal modifier",
          "Desecration"),
    _omen("Omen of Putrefaction",
          "your next Desecration attempt will replace all modifiers on the item creating "
          "an item with up to 6 Unrevealed modifiers and Corrupting the item",
          "Desecration"),
]

ESSENCES: list[EssenceTier] = [
    EssenceTier(
        ("Lesser", "", "Greater"),
        "Upgrades a Magic item to a Rare item, adding a guaranteed modifier",
        requires=("magic",), result="rare", adds=1, removes=0,
    ),
    EssenceTier(
        ("Perfect", "Corrupted"),
        "Removes a random modifier and augments a Rare item with a new guaranteed modifier",
        requires=("rare",), result="rare", adds=1, removes=1,
    ),
]

CURRENCY_BY_NAME = {c.name: c for c in CURRENCIES}
OMEN_BY_NAME = {o.name: o for o in OMENS}
OMEN_TARGETS = {*CURRENCY_BY_NAME, "Perfect or Corrupted Essence", "Desecration"}
