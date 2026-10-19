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


def children_of_name(geff_lineage: pd.DataFrame, name: str) -> list[str]:
    """The Sulston lineage's names for `name`'s two daughter cells, if any.

    Args:
        geff_lineage: A `load_geff_lineage` dataframe (has `cell_id`,
            `parent_id`, `name` columns).
        name: A cell name to find the daughters of.

    Returns:
        The names of every row whose `parent_id` is `name`'s `cell_id`
        (normally 0 or 2 -- a cell divides into exactly two daughters, or
        not at all). Empty if `name` isn't in `geff_lineage` or has no
        daughters recorded.
    """
    matches = geff_lineage.loc[geff_lineage["name"] == name, "cell_id"]
    if matches.empty:
        return []
    cell_id = matches.iloc[0]
    return geff_lineage.loc[geff_lineage["parent_id"] == cell_id, "name"].tolist()


def suggest_child_names(
    geff_lineage: pd.DataFrame,
    new_parent_name: str,
    old_child_names: tuple[str, str],
) -> tuple[str, str] | None:
    """Suggest new names for two daughter tracklets after their parent was renamed.

    The naive guess for each daughter keeps its own last character (the
    anterior/posterior/etc. suffix) but swaps on the new parent name, e.g.
    renaming `"ABplapa"` to `"ABplapp"` naively suggests `"ABplapaa" ->
    "ABplappa"` and `"ABplapap" -> "ABplappp"`. If that naive guess (as a
    set, ignoring which physical child gets which name) matches the Sulston
    lineage's actual two daughters of `new_parent_name`, the naive pairing is
    used as-is; otherwise the two real Sulston names are assigned to the two
    physical children in a fixed, deterministic (alphabetical) order, since
    there's no way to know which physical daughter corresponds to which
    Sulston name without further information (e.g. position).

    Args:
        geff_lineage: A `load_geff_lineage` dataframe, used to look up
            `new_parent_name`'s real daughters.
        new_parent_name: The parent tracklet's name after renaming.
        old_child_names: The two daughter tracklets' current names, in a
            fixed order matching the caller's own list of child track ids.

    Returns:
        The suggested new names for the two daughters, in the same order as
        `old_child_names`, or `None` if `new_parent_name` has no two
        daughters recorded in `geff_lineage` (nothing to suggest).
    """
    sulston_children = children_of_name(geff_lineage, new_parent_name)
    if len(sulston_children) != 2:
        return None

    naive_guess = tuple(new_parent_name + old_name[-1] for old_name in old_child_names)
    if set(naive_guess) == set(sulston_children):
        return naive_guess

    # No way to tell which physical child is which Sulston daughter without
    # more information (e.g. position) -- sort both arbitrarily but
    # deterministically, rather than guessing at random.
    ordered = sorted(sulston_children)
    return (ordered[0], ordered[1])


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
        geff_lineage = load_geff_lineage(sulston_lineage_path)
        df = resolve_division_edges(df, geff_lineage)
    return df




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
