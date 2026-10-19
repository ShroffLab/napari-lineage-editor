from collections import defaultdict
from collections.abc import Sequence

import networkx as nx
import pandas as pd
from traccuracy import TrackingGraph
from traccuracy.matchers import PointMatcher


def match_nodes(lineages: Sequence[nx.DiGraph]) -> dict[int, list[int]]:
    """_summary_

    Args:
        lineages (Sequence[nx.DiGraph]): _description_

    Returns:
        dict[int, list[int]]: node ids in the first lineage, and the matches in the other lineages
    """
    graphs = [TrackingGraph(lin) for lin in lineages]
    assert len(lineages) > 1
    main = graphs[0]
    matcher = PointMatcher(threshold=1)
    matches = defaultdict(list)
    for other in graphs[1:]:
        matched = matcher.compute_mapping(main, other)
        for node in main.nodes():
            matches[node].append(matched.get_gt_pred_match(node))

    return dict(matches)


def matches_to_df(
    names: Sequence[str], lineages: Sequence[nx.DiGraph], matches: dict[int, list[int]]
) -> pd.DataFrame:
    """_summary_

    Args:
        names: the names of the lineages to use in the column names
        lineages: the list of lineages passed to traccuracy, for fetching names
        matches (dict[int, list[int]]): _description_

    Returns:
        A dataframe with a two-level `(lineage name, "id" | "name")` column
        `MultiIndex` -- e.g. `df["Sulston"]["name"]` or
        `df[("Sulston", "name")]` for one lineage's names, `df["Sulston"]`
        for its `id`/`name` sub-dataframe.
    """
    main_name, *other_names = names
    main, *others = lineages

    rows = []
    for node, other_matches in matches.items():
        row = {(main_name, "id"): node, (main_name, "name"): main.nodes[node]["name"]}
        for other_name, other, match in zip(other_names, others, other_matches, strict=True):
            row[(other_name, "id")] = match
            row[(other_name, "name")] = other.nodes[match]["name"] if match is not None else None
        rows.append(row)

    return pd.DataFrame(rows, columns=pd.MultiIndex.from_product([names, ["id", "name"]]))


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
    main_name, *other_names = matches.columns.get_level_values(0).unique()
    name_columns = [(name, "name") for name in (main_name, *other_names)]

    disagrees = pd.Series(False, index=matches.index)
    for other_name in other_names:
        disagrees |= matches[(other_name, "name")] != matches[(main_name, "name")]

    return matches[disagrees].drop_duplicates(subset=name_columns)
