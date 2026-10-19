from __future__ import annotations

from typing import TYPE_CHECKING

from funtracks.actions import Action, ActionGroup, BasicAction, UpdateNodeAttrs

if TYPE_CHECKING:
    import networkx as nx
    from funtracks.data_model import Tracks


def _set_nodes_attr(tracks: Tracks, nodes: list[int], key: str, value: object) -> ActionGroup:
    """One `UpdateNodeAttrs` per node, as an un-added-to-history `ActionGroup`.

    Does the same per-node work as `funtracks.user_actions.UserUpdateNodesAttrs`,
    but without its own unconditional `action_history.add_new_action`/
    `refresh.emit()` calls -- those only belong on the single enclosing
    action (e.g. `UserSwapNames`), not on each nested sub-step.
    """
    return ActionGroup(tracks, [UpdateNodeAttrs(tracks, node, {key: value}) for node in nodes])


UNRESOLVED_FEATURE_KEY = "unresolved"
"""Boolean node feature marking a tracklet as needing a name reviewed/assigned
after a `UserSwapNames` -- shows up as a group in napari-track-edit's Groups
tab (any boolean feature is automatically a group), so a swap's downstream
fallout is visible and resolvable there without separate UI in this project.
"""


def _ensure_unresolved_feature(tracks: Tracks) -> None:
    """Register the `unresolved` boolean feature on `tracks`, if not already present."""
    if UNRESOLVED_FEATURE_KEY in tracks.features:
        return
    tracks.add_feature(
        UNRESOLVED_FEATURE_KEY,
        {
            "feature_type": "node",
            "value_type": "bool",
            "num_values": 1,
            "display_name": UNRESOLVED_FEATURE_KEY,
            "default_value": False,
        },
    )


class SetName(BasicAction):
    """Set the `name` attribute on every node in a tracklet.

    Name is 1:1 with tracklet in this project, so renaming is applied to
    every node of the tracklet as a single action, rather than per-node.
    """

    def __init__(
        self,
        tracks: Tracks,
        track_id: int,
        name: str,
    ):
        """
        Args:
            tracks (Tracks): The tracks to set the name on.
            track_id (int): The tracklet whose nodes should all get the new name.
            name (str): The new name.
        """
        super().__init__(tracks)
        self.track_id = track_id
        self.nodes = [node for _time, node in tracks.get_track_node_times(track_id)]
        self.prev_names = {node: self.tracks.get_node_attr(node, "name") for node in self.nodes}
        self.new_name = name
        self._apply()

    def inverse(self) -> Action:
        """Restore each node's previous name individually.

        A `SetName` may be undoing a prior state where nodes in the tracklet
        didn't all share one name (e.g. inverting a merge), so this restores
        per-node rather than re-applying a single uniform name.
        """
        actions = [
            UpdateNodeAttrs(self.tracks, node, {"name": prev_name})
            for node, prev_name in self.prev_names.items()
        ]
        return ActionGroup(self.tracks, actions)

    def _apply(self) -> None:
        """Set the new name on every node in the tracklet."""
        for node in self.nodes:
            self.tracks._set_node_attr(node, "name", self.new_name)
        self.tracks.notify_annotators(self)


def _find_stale_tracklets(
    sulston_lineage: nx.DiGraph,
    name_to_track_id: dict[str, int],
    old_name: str,
) -> list[int]:
    """Every tracklet downstream of `old_name` whose name is now stale after a rename.

    Walks the Sulston lineage's children of `old_name`, looks each child
    name up as a *currently-existing* tracklet (via `name_to_track_id`:
    these are tracklets still named after the renamed-away-from `old_name`,
    so their names are stale now that `old_name` belongs to a different
    tracklet), and recurses into each found tracklet using *its own current
    name* -- not the Sulston name it should eventually become, which doesn't
    exist as a tracklet yet. Stops at Sulston leaves or at any child name
    with no current tracklet (nothing further to mark without more curation).

    Args:
        sulston_lineage:
        name_to_track_id: `{current name: track_id}` for the editable lineage
            (see `EmbryoLoaderWidget._track_ids_by_name_by_tracks`).
        old_name: The name a tracklet had before being renamed away from it.

    Returns:
        Every stale tracklet's track_id, in the order found (breadth-first
        from `old_name`'s immediate children downward).
    """

    stale_track_ids = []
    names_to_check = [old_name]
    while names_to_check:
        current_name = names_to_check.pop(0)
        for child_name in sulston_lineage.successors(current_name):
            child_track_id = name_to_track_id.get(child_name)
            if child_track_id is None:
                continue
            stale_track_ids.append(child_track_id)
            names_to_check.append(child_name)
    return stale_track_ids


