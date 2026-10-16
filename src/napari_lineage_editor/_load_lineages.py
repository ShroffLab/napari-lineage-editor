import geff
import pandas as pd
from funtracks.data_model.tracks import Tracks
from funtracks.import_export import tracks_from_df


def load_geff_lineage(path: str) -> pd.DataFrame:
    """Load a geff lineage tree: one row per tracklet (not per imaging frame).

    Unlike `load_lineages`, nodes here have no position or per-frame time --
    each node is a whole tracklet (e.g. `"MSpaapp"`), already linked by the
    geff file's own edges rather than inferred from name continuation.

    Args:
        path: Path to the `.geff` directory.

    Returns:
        One row per node, with `cell_id` (the geff node id), every node
        property from the geff file (e.g. `name`, `division_time_raw`), and
        `parent_id`: the node's one predecessor's `cell_id`, or `-1` for the
        root. Raises if any node has more than one predecessor -- this
        loader assumes a tree, not a general DAG.
    """
    graph, _metadata = geff.read(path, backend="networkx")

    rows = []
    for node_id, props in graph.nodes(data=True):
        predecessors = list(graph.predecessors(node_id))
        if len(predecessors) > 1:
            raise ValueError(
                f"node {node_id!r} has {len(predecessors)} predecessors; "
                "load_geff_lineage expects a tree (at most one parent per node)"
            )
        parent_id = predecessors[0] if predecessors else -1
        rows.append({"cell_id": node_id, "parent_id": parent_id, **props})
    return pd.DataFrame(rows)


def name_to_parent_name(geff_lineage: pd.DataFrame) -> dict[str, str]:
    """A `{cell_name: parent_cell_name}` map derived from a `load_geff_lineage` result.

    Args:
        geff_lineage: A `load_geff_lineage` dataframe (has `cell_id`,
            `parent_id`, `name` columns).

    Returns:
        Every non-root node's name mapped to its parent's name (e.g.
        `"ABa" -> "AB"`). The root has no entry.
    """
    name_by_id = dict(zip(geff_lineage["cell_id"], geff_lineage["name"], strict=True))
    return {
        row["name"]: name_by_id[row["parent_id"]]
        for _, row in geff_lineage.iterrows()
        if row["parent_id"] != -1
    }


def resolve_division_edges(lineage: pd.DataFrame, geff_lineage: pd.DataFrame) -> pd.DataFrame:
    """Fill in `parent_id` for `lineage`'s division-point rows, using `geff_lineage`'s real linking.

    A row left with `parent_id == -1` by name-continuation alone is either a
    cell's first-appearance row (born from a division) or the dataset's true
    start. This looks up the cell's true parent *name* from `geff_lineage`
    (a `load_geff_lineage` result, which already has correct name-based
    linking), then finds the parent name's own row at `time - 1` in
    `lineage` to resolve the division edge.

    Args:
        lineage: A lineage dataframe with `time`, `cell_name`, `cell_id`,
            `parent_id` columns (e.g. from `load_lineages`).
        geff_lineage: A `load_geff_lineage` dataframe for the same lineage,
            used only for its name-to-parent-name structure.

    Returns:
        `lineage`, with `parent_id` filled in for every row whose
        `cell_name`'s parent (per `geff_lineage`) has a row at `time - 1` in
        `lineage`. Rows that still have no resolvable parent (e.g. the
        dataset's first timepoint) keep `parent_id == -1`.
    """
    lineage = lineage.copy()
    parent_name_by_name = name_to_parent_name(geff_lineage)

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
        parent_name = parent_name_by_name.get(cell_name)
        parent_id = id_by_time_name.get((time - 1, parent_name), -1) if parent_name else -1
        resolved_parent_ids.append(parent_id)

    lineage.loc[needs_parent, "parent_id"] = resolved_parent_ids
    return lineage


def load_linked_lineage(path: str) -> pd.DataFrame:
    """Load a tab-separated lineage file that already has real `cell_id`/`parent_id` links.

    Unlike `load_lineages` (which infers links by name continuation for a
    name-only, comma-separated CSV), this is for files that already encode
    the lineage structure directly -- e.g. the zenodo 6460375 tracks.txt
    format, with columns `t, z, y, x, cell_id, parent_id, track_id, radius,
    name, div_state` (a representation whose `has_links` metadata is
    `True`).

    Args:
        path: Path to the tab-separated lineage file.

    Returns:
        The file's rows, in file order, renamed so `t` reads as `time` (to
        match `load_lineages`'s output shape) -- every other column
        (`cell_id`, `parent_id`, `name`, etc.) is passed through unchanged.
    """
    return pd.read_csv(path, sep="\t").rename(columns={"t": "time"})


def load_lineages(path: str, geff_lineage_path: str | None = None) -> pd.DataFrame:
    """Load a name-only lineage CSV and resolve `parent_id` for every row.

    Reads a CSV with columns `time, cell_name, x, y, z, diameter` (one row
    per annotated nucleus per timepoint) and assigns each row a `cell_id`
    (its row index) plus a `parent_id`:

    1. Continuation: the `cell_id` of the row with the same `cell_name` at
       `time - 1`, if any.
    2. Division (only if `geff_lineage_path` is given): for a cell's first-
       appearance row (born from a division, so step 1 finds nothing), look
       up its true parent *name* from the geff lineage tree (which already
       has correct name-based linking, e.g. `"ABa"`'s parent is `"AB"`), then
       find that parent name's own row at `time - 1` in this CSV.

    Rows with no resolvable parent either way (e.g. the dataset's first
    timepoint) get `parent_id == -1`.

    Args:
        path: Path to the lineage CSV.
        geff_lineage_path: Path to a `.geff` lineage tree for the same
            lineage (see `load_geff_lineage`), used to resolve division
            edges. If omitted, division edges are left unresolved
            (`parent_id == -1` for every division-point row).

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

    if geff_lineage_path is not None:
        geff_lineage = load_geff_lineage(geff_lineage_path)
        df = resolve_division_edges(df, geff_lineage)
    return df


LOAD_LINEAGES_NODE_NAME_MAP = {
    "time": "time",
    "pos": ["z", "y", "x"],
    "id": "cell_id",
    "parent_id": "parent_id",
    "name": "cell_name",
}
LOAD_LINKED_LINEAGE_NODE_NAME_MAP = {
    "time": "time",
    "pos": ["z", "y", "x"],
    "id": "cell_id",
    "parent_id": "parent_id",
    "name": "name",
}


def tracks_from_lineage_df(
    lineage: pd.DataFrame, node_name_map: dict[str, str | list[str]] | None = None
) -> Tracks:
    """Build a `funtracks` `Tracks` object from a lineage dataframe.

    Args:
        lineage: A dataframe with `time`, `x`, `y`, `z`, `cell_id`,
            `parent_id`, and a cell-name column (e.g. from `load_lineages`
            or `load_linked_lineage`).
        node_name_map: `funtracks.import_export.tracks_from_df`'s
            `node_name_map`. Defaults to the shape `load_lineages` produces
            (name column `cell_name`); pass
            `LOAD_LINKED_LINEAGE_NODE_NAME_MAP` for a `load_linked_lineage`
            dataframe instead (name column `name`).

    Returns:
        A `Tracks` object, ready to pass to
        `napari_track_edit`'s `TracksList.add_tracks`.
    """
    if node_name_map is None:
        node_name_map = LOAD_LINEAGES_NODE_NAME_MAP
    return tracks_from_df(lineage, node_name_map=node_name_map)
