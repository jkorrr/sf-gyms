"""Deterministic venue taxonomy shared by catalog import jobs.

Venue type is intentionally broader than ``gymType``. The latter preserves a
source/operator description; this field gives web and mobile clients a stable,
filterable product taxonomy.
"""

from __future__ import annotations

from typing import Any

VENUE_TYPES = (
    "traditional_gym",
    "boutique_fitness",
    "yoga_studio",
    "pilates_barre",
    "martial_arts_boxing",
    "climbing_gym",
    "gymnastics",
    "personal_training",
    "recreation_sports",
    "outdoor_fitness",
    "dance_movement",
)

OUTDOOR_STATION_NAMES = {
    "achilles stretch",
    "achillles stretch",
    "balance beam",
    "bench leg raise",
    "body curl",
    "chin up",
    "chin up bar",
    "circle body",
    "exercise area",
    "hand walk",
    "hop kick",
    "knee lift",
    "leg stretch",
    "log jumps",
    "push up",
    "sit up",
    "sit reach",
    "step up",
    "touch toes",
    "vault bar",
}


def normalized(value: Any) -> str:
    return " ".join(
        "".join(character if character.isalnum() else " " for character in str(value or "").casefold()).split()
    )


def contains_any(value: str, terms: tuple[str, ...]) -> bool:
    return any(term in value for term in terms)


def classify_venue(record: dict[str, Any]) -> str:
    """Return one primary venue type using the most specific match first."""

    name = normalized(record.get("name"))
    gym_type = normalized(record.get("gymType") or record.get("gym_type"))
    amenities = record.get("amenities") or []
    primary_activity = normalized(amenities[0]) if amenities else ""
    identity = f"{name} {gym_type}"

    if name in OUTDOOR_STATION_NAMES or contains_any(
        identity, ("outdoor exercise station", "outdoor fitness station", "fitness court"),
    ):
        return "outdoor_fitness"

    if contains_any(
        identity,
        ("climbing", "boulder", "mission cliffs", "movement san francisco"),
    ):
        return "climbing_gym"

    if primary_activity in {"boxing", "martial arts", "jiu jitsu", "kickboxing", "muay thai"} or contains_any(
        identity,
        (
            "martial arts", "boxing", "jiu jitsu", "jiujitsu", "bjj", "muay thai",
            "karate", "krav maga", "kickboxing", "taekwondo", "tae kwon", "kung fu",
            "dojo", " mma ", "wushu", "capoeira", "aikido", "judo", "hapkido",
            "kenpo", "fencing", "self defense",
        ),
    ):
        return "martial_arts_boxing"

    if contains_any(identity, ("pilates", "barre", "lagree", "bodyrok", "core40", "solidcore")):
        return "pilates_barre"

    if contains_any(identity, ("yoga", "bikram")):
        return "yoga_studio"

    if contains_any(identity, ("dance", "ballet", "zumba")):
        return "dance_movement"

    # CrossFit facilities can mention gymnastics without being gymnastics schools.
    if "crossfit" not in name and contains_any(identity, ("gymnastics", "trampoline", "acrobatics")):
        return "gymnastics"

    if contains_any(
        identity,
        (
            "orangetheory", "orange theory", "barry s", "f45", "soulcycle", "rumble",
            "row house", "cyclebar", "spin studio", "hiit and strength studio",
            "resistance training studio",
        ),
    ):
        return "boutique_fitness"

    if contains_any(
        identity,
        ("personal training", "personal trainer", "trainer facility", "stretchlab", "stretch lab"),
    ):
        return "personal_training"

    if contains_any(
        identity,
        (
            "tennis", "pickleball", "swimming", "swim club", " pool", "soccer", "bocce",
            "recreation center", "recreation and fitness", "aquatics center", "sports field",
            "athletic field", "playground", "community fitness", "community ymca",
            "community college", "university and public", "university recreation",
            "workplace fitness", "workplace open gym", "corporate wellness", "hotel fitness",
        ),
    ):
        return "recreation_sports"

    if contains_any(
        identity,
        (
            "gym", "fitness", "crossfit", "hyrox", "strength", "barbell", "powerlifting",
            "weightlifting", "athletic club", "bay club", "equinox", "crunch",
        ),
    ):
        return "traditional_gym"

    if contains_any(gym_type, ("sports centre", "sports center")):
        return "recreation_sports"

    # The OSM query only admits fitness/sports facilities. An unrecognized
    # fitness-centre record is safer in the general gym bucket than dropped.
    return "traditional_gym"


def classify_all(records: list[dict[str, Any]]) -> None:
    for record in records:
        record["venueType"] = classify_venue(record)
