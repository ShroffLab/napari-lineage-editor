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
        # Start at 1, not 0: `tracks_from_df` relabels every node id by +1
        # whenever it finds a 0 (0 is reserved for the segmentation
        # background label), so starting at 0 here means the actual loaded
        # node ids depend on whether this exact id-assignment ran, making
        # ids computed at different times (e.g. an id saved to a CSV by one
        # script run) not comparable to ids from a later load.
        df["cell_id"] = df.index + 1
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


def nx_from_tracks(tracks: Tracks) -> nx.DiGraph:
    """Build a `networkx.DiGraph` from a `Tracks` object's solution graph, for `traccuracy`.

    Mirrors `nx_from_lineage_df`'s node/edge shape, but reads directly off
    `tracks.graph_solution` instead of the original CSV -- so edits made
    since loading (e.g. cropping to a time range) are reflected.

    Args:
        tracks: The `Tracks` whose `graph_solution` to convert.

    Returns:
        One node per `graph_solution` node, keyed by node id, with `t`
        (time), `x`, `y`, `z`, and `name` attributes. One edge per
        `graph_solution` edge, directed from source to target.
    """
    node_attrs = tracks.graph_solution.node_attrs()
    edge_attrs = tracks.graph_solution.edge_attrs()

    graph = nx.DiGraph()
    for row in node_attrs.iter_rows(named=True):
        z, y, x = row["pos"]
        graph.add_node(row["node_id"], t=row["t"], x=x, y=y, z=z, name=row["name"])
    for row in edge_attrs.iter_rows(named=True):
        graph.add_edge(row["source_id"], row["target_id"])
    return graph


def save_tracks_to_csv(tracks: Tracks, path: Path) -> None:
    """Write `tracks`' solution graph to `path`, in the same shape `_load_lineage` reads back.

    Columns: `time`, `cell_name`, `x`, `y`, `z`, `cell_id`, `parent_id` --
    mirrors `nx_from_tracks`'s read of `graph_solution.node_attrs()`/
    `edge_attrs()`, but writes a CSV instead of a `networkx.DiGraph`. Nodes
    with no incoming edge (the lineage's start, or a division's first
    daughter if edges aren't tracked that way) get `parent_id == -1`,
    matching `_load_lineage`'s own convention.

    Rows are sorted by `(time, cell_id)` before writing: `node_attrs()`'s
    row order isn't guaranteed stable across graph states (e.g. before vs.
    after a crop or an edit), so writing it as-is would reorder every row on
    every save and make the on-disk diff unreadable even when only a
    handful of rows actually changed.

    Args:
        tracks: The `Tracks` whose `graph_solution` to save.
        path: Destination CSV path, overwritten if it already exists.
    """
    node_attrs = tracks.graph_solution.node_attrs()
    edge_attrs = tracks.graph_solution.edge_attrs()

    parent_by_node = dict(
        zip(edge_attrs["target_id"].to_list(), edge_attrs["source_id"].to_list(), strict=True)
    )

    node_ids = node_attrs["node_id"].to_list()
    positions = node_attrs["pos"].to_list()
    out = pd.DataFrame(
        {
            "time": node_attrs["t"].to_list(),
            "cell_name": node_attrs["name"].to_list(),
            "z": [pos[0] for pos in positions],
            "y": [pos[1] for pos in positions],
            "x": [pos[2] for pos in positions],
            "cell_id": node_ids,
            "parent_id": [parent_by_node.get(node_id, -1) for node_id in node_ids],
        }
    )
    out = out.sort_values(["time", "cell_id"]).reset_index(drop=True)
    out.to_csv(path, index=False)


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
