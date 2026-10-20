from pathlib import Path
from typing import Literal

from dataset_registry import Registry
from janelia_pathlib import from_janelia_uri
from pydantic import BaseModel, model_validator


def registry(path: Path | str) -> Registry:
    """Open the registry at `path`, resolving (and mounting, on macOS) a `janelia://` URI first.

    Callers can pass either a plain local path or a `janelia://host/share/subpath`
    URI -- the latter is translated to its real local mountpoint via
    `janelia_pathlib`, so scripts just set `REGISTRY_PATH` to the `janelia://`
    URI and never need to call into `janelia_pathlib` themselves.
    """
    local_path = from_janelia_uri(str(path))
    return Registry(
        Path(local_path),
        sample_metadata_model=CelegansSampleMetadata,
        representation_metadata_model=[CelegansImageRepMetadata, CelegansLineageRepMetadata],
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

    type: Literal["image"] = "image"
    file_pattern: str
    start_time: int = 0
    end_time: int
    start_z: int | None = None
    end_z: int | None = None

    @model_validator(mode="after")
    def _check_z_range(self) -> "CelegansImageRepMetadata":
        if (self.start_z is None) != (self.end_z is None):
            raise ValueError("start_z and end_z must either both be set or both be None")
        return self


class CelegansLineageRepMetadata(BaseModel):
    """Fields specific to a lineage representation.

    `path` points directly at one CSV file (not a per-timepoint series) with
    fixed columns `time, cell_name, x, y, z, diameter`, and optional cell_id and parent_id columns.
    """

    type: Literal["lineage"] = "lineage"

    has_links: bool = False
    has_names: bool = True
    time_offset: int = 0
    """Value to add to time to match up with the time range in the related image sequence"""
