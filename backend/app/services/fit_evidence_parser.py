import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from app.schemas.fit_evidence import EvidenceFact, EvidenceSource
from app.utils.garment_vocabulary import ITEM_ROLE

NOTE_KINDS = {
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
        match = re.fullmatch(r"\s*(?:(\d+(?:\.\d+)?)%\s*)?([^\d%]+?)\s*", part)
        if not match:
            return []
        percent, name = match.groups()
        if not normalize(name) or (percent is not None and Decimal(percent) > 100):
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
    locator: dict
    sources: dict[str, EvidenceSource] = field(default_factory=dict)
    facts: list[EvidenceFact] = field(default_factory=list)
    notes_truncated: bool = False

    def add(self, source_field, kind, raw, origin):
        source_id = f"{self.key}:{source_field}"
        text = raw if isinstance(raw, str) else ""
        bounded = 0 < len(text) <= 300
        self.sources[source_id] = EvidenceSource(
            **self.locator,
            field=source_field,
            excerpt=text if bounded else None,
            truncated=len(text) > 300,
        )
        if kind is None or raw is None or (isinstance(raw, str) and not raw.strip()):
            return
        normalized = normalize(text) if bounded else ""
        valid = bool(normalized)
        if kind == "rating":
            valid &= normalized in {"love", "like", "mixed", "dislike"}
        if kind == "size":
            valid &= len(text) <= 32
        if kind == "type":
            valid &= normalized in ITEM_ROLE
        values = fibers(text) if kind == "fiber" and valid else []
        if kind != "fiber" and valid:
            values = [{"label" if kind == "rating" else "normalized": normalized}]
        conflict = (
            kind == "fiber" and sum(Decimal(value["percent"] or "0") for value in values) > 100
        )
        for index, value in enumerate(values or [None]):
            self.facts.append(
                EvidenceFact(
                    fact_id=f"{source_id}:{kind}:{index}",
                    source_id=source_id,
                    kind=kind,
                    origin=origin,
                    value=value,
                    status="conflict" if conflict else "known" if value is not None else "unknown",
                )
            )

    def of_kind(self, kind):
        return [fact for fact in self.facts if fact.kind == kind]

    def values(self, kind):
        return {
            fact.value.get("normalized", fact.value.get("label"))
            for fact in self.of_kind(kind)
            if fact.value is not None and ("normalized" in fact.value or "label" in fact.value)
        }

    def composition(self):
        facts = self.of_kind("fiber")
        if not facts or any(fact.status != "known" for fact in facts):
            return None
        components = {
            fact.value["name"]: fact.value["percent"]
            for fact in facts
            if fact.value["percent"] is not None
        }
        names = {fact.value["name"] for fact in facts}
        if set(components) != names or sum(map(Decimal, components.values())) != 100:
            return None
        return sorted(components.items())

    def resolve(self):
        for kind in ("brand", "line", "size", "type", "color", "rating", "fiber", "size_rule"):
            facts = [fact for fact in self.of_kind(kind) if fact.value is not None]
            conflict = len(self.values(kind)) > 1
            if kind == "fiber":
                groups = {}
                for fact in facts:
                    groups.setdefault(fact.source_id, {})[fact.value["name"]] = fact.value[
                        "percent"
                    ]
                for first in groups.values():
                    for second in groups.values():
                        conflict |= not bool(first.keys() & second.keys())
                        conflict |= any(
                            first[name] is not None
                            and second[name] is not None
                            and first[name] != second[name]
                            for name in first.keys() & second.keys()
                        )
                        conflict |= (
                            sum(Decimal(value or "0") for value in first.values()) == 100
                            and sum(Decimal(value or "0") for value in second.values()) == 100
                            and first != second
                        )
            if conflict or any(fact.status == "conflict" for fact in facts):
                for fact in facts:
                    fact.status = "conflict"
                    fact.conflict_ids = [other.fact_id for other in facts if other != fact]
        return self


def parse_item(item) -> EvidenceRecord:
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
        ("material_detail", "fiber"),
        ("fit", "feel"),
    ):
        if name in tags:
            record.add(f"tags.{name}", kind, tags[name], "stored_tag")
    if "material_detail" in tags:
        record.add("tags.material_detail.excerpt", "feel", tags["material_detail"], "stored_tag")
    notes = item.notes if isinstance(item.notes, str) else ""
    record.notes_truncated = len(notes) > 4096
    scanned = notes[:4096]
    entries = re.split(r"[|;\n]", scanned)
    if record.notes_truncated:
        entries = entries[:-1]
    for index, entry in enumerate(entries):
        key, separator, value = entry.partition(":")
        key = normalize(key)
        if separator and key in NOTE_KINDS:
            record.add(f"notes.{key}:{index}", NOTE_KINDS[key], value.strip(), "explicit_note")
    return record.resolve()


def parse_row(profile, section, index, row):
    key = f"{section}:{index}"
    record = EvidenceRecord(
        f"profile:{profile.rev}:{key}", {"profile_rev": profile.rev, "row_key": key}
    )
    for name, value in row.items():
        record.add(
            name, name if name in {"brand", "line", "size"} else "feel", value, "profile_row"
        )
    return record.resolve()
