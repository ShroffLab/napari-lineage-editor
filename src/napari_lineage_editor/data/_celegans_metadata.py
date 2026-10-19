from pathlib import Path
from typing import Any, Literal

from dataset_registry import Registry
from pydantic import BaseModel, create_model, model_validator


def registry(path: Path) -> Registry:
    return Registry(
        path,
        sample_metadata_model=CelegansSampleMetadata,
        representation_metadata_model=CelegansRepMetadata,
    )


class CelegansSampleMetadata(BaseModel):
    """This project's sample-level `metadata` fields, typed."""

    strain: str | None = None
    """The imaged strain's genotype/cross, e.g. `"SLS267xOH15257"`."""


class CelegansImageRepMetadata(BaseModel):
    """Fields specific to an image representation (one tiff per timepoint, or per timepoint+slice).

    A single-timepoint representation (e.g. one promoted frame from a
    longer acquisition) is just the `num_timepoints=1` case of the same
    per-timepoint-file-series fields -- there's no separate `timepoint`
    field for it. `start_z`/`end_z` are optional even here: unset for a
    plain per-timepoint 3D tiff, set for a per-timepoint-per-slice 2D tiff
    series.
    """

    file_pattern: str
    start_time: int = 0
    end_time: int
    start_z: int | None = None
    end_z: int | None = None


class CelegansLineageRepMetadata(BaseModel):
    """Fields specific to a lineage representation.

    `path` points directly at one CSV file (not a per-timepoint series) with
    fixed columns `time, cell_name, x, y, z, diameter`, and optional cell_id and parent_id columns.
    """

    has_links: bool = False
    has_names: bool = True
    time_offset: int = 0
    """Value to add to time to match up with the time range in the related image sequence"""


_SUBMODELS_BY_TYPE: dict[str, type[BaseModel]] = {
    "image": CelegansImageRepMetadata,
    "lineage": CelegansLineageRepMetadata,
}


def _flat_field_definitions() -> dict[str, tuple[type, None]]:
    """Every `_SUBMODELS_BY_TYPE` field as an `(Optional[type], None)` create_model definition.

    Flat rather than nested, so `dsr browse`'s metadata editor (which only
    renders flat key/value rows, not nested sub-models) can display and edit
    every field. Fields are made optional here regardless of their
    required-ness on the owning submodel -- `CelegansRepMetadata`'s own
    validator (`_check_fields_match_type`) enforces presence/absence by
    reconstructing and validating the submodel for `self.type`.
    """
    definitions: dict[str, tuple[type, None]] = {}
    for submodel in _SUBMODELS_BY_TYPE.values():
        for field_name, field_info in submodel.model_fields.items():
            definitions[field_name] = (field_info.annotation | None, None)
    return definitions


_FlatRepFields = create_model("_FlatRepFields", __base__=BaseModel, **_flat_field_definitions())


class CelegansRepMetadata(_FlatRepFields):  # type: ignore[valid-type,misc]
    """This project's representation-level `metadata` fields, typed.

    `type` selects which of `_SUBMODELS_BY_TYPE` must validate against this
    instance's fields -- enforced by `_check_fields_match_type`, which
    reconstructs that submodel from the subset of fields it declares (and
    requires every *other* submodel's fields to be unset).
    """

    type: Literal["image", "lineage"]

    @model_validator(mode="before")  # type: ignore[untyped-decorator]
    @classmethod
    def _fill_own_submodel_defaults(cls, data: Any) -> Any:
        """Apply `self.type`'s submodel defaults (e.g. `start_time=0`) before flat validation.

        Every field here is declared `Optional[None]` so the flat shape can
        hold either submodel's fields at once -- but that means an unset
        field would otherwise read back as `None` instead of its real
        submodel default (e.g. `CelegansImageRepMetadata.start_time`'s `0`).
        Filling defaults in here, rather than requiring callers to go
        through `.submodel()`, keeps direct attribute access like
        `metadata.start_time` correct everywhere.
        """
        if not isinstance(data, dict) or "type" not in data:
            return data
        own_submodel = _SUBMODELS_BY_TYPE.get(data["type"])
        if own_submodel is None:
            return data
        data = dict(data)
        for field_name, field_info in own_submodel.model_fields.items():
            if data.get(field_name) is None and not field_info.is_required():
                data[field_name] = field_info.default
        return data

    @model_validator(mode="after")  # type: ignore[untyped-decorator]
    def _check_fields_match_type(self) -> "CelegansRepMetadata":
        own_submodel = _SUBMODELS_BY_TYPE[self.type]
        own_fields = set(own_submodel.model_fields)
        foreign_fields = {
            field_name
            for submodel in _SUBMODELS_BY_TYPE.values()
            for field_name in submodel.model_fields
        } - own_fields
        set_foreign_fields = [f for f in foreign_fields if getattr(self, f) is not None]
        if set_foreign_fields:
            raise ValueError(
                f"type={self.type!r} but field(s) from another type are set: {set_foreign_fields}"
            )
        self.submodel()  # raises if a field required by `own_submodel` is unset
        return self

    def submodel(self) -> BaseModel:
        """This instance's fields, validated into its own `_SUBMODELS_BY_TYPE` submodel.

        Unset (`None`) fields are omitted rather than passed through
        explicitly, so the submodel's own defaults (e.g. `start_time: int =
        0`) apply instead of a spurious "must be an int, got None" error.
        """
        own_submodel = _SUBMODELS_BY_TYPE[self.type]
        values = {
            field_name: value
            for field_name in own_submodel.model_fields
            if (value := getattr(self, field_name)) is not None
        }
        return own_submodel.model_validate(values)
