from __future__ import annotations

from typing import TYPE_CHECKING

from napari_track_edit.data_views.views.tree_view.tree_widget import TreeWidget
from qtpy.QtWidgets import QCheckBox, QVBoxLayout, QWidget

if TYPE_CHECKING:
    import napari
    from napari_track_edit.data_views import TracksViewer


class TreeViewControlWidget(QWidget):
    """One checkbox per loaded lineage, opening/closing its tree view dock.

    Checking a lineage's box opens a `TreeWidget` dock for its
    `TracksViewer`; unchecking closes it. Rebuilt from scratch on every
    `set_lineages` call (e.g. after loading a new embryo), closing whatever
    docks were open beforehand so stale lineages never leave an orphaned
    dock behind.
    """

    def __init__(self, viewer: napari.Viewer) -> None:
        super().__init__()
        self._viewer = viewer
        self._tracks_viewer_by_name: dict[str, TracksViewer] = {}
        self._dock_by_name: dict[str, QWidget] = {}
        self._checkbox_by_name: dict[str, QCheckBox] = {}

        self._layout = QVBoxLayout()
        self.setLayout(self._layout)

    def set_lineages(
        self,
        tracks_viewers_by_name: dict[str, TracksViewer],
        checked_name: str | None = None,
    ) -> None:
        """Replace the checkbox list to match the currently-loaded lineages.

        Closes every currently-open tree dock first, since the old
        `TracksViewer`s (and their lineages) may no longer be valid once new
        tracks are loaded.

        Args:
            tracks_viewers_by_name: The currently-loaded lineages.
            checked_name: If given, that lineage's checkbox starts checked
                (its tree view opens immediately) -- e.g. the active/editable
                lineage, which used to always have its tree view open by
                default. Every other lineage starts unchecked.
        """
        for name in list(self._dock_by_name):
            self._close_dock(name)

        for checkbox in self._checkbox_by_name.values():
            self._layout.removeWidget(checkbox)
            checkbox.deleteLater()
        self._checkbox_by_name.clear()

        self._tracks_viewer_by_name = dict(tracks_viewers_by_name)

        for name in self._tracks_viewer_by_name:
            checkbox = QCheckBox(name)
            checkbox.toggled.connect(lambda checked, name=name: self._on_toggled(name, checked))
            self._layout.addWidget(checkbox)
            self._checkbox_by_name[name] = checkbox
            if name == checked_name:
                checkbox.setChecked(True)

    def _on_toggled(self, name: str, checked: bool) -> None:
        if checked:
            self._open_dock(name)
        else:
            self._close_dock(name)

    def _open_dock(self, name: str) -> None:
        if name in self._dock_by_name:
            return
        tracks_viewer = self._tracks_viewer_by_name.get(name)
        if tracks_viewer is None:
            return
        tree_widget = TreeWidget(self._viewer, tracks_viewer=tracks_viewer)
        dock = self._viewer.window.add_dock_widget(tree_widget, area="bottom", name=name)
        self._dock_by_name[name] = dock

    def _close_dock(self, name: str) -> None:
        dock = self._dock_by_name.pop(name, None)
        if dock is not None:
            self._viewer.window.remove_dock_widget(dock)
