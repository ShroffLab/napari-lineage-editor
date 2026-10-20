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
    from collections.abc import Callable

    from napari_track_edit.data_views import TracksViewer


class CellNavigatorWidget(QWidget):
    """Type a cell name, jump to the start or end of its tracklet in the active lineage.

    Autocompletes against `track_id_by_name`, refreshed automatically
    whenever the active tracks change.
    """

    def __init__(
        self,
        tracks_viewer: TracksViewer,
        track_id_by_name: dict[str, int],
        get_valid_names: Callable[[int], list[str]],
    ) -> None:
        """
        Args:
            tracks_viewer: The active lineage's `TracksViewer`.
            track_id_by_name: `NameAnnotator.track_id_by_name` for the active
                tracks -- held by reference and mutated in place by the
                annotator, so this widget always sees the current mapping
                without needing to be told when it changes.
            get_valid_names: `NameAnnotator.get_valid_names`, bound to the
                active tracks' annotator -- used to suggest names for the
                selected node's tracklet based on its current graph-parent.
        """
        super().__init__()
        self._tracks_viewer = tracks_viewer
        self._track_id_by_name = track_id_by_name
        self._get_valid_names = get_valid_names

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
        self._valid_names_label = QLabel()

        layout = QVBoxLayout()
        layout.addWidget(self._name_edit)
        layout.addLayout(name_nav_buttons)
        layout.addWidget(self._selected_names_label)
        layout.addWidget(self._valid_names_label)
        self.setLayout(layout)

        self._tracks_viewer.tracks_updated.connect(self._on_tracks_updated)
        self._tracks_viewer.node_selection_updated.connect(self._on_selection_updated)

    def set_name_lookups(
        self, track_id_by_name: dict[str, int], get_valid_names: Callable[[int], list[str]]
    ) -> None:
        """Point this widget at a new `NameAnnotator`'s lookups (e.g. after a crop)."""
        self._track_id_by_name = track_id_by_name
        self._get_valid_names = get_valid_names

    def _on_tracks_updated(self, _refresh_view: bool | None) -> None:
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
        else:
            self._valid_names_label.setText("")

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
