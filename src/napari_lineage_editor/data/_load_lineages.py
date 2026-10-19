import geff
import networkx as nx
import pandas as pd
from funtracks.data_model.tracks import Tracks
from funtracks.import_export import tracks_from_df


def load_sulston_lineage(path: str) -> nx.DiGraph:
    """Load a geff lineage tree: one row per tracklet (not per imaging frame).

    Unlike `load_lineages`, nodes here have no position or per-frame time --
    each node is a whole tracklet (e.g. `"MSpaapp"`), already linked by the
    geff file's own edges rather than inferred from name continuation.

    Args:
        path: Path to the `.geff` directory.

    Returns:
        The cell ids are the names - this is okay, for the tracklet graph, and simplifies every
        other lookup we need (predecessors, succesors, etc)
    """
    graph, _metadata = geff.read(path, backend="networkx")

    return nx.relabel_nodes(graph, nx.get_node_attributes(graph, "name"))


def resolve_division_edges(lineage: pd.DataFrame, sulston_lineage: nx.DiGraph) -> pd.DataFrame:
    """Fill in `parent_id` for `lineage`'s division-point rows, using `geff_lineage`'s real linking.

    A row left with `parent_id == -1` by name-continuation alone is either a
    cell's first-appearance row (born from a division) or the dataset's true
    start. This looks up the cell's true parent *name* from `sulston lineage`
, then finds the parent name's own row at `time - 1` in
    `lineage` to resolve the division edge.

    Args:
        lineage: A lineage dataframe with `time`, `cell_name`, `cell_id`,
            `parent_id` columns (e.g. from `load_lineages`).
       sulston_lineage

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
        parents = list(sulston_lineage.predecessors(cell_name))
        if len(parents) == 0:
            parent_name = None
        elif len(parents) == 1:
            parent_name = parents[0]
        else:
            raise ValueError("Sulston lineage can't have multiple parents")
        parent_id = id_by_time_name.get((time - 1, parent_name), -1) if parent_name else -1
        resolved_parent_ids.append(parent_id)

    lineage.loc[needs_parent, "parent_id"] = resolved_parent_ids
    return lineage


def load_linked_lineage(path: str, time_offset: int = 0) -> pd.DataFrame:
    """Load a lineage CSV that already has real `cell_id`/`parent_id` links.

    Args:
        path: Path to the lineage CSV.
        time_offset: Added to every row's `time` value
    Returns:
        The file's rows, in file order, offset by `time_offset`.
    """
    lineage = pd.read_csv(path)
    lineage["time"] += time_offset
    return lineage


def load_lineages(path: str, sulston_lineage_path: str | None = None) -> pd.DataFrame:
    """Load a name-only lineage CSV and resolve `parent_id` for every row.

    Reads a CSV with columns `time, cell_name, x, y, z, diameter` (one row
    per annotated nucleus per timepoint) and assigns each row a `cell_id`
    (its row index) plus a `parent_id`:

    1. Continuation: the `cell_id` of the row with the same `cell_name` at
       `time - 1`, if any.
    2. Division (only if `sulston_lineage_path` is given): for a cell's first-
       appearance row (born from a division, so step 1 finds nothing), look
       up its true parent *name* from the geff lineage tree (which already
       has correct name-based linking, e.g. `"ABa"`'s parent is `"AB"`), then
       find that parent name's own row at `time - 1` in this CSV.

    Rows with no resolvable parent either way (e.g. the dataset's first
    timepoint) get `parent_id == -1`.

    Args:
        path: Path to the lineage CSV.
        sulston_lineage_path: Path to a `.geff` lineage tree for the sulston lineage, used to
            resolve division edges. If omitted, division edges are left
            unresolved (`parent_id == -1` for every division-point row).

    Returns:
        The CSV's rows, in file order, with added `cell_id` and `parent_id`
        columns.
    """
    df = pd.read_csv(path)
    df["cell_id"] = df.index

    time_name_pairs = list(zip(df["time"], df["cell_name"], strict=True))
    id_by_time_name = dict(zip(time_name_pairs, df["cell_id"], strict=True))
    df["parent_id"] = [
        id_by_time_name.get((time - 1, cell_name), -1)
        for time, cell_name in zip(df["time"], df["cell_name"], strict=True)
    ]

    if sulston_lineage_path is not None:
        sulston_lineage = load_sulston_lineage(sulston_lineage_path)
        df = resolve_division_edges(df, sulston_lineage)
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
        graph.add_node(
            row.cell_id, t=row.time, x=row.x, y=row.y, z=row.z, name=row.cell_name
        )
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
