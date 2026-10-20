from __future__ import annotations

from typing import TYPE_CHECKING

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from napari_lineage_editor import UserSwapNames

if TYPE_CHECKING:
    from napari_track_edit.data_views import TracksViewer


class EditCellNamesWidget(QWidget):
    """Buttons for the implemented cell-naming user actions (see `napari_lineage_editor._actions`).

    Acts on whichever lineage is currently the active (editable) tracks in
    `tracks_viewer`. Also lists every tracklet currently marked `unresolved`
    (its name doesn't satisfy Sulston given its actual graph-parent's
    current name -- see `NameAnnotator`), by name/tracklet id; clicking an
    entry selects that tracklet's first node.
    """

    def __init__(
        self,
        tracks_viewer: TracksViewer,
        track_id_by_name: dict[str, int],
        invalid_track_ids: set[int],
    ) -> None:
        """
        Args:
            tracks_viewer: The active lineage's `TracksViewer`.
            track_id_by_name: `NameAnnotator.track_id_by_name` for the active
                tracks -- held by reference and mutated in place by the
                annotator, so this widget always sees the current mapping.
            invalid_track_ids: `NameAnnotator.invalid_track_ids` for the
                active tracks, held the same way.
        """
        super().__init__()
        self._tracks_viewer = tracks_viewer
        self._track_id_by_name = track_id_by_name
        self._invalid_track_ids = invalid_track_ids

        self._swap_names_button = QPushButton("Swap Names")
        self._swap_names_button.setEnabled(False)
        self._swap_names_button.clicked.connect(self._on_swap_names_clicked)

        self._invalid_list = QListWidget()
        self._invalid_list.itemClicked.connect(self._on_invalid_item_clicked)

        layout = QVBoxLayout()
        layout.addWidget(self._swap_names_button)
        layout.addWidget(QLabel("Unresolved tracklets"))
        layout.addWidget(self._invalid_list)
        self.setLayout(layout)

        self._tracks_viewer.tracks_updated.connect(self._on_tracks_updated)
        self._tracks_viewer.node_selection_updated.connect(self._on_selection_updated)

    def set_name_lookups(
        self, track_id_by_name: dict[str, int], invalid_track_ids: set[int]
    ) -> None:
        """Point this widget at a new `NameAnnotator`'s lookups (e.g. after a crop)."""
        self._track_id_by_name = track_id_by_name
        self._invalid_track_ids = invalid_track_ids

    def _on_tracks_updated(self, _refresh_view: bool | None) -> None:
        self._refresh_invalid_list()
        self._on_selection_updated()

    def _refresh_invalid_list(self) -> None:
        self._invalid_list.clear()

        tracks = self._tracks_viewer.tracks
        if tracks is None:
            return

        name_by_track_id = {track_id: name for name, track_id in self._track_id_by_name.items()}

        # Sorted by the tracklet's first timepoint, not by tracklet id, since
        # scanning conflicts in temporal order matches how a user actually
        # works through a lineage.
        rows = []
        for track_id in self._invalid_track_ids:
            node_times = tracks.get_track_node_times(track_id)
            start_time = node_times[0][0] if node_times else None
            rows.append((start_time, track_id))
        rows.sort(key=lambda row: (row[0] is None, row[0]))

        for start_time, track_id in rows:
            name = name_by_track_id.get(track_id, "?")
            time_label = start_time if start_time is not None else "?"
            item = QListWidgetItem(f"t={time_label}: {name} (tracklet {track_id})")
            item.setData(Qt.ItemDataRole.UserRole, track_id)
            self._invalid_list.addItem(item)

    def _on_invalid_item_clicked(self, item: QListWidgetItem) -> None:
        tracks = self._tracks_viewer.tracks
        if tracks is None:
            return
        track_id = item.data(Qt.ItemDataRole.UserRole)
        node_times = tracks.get_track_node_times(track_id)
        if not node_times:
            return
        _time, node = node_times[0]
        self._tracks_viewer.selected_nodes.add(node)

    def _on_selection_updated(self, *_args: object) -> None:
        """Enable "Swap Names" only when exactly two nodes are selected."""
        self._swap_names_button.setEnabled(len(self._tracks_viewer.selected_nodes) == 2)

    def _on_swap_names_clicked(self) -> None:
        tracks = self._tracks_viewer.tracks
        if tracks is None:
            return

        node_a, node_b = list(self._tracks_viewer.selected_nodes)
        UserSwapNames(tracks, node_a, node_b)
        self._refresh_invalid_list()
