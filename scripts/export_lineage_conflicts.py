from __future__ import annotations

import argparse
from pathlib import Path

from napari_lineage_editor import get_conflicts, match_nodes, matches_to_df
from napari_lineage_editor.data import get_sulston, load_lineage, nx_from_lineage_df, registry

REGISTRY_PATH = "/Users/malinmayorc/projects/celegans-cell-id/wormguides-data/registry"
OUTPUT_DIR = Path(__file__).parent.parent / "scratch"

DIST_THRESHOLD = 10


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Compare every pair of lineage representations for each embryo and export "
            "name conflicts to CSV, so the comparison widget can load from a file "
            "instead of the full view-only tracks."
        )
    )
    parser.add_argument(
        "sample_ids",
        nargs="*",
        help="Sample ids to process (default: all samples with 2+ lineage representations).",
    )
    args = parser.parse_args()

    reg = registry(REGISTRY_PATH)
    sulston = get_sulston(reg)

    sample_ids = args.sample_ids if args.sample_ids else reg.list_samples()

    for sample_id in sample_ids:
        sample = reg.load_sample(sample_id)
        repr_ids = [
            repr_id
            for repr_id in reg.list_representations(sample_id)
            if reg.load_representation(sample_id, repr_id).metadata.type == "lineage"
        ]

        if len(repr_ids) < 2:
            print(f"{sample.name} ({sample_id}): fewer than 2 lineage representations, skipped")
            continue

        names = []
        dfs = []
        for repr_id in repr_ids:
            repres = reg.load_representation(sample_id, repr_id)
            df = load_lineage(repres, sulston, reg.path)
            names.append(repres.name)
            dfs.append(df)

        # Lineages often span different time ranges (e.g. a curated lineage
        # continuing past where a zenodo/wormguides one stops) -- crop every
        # lineage to their shared overlap first, so the extra trailing
        # timepoints in a longer lineage don't show up as spurious "missing"
        # conflicts against the shorter ones.
        start_time = max(df["time"].min() for df in dfs)
        end_time = min(df["time"].max() for df in dfs)
        graphs = [nx_from_lineage_df(df[df["time"].between(start_time, end_time)]) for df in dfs]

        matches = match_nodes(graphs, DIST_THRESHOLD)
        matched_df = matches_to_df(names, graphs, matches)
        conflicts = get_conflicts(matched_df)

        if len(conflicts) == 0:
            print(f"{sample.name} ({sample_id}): no conflicts, skipped")
            continue

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_DIR / f"lineage_conflicts_{sample_id}.csv"
        conflicts.to_csv(output_path, index=False)
        print(f"{sample.name} ({sample_id}): wrote {len(conflicts)} rows to {output_path}")


if __name__ == "__main__":
    main()
