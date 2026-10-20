from __future__ import annotations

from typing import TYPE_CHECKING

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
    from funtracks.data_model.tracks import Tracks
    from napari_track_edit.data_views import TracksViewer


class CellNavigatorWidget(QWidget):
    """Type a cell name, jump to the start or end of its tracklet in the active lineage.

    Autocompletes against whichever lineage is currently the active
    (editable) tracks in `tracks_viewer`, refreshed automatically whenever
    the active tracks change.
    """

    def __init__(self, tracks_viewer: TracksViewer) -> None:
        super().__init__()
        self._tracks_viewer = tracks_viewer
        # Maps id(tracks) -> {cell name: tracklet id}, built once per active
        # lineage (name maps 1:1 to tracklet) so a lookup doesn't rescan
        # every node on every click.
        self._track_ids_by_name_by_tracks: dict[int, dict[str, int]] = {}

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
        name_nav_buttons = QHBoxLayout()
        name_nav_buttons.addWidget(go_to_start_button)
        name_nav_buttons.addWidget(go_to_end_button)

        self._selected_names_label = QLabel()

        layout = QVBoxLayout()
        layout.addWidget(self._name_edit)
        layout.addLayout(name_nav_buttons)
        layout.addWidget(self._selected_names_label)
        self.setLayout(layout)

        self._tracks_viewer.tracks_updated.connect(self._on_tracks_updated)
        self._tracks_viewer.node_selection_updated.connect(self._on_selection_updated)

    def _on_tracks_updated(self, _refresh_view: bool | None) -> None:
        tracks = self._tracks_viewer.tracks
        if tracks is not None:
            self._refresh_name_cache(tracks)
        self._on_selection_updated()

    def _on_selection_updated(self, *_args: object) -> None:
        """Show the name(s) of the currently selected node(s) in the active lineage."""
        tracks = self._tracks_viewer.tracks
        selected_nodes = list(self._tracks_viewer.selected_nodes)
        if tracks is None or not selected_nodes:
            self._selected_names_label.setText("Selected: (none)")
            return

        names = tracks.get_nodes_attr(selected_nodes, "name")
        self._selected_names_label.setText("Selected: " + ", ".join(names))

    def _refresh_name_cache(self, tracks: Tracks) -> None:
        """Rebuild the `{name: tracklet id}` cache `_go_to_named_cell` uses.

        Name maps 1:1 to tracklet. Also refreshes the cell-name text field's
        autocomplete suggestions to match.
        """
        node_ids = list(tracks.graph_solution.node_ids())
        names = tracks.get_nodes_attr(node_ids, "name")
        track_ids_by_name = {
            name: tracks.get_track_id(node_id)
            for node_id, name in zip(node_ids, names, strict=True)
        }
        self._track_ids_by_name_by_tracks[id(tracks)] = track_ids_by_name
        self._name_completer.model().setStringList(sorted(track_ids_by_name))

    def _go_to_named_cell(self, *, end: bool) -> None:
        """Select the start (or end) node of the named cell's tracklet in the active lineage.

        Searches whichever lineage is currently displayed
        (`tracks_viewer.tracks`), using the `{name: track_id}` cache built
        when that lineage was loaded (name maps 1:1 to tracklet).

        Args:
            end: If `True`, select the tracklet's last (max time) node;
                otherwise its first (min time) node.
        """
        tracks = self._tracks_viewer.tracks
        if tracks is None:
            return
        track_ids_by_name = self._track_ids_by_name_by_tracks.get(id(tracks))
        if track_ids_by_name is None:
            return

        name = self._name_edit.text().strip()
        track_id = track_ids_by_name.get(name)
        if track_id is None:
            return

        node_times = tracks.get_track_node_times(track_id)  # sorted by time
        _time, node = node_times[-1] if end else node_times[0]
        self._tracks_viewer.selected_nodes.add(node)
