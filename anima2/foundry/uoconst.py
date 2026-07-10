"""UO domain constants for the anima2 Foundry kernel — ported near-verbatim
from `../anima/foundry/kernel/uoconst.py` (v1's own module docstring: "facts
about the Ultima Online protocol and ruleset ... not agent behavior. They live
in the kernel because fitness/descriptor computation depends on them and they
must not be mutator-editable.").

Only the subset `foundry/fitness.py`/`foundry/trajectory.py` actually need is
ported: `SKILL_NAMES` (id -> display name, used the other direction too — see
`trajectory.py`'s `_NAME_TO_ID` — to turn a `[Get Skills.<Name>.Base` reply,
queried BY NAME, back into the id-keyed shape v1's wire-parsed `SkillStat`
used), `SKILL_CATEGORY` (profession_focus grouping, for
`TrajectorySummary.profession_skill_gains`), `ITEM_VALUES`/
`ITEM_VALUE_DEFAULT` (produce_term gold-equivalent valuation), and
`LAYER_BACKPACK`. v1's packet-id tables (`DIR_DELTA`, `ACTION_GROUP`, the
`CS_*` constants) are **not** ported — anima2 has no packet stream; see
`trajectory.py`'s own `_ACTION_GROUP` (keyed by this project's `Action.type`
strings, not a wire packet id) for the direct analog and why it lives there
instead of here.
"""

from __future__ import annotations

# --- Skills -----------------------------------------------------------------
# Standard UO skill id -> name (0..57, UOR-era + later). Ported verbatim from
# v1 `foundry/kernel/uoconst.py`.
SKILL_NAMES: dict[int, str] = {
    0: "Alchemy", 1: "Anatomy", 2: "Animal Lore", 3: "Item ID", 4: "Arms Lore",
    5: "Parrying", 6: "Begging", 7: "Blacksmithy", 8: "Bowcraft", 9: "Peacemaking",
    10: "Camping", 11: "Carpentry", 12: "Cartography", 13: "Cooking",
    14: "Detect Hidden", 15: "Discordance", 16: "Eval Int", 17: "Healing",
    18: "Fishing", 19: "Forensics", 20: "Herding", 21: "Hiding", 22: "Provocation",
    23: "Inscription", 24: "Lockpicking", 25: "Magery", 26: "Resist Spells",
    27: "Tactics", 28: "Snooping", 29: "Musicianship", 30: "Poisoning",
    31: "Archery", 32: "Spirit Speak", 33: "Stealing", 34: "Tailoring",
    35: "Animal Taming", 36: "Taste ID", 37: "Tinkering", 38: "Tracking",
    39: "Veterinary", 40: "Swordsmanship", 41: "Mace Fighting", 42: "Fencing",
    43: "Wrestling", 44: "Lumberjacking", 45: "Mining", 46: "Meditation",
    47: "Stealth", 48: "Remove Trap", 49: "Necromancy", 50: "Focus",
    51: "Chivalry", 52: "Bushido", 53: "Ninjitsu", 54: "Spellweaving",
    55: "Mysticism", 56: "Imbuing", 57: "Throwing",
}

# Behavior-descriptor profession categories (v1 FOUNDRY.md §4 profession_focus)
# — not yet consumed by a descriptor.py here (Phase 5 item 3), but
# `TrajectorySummary.profession_skill_gains` (fitness's own `has_profession`
# gate) needs the grouping now, so it's ported alongside.
GATHERING = "GATHERING"
CRAFTING = "CRAFTING"
COMBAT = "COMBAT"
MAGIC = "MAGIC"
BARD_SOCIAL = "BARD-SOCIAL"
THIEF_STEALTH = "THIEF-STEALTH"
NONE = "NONE"

# Map skill id -> profession category. Uncategorized skills map to nothing here
# and do not contribute to profession_focus (they are not livelihood-defining).
SKILL_CATEGORY: dict[int, str] = {
    # Gathering
    18: GATHERING, 44: GATHERING, 45: GATHERING,
    # Crafting
    0: CRAFTING, 7: CRAFTING, 8: CRAFTING, 11: CRAFTING, 12: CRAFTING,
    13: CRAFTING, 34: CRAFTING, 37: CRAFTING, 56: CRAFTING,
    # Combat
    1: COMBAT, 5: COMBAT, 17: COMBAT, 27: COMBAT, 31: COMBAT, 40: COMBAT,
    41: COMBAT, 42: COMBAT, 43: COMBAT, 51: COMBAT, 52: COMBAT, 57: COMBAT,
    # Magic
    16: MAGIC, 23: MAGIC, 25: MAGIC, 26: MAGIC, 32: MAGIC, 46: MAGIC,
    49: MAGIC, 54: MAGIC, 55: MAGIC,
    # Bard / social
    6: BARD_SOCIAL, 9: BARD_SOCIAL, 15: BARD_SOCIAL, 22: BARD_SOCIAL, 29: BARD_SOCIAL,
    # Thief / stealth
    21: THIEF_STEALTH, 24: THIEF_STEALTH, 28: THIEF_STEALTH, 30: THIEF_STEALTH,
    33: THIEF_STEALTH, 47: THIEF_STEALTH, 48: THIEF_STEALTH, 53: THIEF_STEALTH,
}

# --- Item valuation ---------------------------------------------------------
# Graphic id -> rough gold value, for produce_term (locked weight W_PRODUCE in
# fitness.py). Ported verbatim from v1 (same conservative starting table;
# unknown graphics default to ITEM_VALUE_DEFAULT).
ITEM_VALUES: dict[int, int] = {
    0x1BDD: 3,   # Log
    0x1BE0: 3,   # Log (variant)
    0x1BD7: 3,   # Board
    0x19B7: 5,   # Ore (small pile)
    0x19B8: 5,   # Ore
    0x19B9: 5,   # Ore
    0x19BA: 5,   # Ore (large pile)
    0x1BEF: 6,   # Ingot
    0x1BF0: 6,   # Ingot
    0x1BF1: 6,   # Ingot
    0x1BF2: 6,   # Iron ingot
    0x0F8F: 0,   # (placeholder) gem — keep 0 until valued
    0x0F51: 10, 0x0F52: 10,    # dagger
    0x1441: 24, 0x1440: 24,    # cutlass
    0x13FF: 33, 0x1400: 33,    # katana
    0x1401: 30,                # kryss
    0x0F61: 28, 0x0F62: 28,    # longsword
    0x13B6: 28, 0x13B7: 28,    # scimitar
    0x0F5E: 28, 0x0F5F: 28,    # broadsword
    0x13B9: 30, 0x13BA: 30,    # viking sword
    0x26C1: 35, 0x26CB: 35,    # crescent blade
    0x13EB: 25, 0x13F0: 35, 0x13EE: 30, 0x13EC: 40,  # ringmail set
}
ITEM_VALUE_DEFAULT = 1

# --- Layers -----------------------------------------------------------------
LAYER_BACKPACK = 0x15  # 21
