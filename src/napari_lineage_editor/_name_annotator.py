from __future__ import annotations

from typing import TYPE_CHECKING

from funtracks.annotators import GraphAnnotator

from napari_lineage_editor._actions import (
    UNRESOLVED_FEATURE_KEY,
    SetName,
    _ensure_unresolved_feature,
)

if TYPE_CHECKING:
    import networkx as nx
    from funtracks.actions import BasicAction
    from funtracks.data_model import Tracks
    from funtracks.features import Feature

DEFAULT_NAME_KEY = "name"


class NameAnnotator(GraphAnnotator):
    """A graph annotator that maintains a `{name: tracklet_id}` lookup for the `name` feature.

    Name maps 1:1 to tracklet in this project. This annotator doesn't compute
    `name` itself (it's assigned by `SetName`/`UserSwapNames`, loaded from a
    CSV, etc.) -- it only keeps `track_id_by_name` in sync, so callers (e.g.
    `CellNavigatorWidget`, `EditCellNamesWidget`) don't each rebuild that
    lookup from scratch by rescanning every node.

    Also keeps every tracklet's `unresolved` flag (see `UNRESOLVED_FEATURE_KEY`)
    in sync with whether its current name actually satisfies `sulston_lineage`
    given its real graph-parent's current name -- there's no separate
    "resolve" action; a tracklet is unresolved exactly when its name doesn't
    currently satisfy Sulston, recomputed on every `SetName` (see
    `is_valid_name`/`_revalidate`), including cascading into descendants
    whose own validity depends on this tracklet's name.

    Attributes:
        track_id_by_name (dict[str, int]): A mapping from cell name to the
            tracklet id currently holding that name.
        invalid_track_ids (set[int]): Tracklet ids currently marked
            `unresolved` (see `UNRESOLVED_FEATURE_KEY`), for display in the
            UI by name/tracklet id rather than by cell/node.

    Args:
        tracks (Tracks): The tracks to annotate.
        sulston_lineage (nx.DiGraph): The Sulston reference lineage, used to
            check whether a tracklet's name satisfies its parent's.
        name_key (str | None, optional): The node attribute holding the cell
            name. Defaults to DEFAULT_NAME_KEY.
    """

    @classmethod
    def get_available_features(cls, ndim: int = 3) -> dict[str, Feature]:
        """Get all features that can be computed by this annotator.

        Args:
            ndim: Total number of dimensions including time (unused for this
                annotator, kept for API consistency). Defaults to 3.

        Returns:
            Dictionary mapping feature keys to Feature definitions.
        """
        return {
            DEFAULT_NAME_KEY: {
                "feature_type": "node",
                "value_type": "str",
                "num_values": 1,
                "display_name": "Name",
                "default_value": None,
            }
        }

    @property
    def graph(self):
        """`name` is assigned by `SetName`, which only ever operates on solution nodes.

        Overrides the base default of `graph_full`, same reasoning as
        `TrackAnnotator`.
        """
        return self.tracks.graph_solution

    def __init__(
        self,
        tracks: Tracks,
        sulston_lineage: nx.DiGraph,
        name_key: str | None = DEFAULT_NAME_KEY,
    ):
        self.tracks: Tracks  # Narrow type from base class
        self.sulston_lineage = sulston_lineage
        self.name_key = name_key if name_key is not None else DEFAULT_NAME_KEY

        feats = self.get_available_features()
        super().__init__(tracks, {self.name_key: feats[DEFAULT_NAME_KEY]})

        # Mutated in place (never reassigned) everywhere it's updated, so a
        # caller that captures a bare reference to this dict (e.g. to avoid
        # holding the whole annotator) never goes stale.
        self.track_id_by_name: dict[str, int] = {}
        self.invalid_track_ids: set[int] = set()
        if tracks.graph_solution.num_nodes() > 0:
            self._rebuild_track_id_by_name()

    def _rebuild_track_id_by_name(self) -> None:
        """Mutate `track_id_by_name` in place to match the current `name` attribute values."""
        self.track_id_by_name.clear()
        if self.name_key not in self.graph.node_attr_keys():
            return
        node_ids = list(self.graph.node_ids())
        names = self.tracks.get_nodes_attr(node_ids, self.name_key)
        self.track_id_by_name.update(
            (name, self.tracks.get_track_id(node_id))
            for node_id, name in zip(node_ids, names, strict=True)
            if name is not None
        )

    def get_sulston_parent(self, name: str) -> str | None:
        """The Sulston lineage's parent name for `name`, or `None` if `name` is a root."""
        parents = list(self.sulston_lineage.predecessors(name))
        if not parents:
            return None
        if len(parents) > 1:
            raise ValueError(f"Sulston lineage can't have multiple parents for {name!r}")
        return parents[0]

    def get_sulston_children(self, name: str) -> list[str]:
        """The Sulston lineage's child names for `name` (0, 1, or 2 of them)."""
        return list(self.sulston_lineage.successors(name))

    def get_sulston_sibling_name(self, name: str) -> str | None:
        """The Sulston lineage's other child of `name`'s parent, or `None` if there isn't one.

        Purely name-based (no tracklet lookup): `name`'s own tracklet
        doesn't need to exist yet for this to answer "what name would its
        sibling have".
        """
        parent_name = self.get_sulston_parent(name)
        if parent_name is None:
            return None
        siblings = [
            child_name
            for child_name in self.get_sulston_children(parent_name)
            if child_name != name
        ]
        return siblings[0] if siblings else None

    def get_parent_track_id(self, track_id: int) -> int | None:
        """The tracklet id immediately upstream of `track_id` in the graph, if any."""
        _time, first_node = self.tracks.get_track_node_times(track_id)[0]
        preds = self.tracks.predecessors(first_node)
        if not preds:
            return None
        return self.tracks.get_track_id(preds[0])

    def get_child_track_ids(self, track_id: int) -> list[int]:
        """The tracklet id(s) immediately downstream of `track_id` in the graph."""
        _time, last_node = self.tracks.get_track_node_times(track_id)[-1]
        succs = self.tracks.successors(last_node)
        return [self.tracks.get_track_id(succ) for succ in succs]

    def get_valid_names(self, track_id: int) -> list[str]:
        """Name(s) that would satisfy Sulston for `track_id`, given its actual graph-parent.

        A tracklet with no graph-parent (the lineage's start) has no
        suggestion -- any root name is as good as another without more
        context. Otherwise, suggestions are all of Sulston's children of the
        parent's *current* name -- both daughters, even if one is already
        held by a sibling tracklet elsewhere, since the user may want to
        swap rather than only fill in whichever slot happens to be empty.

        Args:
            track_id: The tracklet to suggest names for.

        Returns:
            Every Sulston child name of the parent tracklet's name, or `[]`
            if the parent has no name yet or `track_id` has no graph-parent.
        """
        parent_track_id = self.get_parent_track_id(track_id)
        if parent_track_id is None:
            return []

        _time, parent_first_node = self.tracks.get_track_node_times(parent_track_id)[0]
        parent_name = self.tracks.get_node_attr(parent_first_node, self.name_key)
        if parent_name is None:
            return []

        return self.get_sulston_children(parent_name)

    def compute(self, feature_keys: list[str] | None = None) -> None:
        """Rebuild `track_id_by_name` from the current `name` attribute values.

        `name` itself isn't computed here (see class docstring) -- this only
        (re)builds the bookkeeping, e.g. after loading a lineage whose `name`
        column is already populated.

        Args:
            feature_keys: Optional list of specific feature keys to compute.
                If None, computes all currently active features. Keys not in
                self.features (not enabled) are ignored.
        """
        keys_to_compute = self._filter_feature_keys(feature_keys)
        if self.name_key not in keys_to_compute:
            return
        self._rebuild_track_id_by_name()

    def update(self, action: BasicAction) -> None:
        """Write the new `name` onto every tracklet node, update bookkeeping, re-validate.

        `SetName` only records the intent (which tracklet, which new name) --
        this annotator does the actual attribute write, the same way
        `TrackAnnotator` writes `tracklet_id`/`lineage_id` in response to
        `UpdateTrackIDs` rather than the action writing them itself. It then
        re-checks the renamed tracklet (and cascades into its descendants,
        since a rename here can change whether they satisfy Sulston too)
        against `sulston_lineage`, marking each `unresolved` or resolved
        accordingly -- there's no separate "resolve" action; a tracklet is
        only ever unresolved because its current name doesn't satisfy
        Sulston right now.

        Args:
            action (BasicAction): The action that triggered this update.
        """
        if self.name_key not in self.features:
            return
        if not isinstance(action, SetName):
            return

        if action.nodes:
            self.graph.update_node_attrs(
                attrs={self.name_key: [action.new_name] * len(action.nodes)},
                node_ids=action.nodes,
            )

        old_name = next(iter(action.prev_names.values()), None)
        if old_name is not None:
            self.track_id_by_name.pop(old_name, None)
        self.track_id_by_name[action.new_name] = action.track_id

        self._revalidate(action.track_id)

    def is_valid_name(self, track_id: int) -> bool:
        """Whether `track_id`'s current name satisfies Sulston given its actual graph-parent.

        A tracklet with no graph-parent (the lineage's start) is valid iff
        Sulston also has it as a root (no Sulston parent). A tracklet with a
        graph-parent is valid iff Sulston says its name's parent is that
        parent tracklet's *current* name -- so an unnamed/un-Sulston parent
        (nothing to check against yet) is treated as valid, not stale, until
        the parent itself gets a name.
        """
        _time, first_node = self.tracks.get_track_node_times(track_id)[0]
        name = self.tracks.get_node_attr(first_node, self.name_key)
        if name is None:
            return True

        sulston_parent = self.get_sulston_parent(name)
        parent_track_id = self.get_parent_track_id(track_id)
        if parent_track_id is None:
            return sulston_parent is None

        parent_name = self.tracks.get_node_attr(
            self.tracks.get_track_node_times(parent_track_id)[0][1], self.name_key
        )
        if parent_name is None:
            return True
        return sulston_parent == parent_name

    def _revalidate(self, track_id: int) -> None:
        """Re-check `track_id` and cascade into its children, updating `unresolved`/bookkeeping.

        Stops cascading into a subtree as soon as a child's validity doesn't
        change, since nothing downstream of it could have changed either.

        Args:
            track_id: The tracklet to re-check first.
        """
        _ensure_unresolved_feature(self.tracks)

        track_ids_to_check = [track_id]
        while track_ids_to_check:
            current_track_id = track_ids_to_check.pop(0)
            was_invalid = current_track_id in self.invalid_track_ids
            is_valid = self.is_valid_name(current_track_id)

            if is_valid:
                self.invalid_track_ids.discard(current_track_id)
            else:
                self.invalid_track_ids.add(current_track_id)

            nodes = [node for _time, node in self.tracks.get_track_node_times(current_track_id)]
            self.tracks._set_nodes_attr(nodes, UNRESOLVED_FEATURE_KEY, [not is_valid] * len(nodes))

            if was_invalid != (not is_valid) or current_track_id == track_id:
                track_ids_to_check.extend(self.get_child_track_ids(current_track_id))

    def change_key(self, old_key: str, new_key: str) -> None:
        """Rename a feature key in this annotator.

        Overrides base implementation to also update the name_key instance variable.

        Args:
            old_key: Existing key to rename.
            new_key: New key to replace it with.

        Raises:
            KeyError: If old_key does not exist.
        """
        super().change_key(old_key, new_key)
        if self.name_key == old_key:
            self.name_key = new_key
