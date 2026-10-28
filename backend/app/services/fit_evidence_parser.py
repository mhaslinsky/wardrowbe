import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations

from app.models.item import ClothingItem
from app.schemas.fit_evidence import EvidenceFact, EvidenceSource, FactKind, FactOrigin
from app.schemas.fit_profile import FitProfile
from app.schemas.item import USER_TAG_KEYS
from app.utils.garment_vocabulary import ITEM_ROLE, canonical_color

NOTE_KINDS: dict[str, FactKind] = {
    "rating": "rating",
    "size": "size",
    "fabric": "fiber",
    "line": "line",
    "color": "color",
    **dict.fromkeys(("fabric detail", "fit", "warmth", "works", "doesnt", "style"), "feel"),
}


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def fibers(value: str) -> list[dict[str, str | None]]:
    components = []
    for part in re.split(r"[,/+]", value):
        match = re.fullmatch(r"\s*(?:(\d+(?:\.\d{1,2})?)%\s*)?([^\d%]+?)\s*", part)
        if not match:
            return []
        percent, name = match.groups()
        if not 0 < len(normalize(name)) <= 300 or (
            percent is not None and not 0 < Decimal(percent) <= 100
        ):
            return []
        percent = format(Decimal(percent), "f") if percent is not None else None
        components.append(
            {
                "name": normalize(name),
                "percent": percent.rstrip("0").rstrip(".")
                if percent and "." in percent
                else percent,
            }
        )
    if len({component["name"] for component in components}) != len(components):
        return []
    return sorted(components, key=lambda component: component["name"])


@dataclass
class EvidenceRecord:
    key: str
    locator: dict[str, str]
    sources: dict[str, EvidenceSource] = field(default_factory=dict)
    facts: list[EvidenceFact] = field(default_factory=list)
    notes_truncated: bool = False

    def add(
        self, source_field: str, kind: FactKind | None, raw: object, origin: FactOrigin
    ) -> None:
        source_id = f"{self.key}:{source_field}"
        text = raw.strip() if isinstance(raw, str) else ""
        normalized = normalize(text)
        if kind == "color":
            normalized = canonical_color(normalized)
        bounded = 0 < len(text) <= 300 and len(normalized) <= 300
        self.sources[source_id] = EvidenceSource(
            **self.locator,
            field=source_field,
            excerpt=text if bounded else None,
            truncated=len(text) > 300 or len(normalized) > 300,
        )
        if kind is None or raw is None or (isinstance(raw, str) and not raw.strip()):
            return
        valid = bounded and bool(normalized)
        if kind == "rating":
            valid &= normalized in {"love", "like", "mixed", "dislike"}
        if kind == "size":
            valid &= len(text) <= 32 and len(normalized) <= 32
        if kind == "type":
            valid &= normalized in ITEM_ROLE
        values = fibers(text) if kind == "fiber" and valid else []
        if kind != "fiber" and valid:
            values = [{"label" if kind == "rating" else "normalized": normalized}]
        total = sum(Decimal(value["percent"] or "0") for value in values) if kind == "fiber" else 0
        conflict = total > 100 or (
            total == 100 and any(value["percent"] is None for value in values)
        )
        for index, value in enumerate(values or [None]):
            self.facts.append(
                EvidenceFact(
                    fact_id=f"{source_id}:{kind}:{index}",
                    source_id=source_id,
                    kind=kind,
                    origin=origin,
                    producer="user"
                    if origin in {"explicit_note", "profile_row"}
                    or source_field.removeprefix("tags.") in USER_TAG_KEYS
                    else "unknown",
                    value=value,
                    status="conflict" if conflict else "known" if value is not None else "unknown",
                )
            )

    def of_kind(self, kind: str) -> list[EvidenceFact]:
        return [fact for fact in self.facts if fact.kind == kind]

    def values(self, kind: str) -> set[str]:
        values = set()
        for fact in self.of_kind(kind):
            if fact.value is not None:
                value = fact.value.get("normalized", fact.value.get("label"))
                if value is not None:
                    values.add(value)
        return values

    def fiber_groups(self) -> dict[str, dict[str, str | None]]:
        groups = {}
        for fact in self.of_kind("fiber"):
            if fact.value is not None:
                groups.setdefault(fact.source_id, {})[fact.value["name"]] = fact.value["percent"]
        return groups

    def composition(self) -> list[tuple[str, str]] | None:
        facts = self.of_kind("fiber")
        if not facts or any(fact.status != "known" for fact in facts):
            return None
        for group in self.fiber_groups().values():
            complete = {name: percent for name, percent in group.items() if percent is not None}
            if len(complete) == len(group) and sum(map(Decimal, complete.values())) == 100:
                return sorted(complete.items())
        return None

    def resolve(self) -> "EvidenceRecord":
        for kind in ("brand", "line", "size", "type", "color", "rating", "fiber"):
            facts = self.of_kind(kind)
            conflict = len(self.values(kind)) > 1 or (
                kind != "fiber"
                and any(fact.value is None for fact in facts)
                and any(fact.value is not None for fact in facts)
            )
            if kind == "fiber":
                for first, second in combinations(self.fiber_groups().values(), 2):
                    subset = (
                        all(percent is None for percent in first.values())
                        and first.keys() <= second.keys()
                    ) or (
                        all(percent is None for percent in second.values())
                        and second.keys() <= first.keys()
                    )
                    conflict |= first != second and not subset
            if conflict or any(fact.status == "conflict" for fact in facts):
                for fact in facts:
                    fact.status = "conflict"
                    fact.conflict_ids = [other.fact_id for other in facts if other != fact]
        return self


