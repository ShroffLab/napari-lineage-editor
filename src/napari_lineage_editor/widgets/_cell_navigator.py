from __future__ import annotations

from typing import TYPE_CHECKING

from napari.utils.notifications import show_warning
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    import networkx as nx
    from napari_track_edit.data_views import TracksViewer

NAME_KEY = "name"


class CellNavigatorWidget(QWidget):
    """Type a cell name, jump to the start or end of its tracklet in the active lineage.

    Autocompletes against `track_id_by_name`, rebuilt from the active tracks'
    `name` attribute automatically whenever the active tracks change.
    """

    def __init__(
        self,
        tracks_viewer: TracksViewer,
        sulston_lineage: nx.DiGraph,
    ) -> None:
        """
        Args:
            tracks_viewer: The active lineage's `TracksViewer`.
            sulston_lineage: The Sulston reference lineage, used to suggest
                valid names for a tracklet based on its graph-parent's
                current name, and to find a named cell's Sulston sibling.
        """
        super().__init__()
        self._tracks_viewer = tracks_viewer
        self._sulston_lineage = sulston_lineage
        self._track_id_by_name: dict[str, int] = {}

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Cell name, e.g. ABa")
        self._name_completer = QCompleter([])
        self._name_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._name_completer.setFilterMode(Qt.MatchContains)
        self._name_edit.setCompleter(self._name_completer)

        go_to_start_button = QPushButton("Go to start")
        go_to_start_button.clicked.connect(lambda: self._go_to_named_cell(end=False))
        go_to_end_button = QPushButton("Go to end")
        go_to_end_button.clicked.connect(lambda: self._go_to_named_cell(end=True))
        go_to_current_button = QPushButton("Go to current frame")
        go_to_current_button.clicked.connect(self._go_to_named_cell_at_current_time)
        name_nav_buttons = QHBoxLayout()
        name_nav_buttons.addWidget(go_to_start_button)
        name_nav_buttons.addWidget(go_to_current_button)
        name_nav_buttons.addWidget(go_to_end_button)

        select_siblings_button = QPushButton("Select Siblings")
        select_siblings_button.clicked.connect(self._on_select_siblings_clicked)

        self._selected_names_label = QLabel()
        self._valid_names_label = QLabel()

        layout = QVBoxLayout()
        layout.addWidget(self._name_edit)
        layout.addLayout(name_nav_buttons)
        layout.addWidget(select_siblings_button)
        layout.addWidget(self._selected_names_label)
        layout.addWidget(self._valid_names_label)
        self.setLayout(layout)

        self._tracks_viewer.tracks_updated.connect(self._on_tracks_updated)
        self._tracks_viewer.node_selection_updated.connect(self._on_selection_updated)

        self._rebuild_track_id_by_name()

    def set_name(self, name: str) -> None:
        """Populate the name text box, e.g. so another widget's selection is easy to compare."""
        self._name_edit.setText(name)

    def _rebuild_track_id_by_name(self) -> None:
        """Rebuild `track_id_by_name` from the active tracks' current `name` attribute values.

        Rebuilt from scratch on every tracks update (e.g. after a crop,
        which produces a new `Tracks` with its own node ids) rather than
        tracked incrementally, since this widget doesn't observe individual
        renames -- only whole-tracks swaps via `tracks_updated`.
        """
        self._track_id_by_name.clear()
        tracks = self._tracks_viewer.tracks
        if tracks is None or NAME_KEY not in tracks.graph_solution.node_attr_keys():
            return

        node_ids = list(tracks.graph_solution.node_ids())
        names = tracks.get_nodes_attr(node_ids, NAME_KEY)
        self._track_id_by_name.update(
            (name, tracks.get_track_id(node_id))
            for node_id, name in zip(node_ids, names, strict=True)
            if name is not None
        )

    def _get_sulston_parent(self, name: str) -> str | None:
        """The Sulston lineage's parent name for `name`, or `None` if `name` is a root."""
        parents = list(self._sulston_lineage.predecessors(name))
        if not parents:
            return None
        if len(parents) > 1:
            raise ValueError(f"Sulston lineage can't have multiple parents for {name!r}")
        return parents[0]

    def _get_sulston_children(self, name: str) -> list[str]:
        """The Sulston lineage's child names for `name` (0, 1, or 2 of them)."""
        return list(self._sulston_lineage.successors(name))

    def _get_sulston_sibling_name(self, name: str) -> str | None:
        """The Sulston lineage's other child of `name`'s parent, or `None` if there isn't one."""
        parent_name = self._get_sulston_parent(name)
        if parent_name is None:
            return None
        siblings = [
            child_name
            for child_name in self._get_sulston_children(parent_name)
            if child_name != name
        ]
        return siblings[0] if siblings else None

    def _get_valid_names(self, track_id: int) -> list[str]:
        """Name(s) that would satisfy Sulston for `track_id`, given its actual graph-parent.

        A tracklet with no graph-parent (the lineage's start) has no
        suggestion -- any root name is as good as another without more
        context. Otherwise, suggestions are all of Sulston's children of the
        parent's *current* name -- both daughters, even if one is already
        held by a sibling tracklet elsewhere, since the user may want to
        swap rather than only fill in whichever slot happens to be empty.
        """
        tracks = self._tracks_viewer.tracks
        _time, first_node = tracks.get_track_node_times(track_id)[0]
        preds = tracks.predecessors(first_node)
        if not preds:
            return []
        parent_track_id = tracks.get_track_id(preds[0])

        _time, parent_first_node = tracks.get_track_node_times(parent_track_id)[0]
        parent_name = tracks.get_node_attr(parent_first_node, NAME_KEY)
        if parent_name is None:
            return []

        return self._get_sulston_children(parent_name)

    def _on_tracks_updated(self, _refresh_view: bool | None) -> None:
        self._rebuild_track_id_by_name()
        self._name_completer.model().setStringList(sorted(self._track_id_by_name))
        self._on_selection_updated()

    def _on_selection_updated(self, *_args: object) -> None:
        """Show the selected node(s)' name(s), and valid-name suggestions for a single selection."""
        tracks = self._tracks_viewer.tracks
        selected_nodes = list(self._tracks_viewer.selected_nodes)
        if tracks is None or not selected_nodes:
            self._selected_names_label.setText("Selected: (none)")
            self._valid_names_label.setText("")
            return

        names = tracks.get_nodes_attr(selected_nodes, "name")
        self._selected_names_label.setText("Selected: " + ", ".join(names))

        if len(selected_nodes) == 1:
            track_id = tracks.get_track_id(selected_nodes[0])
            valid_names = self._get_valid_names(track_id)
            self._valid_names_label.setText(
                "Valid names: " + (", ".join(valid_names) if valid_names else "(none)")
            )
            self._name_edit.setText(names[0] or "")
        else:
            self._valid_names_label.setText("")

    def _on_select_siblings_clicked(self) -> None:
        """Select the current node plus its Sulston sibling's node at the same timepoint.

        Only acts on a single current selection -- there's no well-defined
        "sibling" for a multi-node selection. Does nothing if the selected
        cell has no name, no Sulston sibling, or that sibling's tracklet
        doesn't have a node at this exact timepoint (e.g. it hasn't started
        yet, or has already ended).
        """
        tracks = self._tracks_viewer.tracks
        selected_nodes = list(self._tracks_viewer.selected_nodes)
        if tracks is None or len(selected_nodes) != 1:
            return

        node = selected_nodes[0]
        name = tracks.get_node_attr(node, "name")
        if name is None:
            return

        sibling_name = self._get_sulston_sibling_name(name)
        if sibling_name is None:
            return

        sibling_track_id = self._track_id_by_name.get(sibling_name)
        if sibling_track_id is None:
            return

        time = tracks.get_time(node)
        sibling_node = next(
            (n for t, n in tracks.get_track_node_times(sibling_track_id) if t == time), None
        )
        if sibling_node is None:
            return

        self._tracks_viewer.selected_nodes.add(sibling_node, append=True)

    def _go_to_named_cell(self, *, end: bool) -> None:
        """Select the start (or end) node of the named cell's tracklet in the active lineage.

        Searches whichever lineage is currently displayed
        (`tracks_viewer.tracks`), using `track_id_by_name` (name maps 1:1 to
        tracklet).

        Args:
            end: If `True`, select the tracklet's last (max time) node;
                otherwise its first (min time) node.
        """
        tracks = self._tracks_viewer.tracks
        if tracks is None:
            return

        name = self._name_edit.text().strip()
        track_id = self._track_id_by_name.get(name)
        if track_id is None:
            return

        node_times = tracks.get_track_node_times(track_id)  # sorted by time
        _time, node = node_times[-1] if end else node_times[0]
        self._tracks_viewer.selected_nodes.add(node)

    def _go_to_named_cell_at_current_time(self) -> None:
        """Select the named cell's node at the viewer's current timepoint, if it has one.

        Unlike `_go_to_named_cell`, this doesn't move the time slider --
        it looks up whichever node the named tracklet has at whatever
        timepoint is already showing (e.g. after jumping there from the
        drift navigator), so the two can be compared side by side.
        """
        tracks = self._tracks_viewer.tracks
        if tracks is None:
            return

        name = self._name_edit.text().strip()
        track_id = self._track_id_by_name.get(name)
        if track_id is None:
            show_warning(f"No tracklet named {name!r}")
            return

        current_time = int(self._tracks_viewer.viewer.dims.current_step[0])
        node = next(
            (n for t, n in tracks.get_track_node_times(track_id) if t == current_time), None
        )
        if node is None:
            show_warning(f"{name!r} has no node at t={current_time}")
            return

        self._tracks_viewer.selected_nodes.add(node)
