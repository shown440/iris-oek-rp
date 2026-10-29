import tomllib
from dataclasses import dataclass
from pathlib import Path

from app.transformers.normalizers import vocabulary_key

# Target columns the transformer knows how to fill from a source property.
SUPPORTED_TARGETS = frozenset(
    {
        "object_type",
        "site_name",
        "file_reference",
        "municipality_name",
        "municipality_key",
        "cadastral_district_name",
        "cadastral_district_key",
        "status",
        "area_ha",
        "eco_credits_total",
        "eco_credits_available",
        "registering_authority",
        "registered_on",
    }
)


@dataclass(frozen=True)
class AttributeMapping:
    """
    Declarative source -> target mapping (config/attribute_mapping.toml).
    """

    layer: str
    id_field: str
    sort_field: str | None
    allow_feature_id_fallback: bool
    fields: dict[str, str]
    object_types: dict[str, str]
    object_types_excluded: frozenset[str]
    status: dict[str, str]

    def source_field(self, target: str) -> str | None:
        return self.fields.get(target)

    def mapped_source_fields(self) -> set[str]:
        return {self.id_field, *self.fields.values()}


def load_mapping(path: Path) -> AttributeMapping:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)

    fields = dict(raw.get("fields", {}))
    unknown = set(fields) - SUPPORTED_TARGETS

    if unknown:
        raise ValueError(f"Unsupported target columns in {path}: {sorted(unknown)}")

    source = raw["source"]

    return AttributeMapping(
        layer=source["layer"],
        id_field=source["id_field"],
        sort_field=source.get("sort_field"),
        allow_feature_id_fallback=bool(source.get("allow_feature_id_fallback", False)),
        fields=fields,
        object_types={vocabulary_key(k): v for k, v in raw.get("object_types", {}).items()},
        object_types_excluded=frozenset(
            vocabulary_key(v) for v in raw.get("object_types_excluded", {}).get("values", [])
        ),
        status={vocabulary_key(k): v for k, v in raw.get("status", {}).items()},
    )
