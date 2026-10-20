from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx
import pandas as pd
from funtracks.import_export import tracks_from_df

from ._utils import to_local_path

if TYPE_CHECKING:
    from pathlib import Path

    from dataset_registry import Representation
    from funtracks.data_model.tracks import Tracks


def _resolve_division_edges(lineage: pd.DataFrame, sulston_lineage: nx.DiGraph) -> pd.DataFrame:
    """Fill in `parent_id` for `lineage`'s division-point rows, using `geff_lineage`'s real linking.

        A row left with `parent_id == -1` by name-continuation alone is either a
        cell's first-appearance row (born from a division) or the dataset's true
        start. This looks up the cell's true parent *name* from `sulston lineage`
    , then finds the parent name's own row at `time - 1` in
        `lineage` to resolve the division edge.

        Args:
            lineage: A lineage dataframe with `time`, `cell_name`, `cell_id`,
                `parent_id` columns (e.g. from `load_lineages`).
            sulston_lineage: sulston lineage as a nx graph, one node per named tracklet

        Returns:
            `lineage`, with `parent_id` filled in for every row whose
            `cell_name`'s parent (per `geff_lineage`) has a row at `time - 1` in
            `lineage`. Rows that still have no resolvable parent (e.g. the
            dataset's first timepoint) keep `parent_id == -1`.
    """
    lineage = lineage.copy()

    id_by_time_name = dict(
        zip(
            zip(lineage["time"], lineage["cell_name"], strict=True),
            lineage["cell_id"],
            strict=True,
        )
    )

    needs_parent = lineage["parent_id"] == -1
    resolved_parent_ids = []
    for time, cell_name in zip(
        lineage.loc[needs_parent, "time"], lineage.loc[needs_parent, "cell_name"], strict=True
    ):
        if sulston_lineage.has_node(cell_name):
            parents = list(sulston_lineage.predecessors(cell_name))
            if len(parents) == 0:
                parent_name = None
            elif len(parents) == 1:
                parent_name = parents[0]
            else:
                raise ValueError("Sulston lineage can't have multiple parents")
            parent_id = id_by_time_name.get((time - 1, parent_name), -1) if parent_name else -1
        else:
            parent_id = -1
        resolved_parent_ids.append(parent_id)

    lineage.loc[needs_parent, "parent_id"] = resolved_parent_ids
    return lineage


def load_lineage(repres: Representation, sulston: nx.DiGraph, registry_root: Path) -> pd.DataFrame:
    path = to_local_path(repres.path, registry_root=registry_root)
    return _load_lineage(path, sulston_lineage=sulston, time_offset=repres.metadata.time_offset)


def _load_lineage(
    path: Path, sulston_lineage: nx.DiGraph | None, time_offset: int = 0
) -> pd.DataFrame:
    """Load a name-only lineage CSV and resolve `parent_id` for every row.

    Reads a CSV with columns `time, cell_name, x, y, z, diameter` (one row
    per annotated nucleus per timepoint) and assigns each row a `cell_id`
    (its row index) plus a `parent_id`:

    1. Continuation: the `cell_id` of the row with the same `cell_name` at
       `time - 1`, if any.
    2. Division (only if `sulston_lineage` is given): for a cell's first-
       appearance row (born from a division, so step 1 finds nothing), look
       up its true parent *name* from the geff lineage tree (which already
       has correct name-based linking, e.g. `"ABa"`'s parent is `"AB"`), then
       find that parent name's own row at `time - 1` in this CSV.

    Rows with no resolvable parent either way (e.g. the dataset's first
    timepoint) get `parent_id == -1`.

    Args:
        path: Path to the lineage CSV.
        sulston_lineage: the sulston lineage as a networkx graph, one node per name
        time_offset: add this to all times in the csv, to align with other data (e.g. images)

    Returns:
        The CSV's rows, in file order, with added `cell_id` and `parent_id`
        columns.
    """
    df = pd.read_csv(path)
    if "cell_id" not in df.columns:
        df["cell_id"] = df.index
    if "parent_id" not in df.columns:
        time_name_pairs = list(zip(df["time"], df["cell_name"], strict=True))
        id_by_time_name = dict(zip(time_name_pairs, df["cell_id"], strict=True))
        df["parent_id"] = [
            id_by_time_name.get((time - 1, cell_name), -1)
            for time, cell_name in zip(df["time"], df["cell_name"], strict=True)
        ]
        if sulston_lineage is None:
            raise ValueError("No division links and no sulston lineage")
        else:
            df = _resolve_division_edges(df, sulston_lineage)
        df["time"] += time_offset
    return df


def nx_from_lineage_df(lineage: pd.DataFrame) -> nx.DiGraph:
    """Build a `networkx.DiGraph` from a lineage dataframe, for `traccuracy`'s `TrackingGraph`.

    Args:
        lineage: A dataframe with `time`, `x`, `y`, `z`, `cell_id`,
            `parent_id`, `cell_name` columns (e.g. from `load_lineages` or
            `load_linked_lineage`).

    Returns:
        One node per row, keyed by `cell_id`, with `t` (time), `x`, `y`,
        `z`, and `name` (the row's `cell_name`) attributes. One edge per row
        with a resolvable `parent_id` (`parent_id != -1`), directed from
        parent to child.
    """
    graph = nx.DiGraph()
    for row in lineage.itertuples():
        graph.add_node(row.cell_id, t=row.time, x=row.x, y=row.y, z=row.z, name=row.cell_name)
    for row in lineage.itertuples():
        if row.parent_id != -1:
            graph.add_edge(row.parent_id, row.cell_id)
    return graph


def tracks_from_lineage_df(lineage: pd.DataFrame) -> Tracks:
    """Build a `funtracks` `Tracks` object from a lineage dataframe.

    Args:
        lineage: A dataframe with `time`, `x`, `y`, `z`, `cell_id`,
            `parent_id`, `cell_name` columns (e.g. from ß`load_lineages` or
            `load_linked_lineage`).

    Returns:
        A `Tracks` object, ready to pass to
        `napari_track_edit`'s `TracksList.add_tracks`.
    """
    LINEAGE_NODE_NAME_MAP = {
        "time": "time",
        "pos": ["z", "y", "x"],
        "id": "cell_id",
        "parent_id": "parent_id",
        "name": "cell_name",
    }
    return tracks_from_df(lineage, node_name_map=LINEAGE_NODE_NAME_MAP)