def parse_item(item: ClothingItem) -> EvidenceRecord:
    record = EvidenceRecord(f"item:{item.id}", {"item_id": str(item.id)})
    for name, kind in (
        ("brand", "brand"),
        ("type", "type"),
        ("primary_color", "color"),
        ("material", "fiber"),
        ("subtype", None),
    ):
        record.add(name, kind, getattr(item, name, None), "stored_tag")
    tags = item.tags if isinstance(item.tags, dict) else {}
    for name, kind in (
        ("size", "size"),
        ("material", "fiber"),
        ("fit", "feel"),
    ):
        if name in tags:
            record.add(f"tags.{name}", kind, tags[name], "stored_tag")
    if "material_detail" in tags:
        detail = tags["material_detail"]
        components = fibers(detail) if isinstance(detail, str) else []
        kind = (
            "fiber"
            if components and all(component["percent"] is not None for component in components)
            else "feel"
        )
        record.add("tags.material_detail", kind, detail, "stored_tag")
    notes = item.notes if isinstance(item.notes, str) else ""
    record.notes_truncated = len(notes) > 4096
    scanned = notes[:4096]
    entries = re.split(r"[|;\n]", scanned)
    if record.notes_truncated and not (scanned.endswith(("|", ";", "\n")) or notes[4096] in "|;\n"):
        entries = entries[:-1]
    for index, entry in enumerate(entries):
        key, separator, value = entry.partition(":")
        key = normalize(key)
        if separator and key in NOTE_KINDS:
            record.add(f"notes.{key}:{index}", NOTE_KINDS[key], value.strip(), "explicit_note")
    return record.resolve()


def parse_row(
    profile: FitProfile, section: str, index: int, row: dict[str, object]
) -> EvidenceRecord:
    key = f"{section}:{index}"
    record = EvidenceRecord(
        f"profile:{profile.rev}:{key}", {"profile_rev": profile.rev, "row_key": key}
    )
    for name, value in row.items():
        kind = {"brand": "brand", "line": "line", "size": "size"}.get(name)
        if section == "brand_sizes" and name == "note":
            kind = "feel"
        record.add(name, kind, value, "profile_row")
    return record.resolve()
