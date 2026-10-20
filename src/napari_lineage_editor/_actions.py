from __future__ import annotations

from typing import TYPE_CHECKING

from funtracks.actions import Action, ActionGroup, BasicAction

if TYPE_CHECKING:
    from funtracks.data_model import Tracks


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
        """Restore the tracklet's previous name, as another `SetName`.

        Assumes the same state as just existed: all of `self.nodes` shared
        `self.new_name` immediately before this inverse runs, so any one of
        `prev_names`' values represents "before" well enough.
        """
        prev_name = next(iter(self.prev_names.values()))
        return SetName(self.tracks, self.track_id, prev_name)

    def _apply(self) -> None:
        """Notify annotators of the rename; `NameAnnotator` does the actual attribute write."""
        self.tracks.notify_annotators(self)


class UserSetName(ActionGroup):
    """Rename a tracklet, as one undo step.

    Renames `node`'s tracklet to `name`. Marking now-stale descendant
    tracklets as unresolved (see `UNRESOLVED_FEATURE_KEY`) happens inside
    `SetName`/`NameAnnotator`, not here -- so any renaming path, not just
    this one, gets it automatically.
    """

    def __init__(
        self,
        tracks: Tracks,
        node: int,
        name: str,
        _top_level: bool = True,
    ):
        """
        Args:
            tracks (Tracks): The tracks to rename a tracklet in.
            node (int): A node in the tracklet to rename.
            name (str): The tracklet's new name.
            _top_level (bool): If True, add this action to the history and emit refresh.
                Set to False when used as a sub-action inside a compound action.
                Defaults to True.
        """
        super().__init__(tracks, actions=[])
        self.tracks: Tracks  # Narrow type from base class

        track_id = tracks.get_track_id(node)
        self.actions.append(SetName(tracks, track_id, name))

        if _top_level:
            self.tracks.action_history.add_new_action(self)
            self.tracks.refresh.emit()


class UserSwapNames(ActionGroup):
    """Swap two tracklets' names, as one undo step.

    Just two `UserSetName`s (each renaming one tracklet to the other's old
    name), composed so the whole swap undoes/redoes together.
    """

    def __init__(
        self,
        tracks: Tracks,
        node_a: int,
        node_b: int,
    ):
        """
        Args:
            tracks (Tracks): The tracks to swap names in.
            node_a (int): A node in the first tracklet.
            node_b (int): A node in the second tracklet.
        """
        super().__init__(tracks, actions=[])
        self.tracks: Tracks  # Narrow type from base class

        name_a = tracks.get_node_attr(node_a, "name")
        name_b = tracks.get_node_attr(node_b, "name")

        self.actions.append(UserSetName(tracks, node_a, name_b, _top_level=False))
        self.actions.append(UserSetName(tracks, node_b, name_a, _top_level=False))

        self.tracks.action_history.add_new_action(self)
        self.tracks.refresh.emit()
