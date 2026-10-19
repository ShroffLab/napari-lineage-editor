from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import networkx as nx


def suggest_child_names(
    sulston_lineage: nx.DiGraph,
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
        sulston_lineage: the sulston linneage to use for suggesting new names
        new_parent_name: The parent tracklet's name after renaming.
        old_child_names: The two daughter tracklets' current names, in a
            fixed order matching the caller's own list of child track ids.

    Returns:
        The suggested new names for the two daughters, in the same order as
        `old_child_names`, or `None` if `new_parent_name` has no two
        daughters recorded in `geff_lineage` (nothing to suggest).
    """
    sulston_children = list(sulston_lineage.successors(new_parent_name))
    if len(sulston_children) != 2:
        return None

    child1, child2 = old_child_names
    naive_guess = (new_parent_name + child1[-1], new_parent_name + child2[-1])
    if set(naive_guess) == set(sulston_children):
        return naive_guess

    # No way to tell which physical child is which Sulston daughter without
    # more information (e.g. position) -- sort both arbitrarily but
    # deterministically, rather than guessing at random.
    ordered = sorted(sulston_children)
    return (ordered[0], ordered[1])
