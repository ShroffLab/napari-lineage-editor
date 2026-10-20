from __future__ import annotations

from typing import TYPE_CHECKING

from qtpy.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QWidget

if TYPE_CHECKING:
    import napari

    from ._cell_navigator import CellNavigatorWidget


class DriftNavigatorWidget(QWidget):
    """Shows how many drifted points remain, and jumps to whichever one the spinbox points at.

    The spinbox's up/down arrows step through points one at a time, same as
    a next/previous button pair would, but also let the index be set
    directly or stepped back to revisit one after navigating away. "Go to"
    selects that point and moves the viewer's time slider (and z-slice) to
    its location. The count updates live off `drift_layer.data`, so
    deleting a point (the way a drifted position gets marked resolved --
    see `scripts/fix_conflicts.py`) is reflected without any extra wiring.
    """

    def __init__(
        self,
        viewer: napari.Viewer,
        drift_layer: napari.layers.Points | None,
        cell_navigator_widget: CellNavigatorWidget | None = None,
    ) -> None:
        """
        Args:
            viewer: The napari viewer whose time slider "Go to" moves.
            drift_layer: The drifted-points `Points` layer to iterate over,
                or `None` if nothing's loaded yet.
            cell_navigator_widget: If given, "Go to" also populates its name
                text box with the selected drift point's name, so the user
                can easily compare against where that name currently sits
                in the active lineage.
        """
        super().__init__()
        self._viewer = viewer
        self._drift_layer = drift_layer
        self._cell_navigator_widget = cell_navigator_widget

        self._count_label = QLabel()
        self._index_spinbox = QSpinBox()
        self._index_spinbox.setMinimum(0)
        go_to_button = QPushButton("Go to")
        go_to_button.clicked.connect(self._on_go_to_clicked)

        layout = QHBoxLayout()
        layout.addWidget(self._count_label)
        layout.addWidget(self._index_spinbox)
        layout.addWidget(go_to_button)
        self.setLayout(layout)

        self._refresh_count()

    def set_drift_layer(self, drift_layer: napari.layers.Points | None) -> None:
        """Point this widget at a new drift layer (e.g. after loading a different embryo)."""
        self._drift_layer = drift_layer
        self._index_spinbox.setValue(0)
        self._refresh_count()

    def _refresh_count(self) -> None:
        count = 0 if self._drift_layer is None else len(self._drift_layer.data)
        self._count_label.setText(f"Drifted points remaining: {count}")
        self._index_spinbox.setMaximum(max(count - 1, 0))

    def _on_go_to_clicked(self) -> None:
        self._refresh_count()
        if self._drift_layer is None or len(self._drift_layer.data) == 0:
            return

        index = self._index_spinbox.value()
        point = self._drift_layer.data[index]

        self._drift_layer.selected_data = {index}

        if self._cell_navigator_widget is not None:
            name = self._drift_layer.features["name"].iloc[index]
            self._cell_navigator_widget.set_name(name)

        # Set world-coordinate `dims.point` directly (not `current_step`,
        # which is a step *index* and would need its own axis-by-axis
        # range/step conversion) -- same approach napari_track_edit's own
        # TracksLayerGroup.center_view uses to jump to a node's location, so
        # this lands on the right z-slice too, not just the right timepoint.
        dims_point = list(self._viewer.dims.point)
        for axis, value in enumerate(point):
            dims_point[axis] = value
        self._viewer.dims.point = tuple(dims_point)
