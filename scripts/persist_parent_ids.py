from __future__ import annotations

import argparse

import pandas as pd

from napari_lineage_editor.data import get_sulston, registry
from napari_lineage_editor.data._load_lineages import _resolve_division_edges
from napari_lineage_editor.data._utils import to_local_path

REGISTRY_PATH = "janelia://nrs.hhmi.org/shroff/Pharynx_Project/wormguides-data/registry"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compute and persist parent_id for a lineage representation's CSV, "
            "if it doesn't already have one."
        )
    )
    parser.add_argument("sample_id")
    parser.add_argument("repr_id")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute parent_id even if the CSV already has one "
        "(e.g. after fixing the sulston lineage source).",
    )
    args = parser.parse_args()

    reg = registry(REGISTRY_PATH)
    repres = reg.load_representation(args.sample_id, args.repr_id)
    path = to_local_path(repres.path, registry_root=reg.path)

    df = pd.read_csv(path)
    if "parent_id" in df.columns:
        if not args.force:
            print(f"{path} already has parent_id, nothing to do (use --force to recompute)")
            return
        df = df.drop(columns=["parent_id"])

    if "cell_id" not in df.columns:
        # Start at 1, not 0 -- see the matching comment in
        # napari_lineage_editor.data._load_lineages._load_lineage.
        df["cell_id"] = df.index + 1

    sulston_lineage = get_sulston(reg)
    time_name_pairs = list(zip(df["time"], df["cell_name"], strict=True))
    id_by_time_name = dict(zip(time_name_pairs, df["cell_id"], strict=True))
    df["parent_id"] = [
        id_by_time_name.get((time - 1, cell_name), -1)
        for time, cell_name in zip(df["time"], df["cell_name"], strict=True)
    ]
    df = _resolve_division_edges(df, sulston_lineage)

    df.to_csv(path, index=False)
    print(f"wrote parent_id to {path}")


if __name__ == "__main__":
    main()
