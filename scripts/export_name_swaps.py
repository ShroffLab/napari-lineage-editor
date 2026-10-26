from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from napari_lineage_editor.data import get_sulston, load_lineage, nx_from_lineage_df, registry

if TYPE_CHECKING:
    from collections.abc import Sequence

    import networkx as nx

REGISTRY_PATH = "janelia://nrs.hhmi.org/shroff/Pharynx_Project/wormguides-data/registry"

DIST_THRESHOLD = 1.0


def match_nodes(lineages: Sequence[nx.DiGraph], threshold: float = 1) -> dict[int, list[int]]:
    """Match nodes between the first ("main") lineage and each of the others.

    Nodes are matched one-to-one within each timepoint by nearest position,
    only if their (z, y, x) positions are within `threshold` of each other.

    Args:
        lineages: sequence of lineages, as networkx graphs, with the first
            one being the "main" one that others are matched against
        threshold: distance threshold to use for position matching

    Returns:
        dict[int, list[int]]: node ids in the first lineage, and the matches in the other lineages
    """
    assert len(lineages) > 1
    main = lineages[0]
    matches: dict[int, list[int | None]] = defaultdict(list)

    for other in lineages[1:]:
        match_for_other = _match_frame_by_frame(main, other, threshold)
        for node in main.nodes():
            matches[node].append(match_for_other.get(node))

    return dict(matches)


def _match_frame_by_frame(main: nx.DiGraph, other: nx.DiGraph, threshold: float) -> dict[int, int]:
    """Match nodes between two lineages, frame by frame, within `threshold` distance.

    Each node in `other` is matched to at most one node in `main`, and vice
    versa. Within a frame, candidate pairs are considered in order of
    increasing distance, so closer pairs are matched first.
    """
    main_nodes_by_frame: dict[int, list[int]] = defaultdict(list)
    for node, t in main.nodes(data="t"):
        main_nodes_by_frame[t].append(node)

    other_nodes_by_frame: dict[int, list[int]] = defaultdict(list)
    for node, t in other.nodes(data="t"):
        other_nodes_by_frame[t].append(node)

    match: dict[int, int] = {}
    for frame, main_nodes in main_nodes_by_frame.items():
        other_nodes = other_nodes_by_frame.get(frame, [])
        if not other_nodes:
            continue

        main_positions = np.array(
            [[main.nodes[n]["z"], main.nodes[n]["y"], main.nodes[n]["x"]] for n in main_nodes]
        )
        other_positions = np.array(
            [[other.nodes[n]["z"], other.nodes[n]["y"], other.nodes[n]["x"]] for n in other_nodes]
        )

        # Chebyshev (max-per-axis) radius, to match the per-axis `abs(a - b) <= threshold`
        # tolerance -- a Euclidean query would miss/include different pairs.
        tree = cKDTree(other_positions)
        pairs = tree.query_ball_point(main_positions, r=threshold, p=np.inf)

        candidates = []
        for main_idx, other_idxs in enumerate(pairs):
            for other_idx in other_idxs:
                dist = np.sum((main_positions[main_idx] - other_positions[other_idx]) ** 2)
                candidates.append((dist, main_nodes[main_idx], other_nodes[other_idx]))

        candidates.sort(key=lambda c: c[0])
        matched_main: set[int] = set()
        matched_other: set[int] = set()
        for _, main_node, other_node in candidates:
            if main_node in matched_main or other_node in matched_other:
                continue
            match[main_node] = other_node
            matched_main.add(main_node)
            matched_other.add(other_node)

    return match


def matches_to_df(
    names: Sequence[str], lineages: Sequence[nx.DiGraph], matches: dict[int, list[int]]
) -> pd.DataFrame:
    """_summary_

    Args:
        names: the names of the lineages to use in the column names
        lineages: the list of lineages passed to traccuracy, for fetching names
        matches (dict[int, list[int]]): _description_

    Returns:
        A dataframe with a `("time", "")` column (from the main lineage's
        nodes) and a two-level `(lineage name, "id" | "name")` column
        `MultiIndex` -- e.g. `df["Sulston"]["name"]` or
        `df[("Sulston", "name")]` for one lineage's names, `df["Sulston"]`
        for its `id`/`name` sub-dataframe.
    """
    main_name, *other_names = names
    main, *others = lineages

    rows = []
    for node, other_matches in matches.items():
        row = {
            ("time", ""): main.nodes[node]["t"],
            (main_name, "id"): node,
            (main_name, "name"): main.nodes[node]["name"],
        }
        for other_name, other, match in zip(other_names, others, other_matches, strict=True):
            row[(other_name, "id")] = match
            row[(other_name, "name")] = other.nodes[match]["name"] if match is not None else None
        rows.append(row)

    columns = pd.MultiIndex.from_tuples(
        [("time", ""), *pd.MultiIndex.from_product([names, ["id", "name"]])]
    )
    return pd.DataFrame(rows, columns=columns)


def get_conflicts(matches: pd.DataFrame) -> pd.DataFrame:
    """Return rows where any other name != main name, deduplicated ignoring IDs.

    Args:
        matches: A `matches_to_df` result (two-level `(lineage name, "id" |
            "name")` column `MultiIndex`, main lineage's columns first).

    Returns:
        The subset of `matches` where at least one non-main lineage's "name"
        column disagrees with the main lineage's "name" column, with one row
        kept per unique combination of "name" column values (the kept row's
        "id" columns are arbitrary among the duplicates).
    """
    main_name, *other_names = (
        name for name in matches.columns.get_level_values(0).unique() if name != "time"
    )
    name_columns = [(name, "name") for name in (main_name, *other_names)]

    disagrees = pd.Series(False, index=matches.index)
    for other_name in other_names:
        disagrees |= matches[(other_name, "name")] != matches[(main_name, "name")]

    return matches[disagrees].drop_duplicates(subset=name_columns)


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
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory to write the conflicts CSVs to.",
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

        output_path = args.output_dir / f"lineage_conflicts_{sample_id}.csv"
        conflicts.to_csv(output_path, index=False)
        print(f"{sample.name} ({sample_id}): wrote {len(conflicts)} rows to {output_path}")


if __name__ == "__main__":
    main()
