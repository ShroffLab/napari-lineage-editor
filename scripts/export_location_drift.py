from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from napari_lineage_editor.data import get_sulston, registry
from napari_lineage_editor.data._load_lineages import load_lineage
from napari_lineage_editor.data._utils import to_local_path

REGISTRY_PATH = "janelia://nrs.hhmi.org/shroff/Pharynx_Project/wormguides-data/registry"

CURATED_SUBSTRING = "curated-by-alyssa"
ZENODO_SUBSTRING = "zenodo"

DRIFT_THRESHOLD = 1.5
SWAP_RESOLVED_THRESHOLD = 2.0


def _find_repr_id(repr_ids: list[str], substring: str) -> str | None:
    matches = [repr_id for repr_id in repr_ids if substring in repr_id]
    return matches[0] if matches else None


def _sibling_names(cell_name: str, names_at_time: set[str]) -> list[str]:
    """Other cell names at the same timepoint sharing `cell_name`'s parent prefix."""
    prefix = cell_name[:-1]
    return [
        name
        for name in names_at_time
        if name != cell_name and len(name) == len(cell_name) and name[:-1] == prefix
    ]


def compute_unexplained_drift(curated_df: pd.DataFrame, zenodo_df: pd.DataFrame) -> pd.DataFrame:
    """Join `curated_df`/`zenodo_df` on (time, cell_name) and find unexplained position drift.

    A pair whose x/y positions disagree by more than `DRIFT_THRESHOLD` is
    kept unless it's a sibling-name-swap artifact: swapping the (cell,
    sibling) x/y positions between the two versions would bring the combined
    distance under `SWAP_RESOLVED_THRESHOLD`.
    """
    merged = curated_df.merge(zenodo_df, on=["time", "cell_name"], suffixes=("_curated", "_zenodo"))
    merged["dist"] = np.sqrt(
        (merged["x_curated"] - merged["x_zenodo"]) ** 2
        + (merged["y_curated"] - merged["y_zenodo"]) ** 2
    )
    drifted = merged[merged["dist"] > DRIFT_THRESHOLD]

    names_at_time: dict[int, set[str]] = {
        time: set(group["cell_name"]) for time, group in merged.groupby("time")
    }
    merged_by_time_name = {(row.time, row.cell_name): row for row in merged.itertuples()}

    unexplained_rows = []
    for row in drifted.itertuples():
        siblings = _sibling_names(row.cell_name, names_at_time.get(row.time, set()))
        is_swap_artifact = False
        for sibling in siblings:
            sibling_row = merged_by_time_name.get((row.time, sibling))
            if sibling_row is None:
                continue
            swapped_dist = np.sqrt(
                (row.x_curated - sibling_row.x_zenodo) ** 2
                + (row.y_curated - sibling_row.y_zenodo) ** 2
            ) + np.sqrt(
                (sibling_row.x_curated - row.x_zenodo) ** 2
                + (sibling_row.y_curated - row.y_zenodo) ** 2
            )
            if swapped_dist < SWAP_RESOLVED_THRESHOLD:
                is_swap_artifact = True
                break
        if not is_swap_artifact:
            unexplained_rows.append(row)

    result = pd.DataFrame(
        {
            "t": [row.time for row in unexplained_rows],
            "z": [row.z_zenodo for row in unexplained_rows],
            "y": [row.y_zenodo for row in unexplained_rows],
            "x": [row.x_zenodo for row in unexplained_rows],
            "name": [row.cell_name for row in unexplained_rows],
        }
    )
    return result.sort_values(["t", "name"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare each embryo's curated-by-alyssa and zenodo lineages and "
            "export cells with unexplained position drift to CSV."
        )
    )
    parser.add_argument(
        "sample_ids",
        nargs="*",
        help="Sample ids to process (default: all samples with both representations).",
    )
    args = parser.parse_args()

    reg = registry(REGISTRY_PATH)
    sulston = get_sulston(reg)

    sample_ids = args.sample_ids if args.sample_ids else reg.list_samples()

    for sample_id in sample_ids:
        sample = reg.load_sample(sample_id)
        repr_ids = reg.list_representations(sample_id)
        curated_repr_id = _find_repr_id(repr_ids, CURATED_SUBSTRING)
        zenodo_repr_id = _find_repr_id(repr_ids, ZENODO_SUBSTRING)

        if curated_repr_id is None or zenodo_repr_id is None:
            print(f"{sample.name} ({sample_id}): missing curated or zenodo representation, skipped")
            continue

        curated_repres = reg.load_representation(sample_id, curated_repr_id)
        zenodo_repres = reg.load_representation(sample_id, zenodo_repr_id)
        curated_df = load_lineage(curated_repres, sulston, reg.path)
        zenodo_df = load_lineage(zenodo_repres, sulston, reg.path)

        drift_df = compute_unexplained_drift(curated_df, zenodo_df)

        if len(drift_df) == 0:
            print(f"{sample.name} ({sample_id}): no unexplained drift, skipped")
            continue

        output_dir = to_local_path(curated_repres.path, reg.path).parent
        output_path = output_dir / f"location_drift_{sample_id}.csv"
        drift_df.to_csv(output_path, index=False)
        print(f"{sample.name} ({sample_id}): wrote {len(drift_df)} rows to {output_path}")


if __name__ == "__main__":
    main()
