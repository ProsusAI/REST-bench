# Copyright © 2026 MIH AI B.V.
# Licensed under the Apache License, Version 2.0.
# See LICENSE file in the project root for full license information.

"""
Static configuration for the digital twin: the restaurant identity,
the menu, the floor plan, and the staff roster.

Everything here is deterministic — only the operational layer uses RNG.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal

from .distributions import zipf_weights


# ---------------------------------------------------------------------------
# Restaurant identity
# ---------------------------------------------------------------------------
RESTAURANT = {
    "id": "rest_001",
    "name": "The Rotterdam Table",
    "concept": "casual full-service bistro",
    "cuisine": "modern European",
    "address": "Witte de Withstraat 42, 3012 BR Rotterdam, NL",
    "tax_rate": 0.09,          # NL low VAT on food
    "drink_tax_rate": 0.21,     # NL high VAT on alcohol & soft drinks
    "service_charge_rate": 0.0, # tipping-based, no auto-gratuity under 6
    "auto_gratuity_party_threshold": 6,
    "auto_gratuity_rate": 0.18,
    "open_hours": {             # day index 0=Mon ... 6=Sun → (open, close) in 24h
        0: (11.0, 23.0),
        1: (11.0, 23.0),
        2: (11.0, 23.0),
        3: (11.0, 23.0),
        4: (11.0, 24.0),
        5: (11.0, 24.0),
        6: (11.0, 22.0),
    },
    "currency": "EUR",
}


# ---------------------------------------------------------------------------
# Floor plan
# ---------------------------------------------------------------------------
@dataclass
class Table:
    id: str
    seats: int
    location: str  # "window", "center", "bar", "patio", "booth"


TABLES: list[Table] = [
    # 2-tops
    Table("T01", 2, "window"),
    Table("T02", 2, "window"),
    Table("T03", 2, "window"),
    Table("T04", 2, "center"),
    Table("T05", 2, "center"),
    Table("T06", 2, "center"),
    Table("T07", 2, "booth"),
    Table("T08", 2, "booth"),
    # 4-tops
    Table("T09", 4, "center"),
    Table("T10", 4, "center"),
    Table("T11", 4, "window"),
    Table("T12", 4, "booth"),
    Table("T13", 4, "booth"),
    Table("T14", 4, "patio"),
    Table("T15", 4, "patio"),
    # 6-tops
    Table("T16", 6, "center"),
    Table("T17", 6, "booth"),
    # 8-top (for larger parties)
    Table("T18", 8, "center"),
    # bar seating
    Table("B01", 1, "bar"),
    Table("B02", 1, "bar"),
    Table("B03", 1, "bar"),
    Table("B04", 1, "bar"),
    Table("B05", 1, "bar"),
    Table("B06", 1, "bar"),
]


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------
# Prices reflect plausible Rotterdam casual-bistro pricing (EUR).
# Popularity rank within category is assigned implicitly by list order —
# first item in each category is the most popular. The simulation maps
# rank → Zipf weight.

MENU_ITEMS = [
    # id, name, category, price, cost_to_restaurant
    ("M01", "Steak frites",                    "main",       24.50, 8.50),
    ("M02", "Burger & fries",                  "main",       18.00, 5.25),
    ("M03", "Grilled salmon",                  "main",       26.00, 9.80),
    ("M04", "Risotto ai funghi",               "main",       19.50, 4.10),
    ("M05", "Chicken schnitzel",               "main",       21.00, 6.00),
    ("M06", "Seafood pasta",                   "main",       23.00, 7.50),
    ("M07", "Vegan buddha bowl",               "main",       17.50, 3.80),
    ("M08", "Lamb shank",                      "main",       28.50, 10.00),
    ("M09", "Beef stroganoff",                 "main",       22.00, 7.20),
    ("M10", "Duck breast",                     "main",       29.00, 11.00),
    ("A01", "Bitterballen",                    "appetizer",   8.50, 2.10),
    ("A02", "Burrata & tomato",                "appetizer",  12.00, 4.00),
    ("A03", "Carpaccio",                       "appetizer",  13.50, 4.80),
    ("A04", "Soup of the day",                 "appetizer",   7.50, 1.60),
    ("A05", "Calamari",                        "appetizer",  11.00, 3.40),
    ("A06", "Bruschetta",                      "appetizer",   9.00, 2.20),
    ("A07", "Charcuterie board",               "appetizer",  16.00, 5.80),
    ("A08", "Oysters (6)",                     "appetizer",  18.00, 8.00),
    ("S01", "Fries",                           "side",        4.50, 0.80),
    ("S02", "Side salad",                      "side",        5.50, 1.30),
    ("S03", "Roasted vegetables",              "side",        6.50, 1.80),
    ("S04", "Mashed potatoes",                 "side",        5.00, 1.00),
    ("S05", "Bread & butter",                  "side",        4.00, 0.60),
    ("D01", "Chocolate fondant",               "dessert",     8.50, 2.00),
    ("D02", "Crème brûlée",                    "dessert",     7.50, 1.50),
    ("D03", "Cheesecake",                      "dessert",     8.00, 1.80),
    ("D04", "Tiramisu",                        "dessert",     8.00, 2.00),
    ("D05", "Ice cream (3 scoops)",            "dessert",     6.50, 1.20),
    ("D06", "Fruit sorbet",                    "dessert",     6.00, 1.40),
    ("B01", "House red (glass)",               "drink",       6.50, 1.50),
    ("B02", "House white (glass)",             "drink",       6.50, 1.50),
    ("B03", "Heineken",                        "drink",       4.50, 0.90),
    ("B04", "IPA",                             "drink",       5.50, 1.30),
    ("B05", "Sparkling water",                 "drink",       3.50, 0.40),
    ("B06", "Still water",                     "drink",       3.00, 0.30),
    ("B07", "Cola",                            "drink",       3.50, 0.50),
    ("B08", "Coffee",                          "drink",       3.00, 0.35),
    ("B09", "Tea",                             "drink",       3.00, 0.25),
    ("B10", "Fresh juice",                     "drink",       4.50, 1.10),
    ("B11", "Cocktail of the day",             "drink",      11.00, 2.80),
    ("B12", "Bottle house red",                "drink",      28.00, 7.00),
]


def build_menu() -> list[dict]:
    """Expand MENU_ITEMS with Zipf popularity weights by category."""
    by_cat: dict[str, list] = {}
    for row in MENU_ITEMS:
        by_cat.setdefault(row[2], []).append(row)
    menu = []
    for cat, items in by_cat.items():
        weights = zipf_weights(len(items))
        for (id_, name, _, price, cost), w in zip(items, weights):
            menu.append({
                "id": id_,
                "name": name,
                "category": cat,
                "price_eur": price,
                "food_cost_eur": cost,
                "popularity_weight": float(w),
                "margin_eur": round(price - cost, 2),
                "margin_pct": round((price - cost) / price, 3),
            })
    return menu


# ---------------------------------------------------------------------------
# Staff
# ---------------------------------------------------------------------------
@dataclass
class StaffMember:
    id: str
    name: str
    role: Literal["server", "chef", "sous_chef", "line_cook",
                  "bartender", "host", "manager", "dishwasher", "busser"]
    hourly_wage_eur: float
    shift_pattern: list[tuple[int, float, float]] = field(default_factory=list)
    # shift_pattern entries: (day_of_week, start_hour, end_hour)


STAFF: list[StaffMember] = [
    # Management (all day every weekday)
    StaffMember("E001", "Marieke de Vries", "manager",     32.00,
                [(d, 11.0, 23.0) for d in range(7)]),
    # Kitchen
    StaffMember("E002", "Thomas Bakker",    "chef",        28.00,
                [(d, 10.0, 23.0) for d in range(7)]),
    StaffMember("E003", "Lisa Jansen",      "sous_chef",   22.00,
                [(d, 10.0, 23.0) for d in (0,1,2,3,4)]),
    StaffMember("E004", "Ahmed Hassan",     "sous_chef",   22.00,
                [(d, 14.0, 23.5) for d in (4,5,6)]),
    StaffMember("E005", "Peter Smit",       "line_cook",   16.50,
                [(d, 10.0, 16.0) for d in range(7)]),
    StaffMember("E006", "Sophie van Dijk",  "line_cook",   16.50,
                [(d, 16.0, 23.5) for d in range(7)]),
    StaffMember("E007", "Karim El-Amin",    "line_cook",   16.50,
                [(d, 17.0, 23.5) for d in (4,5)]),
    StaffMember("E008", "Martin Jong",      "dishwasher",  13.50,
                [(d, 11.0, 23.0) for d in range(7)]),
    # Front of house
    StaffMember("E009", "Emma Visser",      "host",        15.00,
                [(d, 11.0, 16.0) for d in range(7)]),
    StaffMember("E010", "Jeroen Bos",       "host",        15.00,
                [(d, 17.0, 23.0) for d in range(7)]),
    StaffMember("E011", "Nora Mulder",      "server",      14.00,
                [(d, 11.0, 17.0) for d in range(7)]),
    StaffMember("E012", "Lars Hendriks",    "server",      14.00,
                [(d, 11.0, 17.0) for d in range(7)]),
    StaffMember("E013", "Fatima Yildiz",    "server",      14.00,
                [(d, 16.0, 23.0) for d in range(7)]),
    StaffMember("E014", "Daan Meijer",      "server",      14.00,
                [(d, 16.0, 23.0) for d in range(7)]),
    StaffMember("E015", "Iris Kuipers",     "server",      14.00,
                [(d, 17.0, 23.5) for d in (3,4,5,6)]),
    StaffMember("E016", "Rens Post",        "server",      14.00,
                [(d, 17.0, 23.5) for d in (4,5)]),
    StaffMember("E017", "Chloé Martin",     "bartender",   16.00,
                [(d, 16.0, 23.5) for d in range(7)]),
    StaffMember("E018", "Sanne de Boer",    "busser",      12.50,
                [(d, 17.0, 23.0) for d in (3,4,5,6)]),
]


def staff_as_dicts() -> list[dict]:
    rows = []
    for s in STAFF:
        d = asdict(s)
        d["shift_pattern"] = [
            {"day_of_week": dow, "start_hour": start, "end_hour": end}
            for (dow, start, end) in s.shift_pattern
        ]
        rows.append(d)
    return rows


def tables_as_dicts() -> list[dict]:
    return [asdict(t) for t in TABLES]
