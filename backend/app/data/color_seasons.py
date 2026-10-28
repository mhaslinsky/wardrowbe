# Source: https://theconceptwardrobe.com/colour-analysis-comprehensive-guides/complete-seasonal-guides
# Approximate mappings to broad app colors. Shades within a color can differ.
# ColorWise Deep/Cool/Clear correspond to Dark/True/Bright in the source.
from typing import Literal

ColorSeason = Literal[
    "deep-winter",
    "cool-winter",
    "clear-winter",
    "deep-autumn",
    "warm-autumn",
    "soft-autumn",
    "light-spring",
    "warm-spring",
    "clear-spring",
    "light-summer",
    "cool-summer",
    "soft-summer",
]

COLOR_SEASONS = {
    "deep-winter": {
        "best": ["black", "navy", "burgundy", "purple", "white"],
        "avoid": ["orange", "tan", "beige", "gold"],
    },
    "cool-winter": {
        "best": ["black", "white", "navy", "blue", "purple", "silver"],
        "avoid": ["orange", "brown", "tan", "gold", "cream"],
    },
    "clear-winter": {
        "best": ["black", "white", "blue", "red", "pink", "silver"],
        "avoid": ["olive", "beige", "tan", "brown"],
    },
    "deep-autumn": {
        "best": ["brown", "olive", "burgundy", "green", "orange", "gold"],
        "avoid": ["light-blue", "pink", "silver", "white"],
    },
    "warm-autumn": {
        "best": ["brown", "olive", "orange", "gold", "cream", "tan"],
        "avoid": ["black", "white", "pink", "silver", "light-blue"],
    },
    "soft-autumn": {
        "best": ["olive", "beige", "tan", "cream", "brown"],
        "avoid": ["black", "white", "red", "blue"],
    },
    "light-spring": {
        "best": ["cream", "beige", "pink", "yellow", "light-blue"],
        "avoid": ["black", "burgundy", "navy", "brown"],
    },
    "warm-spring": {
        "best": ["cream", "yellow", "gold", "orange", "green", "tan"],
        "avoid": ["black", "gray", "burgundy", "silver"],
    },
    "clear-spring": {
        "best": ["red", "yellow", "green", "blue", "cream"],
        "avoid": ["gray", "olive", "beige", "burgundy"],
    },
    "light-summer": {
        "best": ["light-blue", "pink", "gray", "silver"],
        "avoid": ["black", "brown", "orange", "gold"],
    },
    "cool-summer": {
        "best": ["blue", "light-blue", "pink", "purple", "navy", "gray", "silver"],
        "avoid": ["orange", "gold", "brown", "yellow"],
    },
    "soft-summer": {
        "best": ["gray", "light-blue", "pink", "purple", "navy"],
        "avoid": ["black", "white", "orange", "yellow", "gold"],
    },
}

NEAR_FACE_TYPES = frozenset(
    {
        "shirt",
        "t-shirt",
        "top",
        "dress",
        "jumpsuit",
        "jacket",
        "coat",
        "sweater",
        "hoodie",
        "blazer",
        "suit",
        "vest",
        "cardigan",
        "overshirt",
        "polo",
        "blouse",
        "tank-top",
        "tie",
        "hat",
        "scarf",
    }
)


def stored_color_season(style_profile: dict | None) -> str | None:
    season = (style_profile or {}).get("color_season")
    return season if isinstance(season, str) and season in COLOR_SEASONS else None