class UserSetName(ActionGroup):
    """Rename a tracklet, and flag every now-stale descendant tracklet as unresolved.

    Renames `node`'s tracklet, then walks the Sulston lineage from the
    tracklet's *old* name to find every currently-named descendant tracklet
    whose name no longer makes sense under its new ancestor name, and marks
    each with the boolean `unresolved` feature (see `UNRESOLVED_FEATURE_KEY`)
    so they show up as a group in napari-track-edit's Groups tab for later
    review (see `UserResolveDiv`). The rename and every unresolved flag are
    one undo step.
    """

    def __init__(
        self,
        tracks: Tracks,
        node: int,
        name: str,
        sulston_lineage: nx.DiGraph,
        name_to_track_id: dict[str, int],
        _top_level: bool = True,
    ):
        """
        Args:
            tracks (Tracks): The tracks to rename a tracklet in.
            node (int): A node in the tracklet to rename.
            name (str): The tracklet's new name.
            sulston_lineage
            name_to_track_id (dict[str, int]): `{current name: track_id}` for
                this lineage (see
                `EmbryoLoaderWidget._track_ids_by_name_by_tracks`), used the
                same way.
            _top_level (bool): If True, add this action to the history and emit refresh.
                Set to False when used as a sub-action inside a compound action.
                Defaults to True.
        """
        super().__init__(tracks, actions=[])
        self.tracks: Tracks  # Narrow type from base class

        track_id = tracks.get_track_id(node)
        old_name = tracks.get_node_attr(node, "name")

        self.actions.append(SetName(tracks, track_id, name))

        stale_track_ids = _find_stale_tracklets(sulston_lineage, name_to_track_id, old_name)
        if stale_track_ids:
            _ensure_unresolved_feature(tracks)
            stale_nodes = [
                node
                for track_id in stale_track_ids
                for _time, node in tracks.get_track_node_times(track_id)
            ]
            self.actions.append(_set_nodes_attr(tracks, stale_nodes, UNRESOLVED_FEATURE_KEY, True))

        if _top_level:
            self.tracks.action_history.add_new_action(self)
            self.tracks.refresh.emit()


class UserSwapNames(ActionGroup):
    """Swap two tracklets' names, as one undo step.

    Just two `UserSetName`s (each renaming one tracklet to the other's old
    name, and flagging their own now-stale descendants as unresolved),
    composed so the whole swap undoes/redoes together.
    """

    def __init__(
        self,
        tracks: Tracks,
        node_a: int,
        node_b: int,
        sulston_lineage: nx.DiGraph,
        name_to_track_id: dict[str, int],
    ):
        """
        Args:
            tracks (Tracks): The tracks to swap names in.
            node_a (int): A node in the first tracklet.
            node_b (int): A node in the second tracklet.
            sulston_lineage ,
                used to find downstream tracklets affected by the swap.
            name_to_track_id (dict[str, int]): `{current name: track_id}` for
                this lineage (see
                `EmbryoLoaderWidget._track_ids_by_name_by_tracks`), used the
                same way.
        """
        super().__init__(tracks, actions=[])
        self.tracks: Tracks  # Narrow type from base class

        name_a = tracks.get_node_attr(node_a, "name")
        name_b = tracks.get_node_attr(node_b, "name")

        self.actions.append(
            UserSetName(tracks, node_a, name_b, sulston_lineage, name_to_track_id, _top_level=False)
        )
        self.actions.append(
            UserSetName(tracks, node_b, name_a, sulston_lineage, name_to_track_id, _top_level=False)
        )

        self.tracks.action_history.add_new_action(self)
        self.tracks.refresh.emit()


class UserResolveDiv(ActionGroup):
    """Assign names to a formerly-unresolved division's two daughters, as one undo step.

    Sets each daughter tracklet's name and clears the `unresolved` flag
    (see `UNRESOLVED_FEATURE_KEY`) on both, in one undo step.
    """

    def __init__(
        self,
        tracks: Tracks,
        track_id_a: int,
        track_id_b: int,
        name_a: str,
        name_b: str,
    ):
        """
        Args:
            tracks (Tracks): The tracks to resolve the division in.
            track_id_a (int): The first daughter tracklet.
            track_id_b (int): The second daughter tracklet.
            name_a (str): The first daughter's new name.
            name_b (str): The second daughter's new name.
        """
        super().__init__(tracks, actions=[])
        self.tracks: Tracks  # Narrow type from base class

        self.actions.append(SetName(tracks, track_id_a, name_a))
        self.actions.append(SetName(tracks, track_id_b, name_b))

        if UNRESOLVED_FEATURE_KEY in tracks.features:
            nodes = [
                node
                for track_id in (track_id_a, track_id_b)
                for _time, node in tracks.get_track_node_times(track_id)
            ]
            self.actions.append(_set_nodes_attr(tracks, nodes, UNRESOLVED_FEATURE_KEY, False))

        self.tracks.action_history.add_new_action(self)
        self.tracks.refresh.emit()
