from __future__ import annotations

from typing import TYPE_CHECKING

import napari
import pandas as pd
from qtpy.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QWidget

if TYPE_CHECKING:
    from ._cell_navigator import CellNavigatorWidget

_DRIFT_POINTS_SIZE = 8


class DriftNavigatorWidget(QWidget):
    """Shows how many drifted points remain, and jumps to whichever one the spinbox points at.

    The spinbox's up/down arrows step through points one at a time, same as
    a next/previous button pair would, but also let the index be set
    directly or stepped back to revisit one after navigating away. "Go to"
    selects that point and moves the viewer's time slider (and z-slice) to
    its location. The count updates live off `drift_layer.data`, so
    deleting a point (the way a drifted position gets marked resolved --
    see `scripts/fix_conflicts.py`) is reflected without any extra wiring.

    Also owns loading/saving the drift CSV itself (`load_csv`/`save_csv`),
    including adding/removing its `drift_layer` from the viewer, so callers
    only need to hand it a path.
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
        self._csv_path = None

        self._count_label = QLabel()
        self._current_point_label = QLabel()
        self._index_spinbox = QSpinBox()
        self._index_spinbox.setMinimum(0)
        self._index_spinbox.valueChanged.connect(self._refresh_current_point_label)
        go_to_button = QPushButton("Go to")
        go_to_button.clicked.connect(self._on_go_to_clicked)

        layout = QHBoxLayout()
        layout.addWidget(self._count_label)
        layout.addWidget(self._index_spinbox)
        layout.addWidget(go_to_button)
        layout.addWidget(self._current_point_label)
        self.setLayout(layout)

        self._refresh_count()
        self._refresh_current_point_label()

    def set_drift_layer(self, drift_layer: napari.layers.Points | None) -> None:
        """Point this widget at a new drift layer (e.g. after loading a different embryo)."""
        self._drift_layer = drift_layer
        self._index_spinbox.setValue(0)
        self._refresh_count()
        self._refresh_current_point_label()

    def load_csv(self, csv_path) -> None:
        """Replace the drift layer with whatever `csv_path` (t, z, y, x, name, dist) contains."""
        if self._drift_layer is not None and self._drift_layer in self._viewer.layers:
            self._viewer.layers.remove(self._drift_layer)
        self._csv_path = csv_path
        self.set_drift_layer(None)

        if not csv_path.exists():
            return

        drift_df = pd.read_csv(csv_path)
        if len(drift_df) == 0:
            return

        points = drift_df[["t", "z", "y", "x"]].to_numpy()
        drift_layer = napari.layers.Points(
            points,
            name="Drifted points",
            features={"name": drift_df["name"], "dist": drift_df["dist"]},
            text="name",
            size=_DRIFT_POINTS_SIZE,
            face_color="red",
            symbol="x",
        )
        self._viewer.add_layer(drift_layer)
        self.set_drift_layer(drift_layer)

    def save_csv(self) -> None:
        """Overwrite the loaded CSV with whatever points remain in the drift layer.

        The user marks a drifted point resolved by deleting it from the
        layer (napari's own point-deletion tool) -- so whatever's left when
        this is called is the current set of still-unresolved drift points.
        No-ops if nothing's been loaded via `load_csv`.
        """
        if self._csv_path is None:
            return

        if self._drift_layer is None or self._drift_layer not in self._viewer.layers:
            pd.DataFrame(columns=["t", "z", "y", "x", "name", "dist"]).to_csv(
                self._csv_path, index=False
            )
            print(f"saved drift to {self._csv_path}")
            return

        points = self._drift_layer.data
        names = self._drift_layer.features["name"].to_numpy()
        dists = self._drift_layer.features["dist"].to_numpy()
        drift_df = pd.DataFrame(
            {
                "t": points[:, 0],
                "z": points[:, 1],
                "y": points[:, 2],
                "x": points[:, 3],
                "name": names,
                "dist": dists,
            }
        )
        drift_df.to_csv(self._csv_path, index=False)
        print(f"saved drift to {self._csv_path}")

    def _refresh_count(self) -> None:
        count = 0 if self._drift_layer is None else len(self._drift_layer.data)
        self._count_label.setText(f"Drifted points remaining: {count}")
        self._index_spinbox.setMaximum(max(count - 1, 0))

    def _refresh_current_point_label(self) -> None:
        if self._drift_layer is None or len(self._drift_layer.data) == 0:
            self._current_point_label.setText("")
            return

        index = self._index_spinbox.value()
        name = self._drift_layer.features["name"].iloc[index]
        dist = self._drift_layer.features["dist"].iloc[index]
        self._current_point_label.setText(f"{name} (dist to match: {dist:.2f})")

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
