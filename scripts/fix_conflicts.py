"""Load only each embryo's curated-by-Alyssa lineage, for resolving precomputed conflicts/drift.

Unlike `load_embryo.py`, this never loads images or other lineage
representations (zenodo, wormguides-jar-source, ...) as view-only overlays --
there's nothing to compare live. Instead, the comparison table and the
drifted-position markers both come from CSVs already written by
`scripts/export_lineage_conflicts.py` / `scripts/export_location_drift.py`,
so opening this doesn't require loading every other lineage's full tracks
just to see where they disagreed.
"""

from __future__ import annotations

import napari
import pandas as pd
from dataset_registry import Registry
from napari_track_edit.data_views import TracksViewer
from napari_track_edit.data_views.views.tree_view.tree_widget import TreeWidget
from qtpy.QtWidgets import QComboBox, QPushButton, QVBoxLayout, QWidget

from napari_lineage_editor import NameAnnotator
from napari_lineage_editor.data import (
    get_sulston,
    load_images,
    load_lineage,
    registry,
    save_tracks_to_csv,
    tracks_from_lineage_df,
)
from napari_lineage_editor.data._utils import to_local_path
from napari_lineage_editor.widgets import CellNavigatorWidget, ComparisonWidget, EditCellNamesWidget

REGISTRY_PATH = "janelia://nrs.hhmi.org/shroff/Pharynx_Project/wormguides-data/registry"
CURATED_SUBSTRING = "curated-by-alyssa"
RECONCILED_SUBSTRING = "reconciled-annotations"

_ACTIVE_POINTS_SIZE = 5
_DRIFT_POINTS_SIZE = 8


def _find_curated_repr_id(reg: Registry, sample_id: str) -> str | None:
    for repr_id in reg.list_representations(sample_id):
        representation = reg.load_representation(sample_id, repr_id)
        if representation.metadata.type == "lineage" and CURATED_SUBSTRING in repr_id:
            return repr_id
    return None


def _find_reconciled_repr_id(reg: Registry, sample_id: str) -> str | None:
    for repr_id in reg.list_representations(sample_id):
        representation = reg.load_representation(sample_id, repr_id)
        if representation.metadata.type == "lineage" and RECONCILED_SUBSTRING in repr_id:
            return repr_id
    return None


def _find_image_repr_id(reg: Registry, sample_id: str) -> str | None:
    """Prefer a per-timepoint (per-frame) image representation, falling back to per-slice.

    `CelegansImageRepMetadata.start_z`/`end_z` are `None` for a plain
    per-timepoint 3D tiff series and set for a per-timepoint-per-slice 2D
    tiff series (see `_celegans_metadata.py`) -- per-frame loads one file
    per timepoint instead of one per (timepoint, slice), so it's preferred
    when available.
    """
    image_repr_ids = [
        repr_id
        for repr_id in reg.list_representations(sample_id)
        if reg.load_representation(sample_id, repr_id).metadata.type == "image"
    ]
    for repr_id in image_repr_ids:
        if reg.load_representation(sample_id, repr_id).metadata.start_z is None:
            return repr_id
    return image_repr_ids[0] if image_repr_ids else None


class FixConflictsWidget(QWidget):
    """Pick an embryo, load its Reconciled Annotations lineage, and show its precomputed conflicts/drift.

    Only ever loads the Reconciled Annotations lineage (see
    `scripts/create_reconciled_annotations.py`) as the active (editable)
    tracks -- no images beyond the embryo's own, no other lineages as
    view-only overlays. The comparison table and drifted-position Points
    layer are both read from CSVs next to the curated-by-Alyssa lineage's
    CSV (see module docstring). "Save" overwrites the Reconciled
    Annotations CSV with the current edited tracks.
    """

    def __init__(self, viewer: napari.Viewer, reg: Registry) -> None:
        super().__init__()
        self.viewer = viewer
        self.registry = reg
        self.sulston_lineage = get_sulston(reg)
        self.reconciled_repres = None

        self._samples_by_name = {
            sample.name: sample
            for sample in sorted(
                (reg.load_sample(sample_id) for sample_id in reg.list_samples()),
                key=lambda sample: sample.name,
            )
            if _find_reconciled_repr_id(reg, sample.id) is not None
        }

        self._combo = QComboBox()
        self._combo.addItems(list(self._samples_by_name))

        load_button = QPushButton("Load")
        load_button.clicked.connect(self._on_load_clicked)

        self._save_button = QPushButton("Save")
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self._on_save_clicked)

        self.cell_navigator_widget = CellNavigatorWidget(
            TracksViewer.get_instance(viewer), {}, lambda _track_id: []
        )
        self.edit_cell_names_widget = EditCellNamesWidget(
            TracksViewer.get_instance(viewer), {}, set()
        )

        self.comparison_dock: object | None = None
        self.comparison_widget: ComparisonWidget | None = None
        self.conflicts_csv_path = None
        self.drift_layer: napari.layers.Points | None = None
        self.drift_csv_path = None
        self.image_layer: napari.layers.Image | None = None
        self.tracks = None

        tree_widget = TreeWidget(self.viewer)
        self.viewer.window.add_dock_widget(tree_widget, area="bottom")

        layout = QVBoxLayout()
        layout.addWidget(self._combo)
        layout.addWidget(load_button)
        layout.addWidget(self._save_button)
        layout.addWidget(self.cell_navigator_widget)
        layout.addWidget(self.edit_cell_names_widget)
        self.setLayout(layout)

    def _on_load_clicked(self) -> None:
        sample = self._samples_by_name[self._combo.currentText()]
        reconciled_repr_id = _find_reconciled_repr_id(self.registry, sample.id)
        self.reconciled_repres = self.registry.load_representation(sample.id, reconciled_repr_id)

        df = load_lineage(self.reconciled_repres, self.sulston_lineage, self.registry.path)
        self.tracks = tracks_from_lineage_df(df)

        tv = TracksViewer.get_instance(self.viewer)
        self._register_name_annotator(self.tracks)
        tv.update_tracks(self.tracks, self.reconciled_repres.name)
        self._set_points_size(tv, _ACTIVE_POINTS_SIZE)
        self._save_button.setEnabled(True)

        self._load_image(sample.id)

        curated_repr_id = _find_curated_repr_id(self.registry, sample.id)
        curated_repres = self.registry.load_representation(sample.id, curated_repr_id)
        csv_dir = to_local_path(curated_repres.path, self.registry.path).parent
        self._load_comparison(tv, csv_dir / f"lineage_conflicts_{sample.id}.csv")
        self._load_drift_layer(csv_dir / f"location_drift_{sample.id}.csv")

    def _on_save_clicked(self) -> None:
        if self.tracks is None or self.reconciled_repres is None:
            return
        out_path = to_local_path(self.reconciled_repres.path, self.registry.path)
        save_tracks_to_csv(self.tracks, out_path)
        print(f"saved {self.reconciled_repres.name} to {out_path}")

        if self.comparison_widget is not None and self.conflicts_csv_path is not None:
            self.comparison_widget.conflicts.to_csv(self.conflicts_csv_path, index=False)
            print(f"saved conflicts to {self.conflicts_csv_path}")

        if self.drift_csv_path is not None:
            self._save_drift_layer(self.drift_csv_path)

    def _load_image(self, sample_id: str) -> None:
        if self.image_layer is not None and self.image_layer in self.viewer.layers:
            self.viewer.layers.remove(self.image_layer)
            self.image_layer = None

        image_repr_id = _find_image_repr_id(self.registry, sample_id)
        if image_repr_id is None:
            return

        image_repres = self.registry.load_representation(sample_id, image_repr_id)
        images = load_images(image_repres, self.registry.path)
        # Offset the time axis by start_time so napari's shared time slider
        # lines up with the tracks' own real timepoint (see
        # EmbryoLoaderWidget._load_image_representation).
        translate = [0] * images.ndim
        translate[0] = image_repres.metadata.start_time
        self.image_layer = napari.layers.Image(
            images, name=image_repres.name, blending="additive", translate=translate
        )
        self.viewer.add_layer(self.image_layer)

    def _register_name_annotator(self, tracks) -> None:
        name_annotator = NameAnnotator(tracks, self.sulston_lineage)
        tracks.annotators.append(name_annotator)
        tracks.enable_features([name_annotator.name_key])

        self.cell_navigator_widget.set_name_lookups(
            name_annotator.track_id_by_name, name_annotator.get_valid_names
        )
        self.edit_cell_names_widget.set_name_lookups(
            name_annotator.track_id_by_name, name_annotator.invalid_track_ids
        )

    def _load_comparison(self, tv: TracksViewer, csv_path) -> None:
        if self.comparison_dock is not None:
            self.viewer.window.remove_dock_widget(self.comparison_dock)
            self.comparison_dock = None
            self.comparison_widget = None
            self.conflicts_csv_path = None

        if not csv_path.exists():
            return

        conflicts = pd.read_csv(csv_path, header=[0, 1])
        self.comparison_widget = ComparisonWidget(tv, conflicts)
        self.conflicts_csv_path = csv_path
        self.comparison_dock = self.viewer.window.add_dock_widget(
            self.comparison_widget, name="Conflicts", area="right"
        )

    def _load_drift_layer(self, csv_path) -> None:
        if self.drift_layer is not None and self.drift_layer in self.viewer.layers:
            self.viewer.layers.remove(self.drift_layer)
            self.drift_layer = None
            self.drift_csv_path = None

        if not csv_path.exists():
            return

        drift_df = pd.read_csv(csv_path)
        if len(drift_df) == 0:
            return

        points = drift_df[["t", "z", "y", "x"]].to_numpy()
        self.drift_layer = napari.layers.Points(
            points,
            name="Drifted points",
            features={"name": drift_df["name"]},
            text="name",
            size=_DRIFT_POINTS_SIZE,
            face_color="red",
            symbol="x",
        )
        self.drift_csv_path = csv_path
        self.viewer.add_layer(self.drift_layer)

    def _save_drift_layer(self, csv_path) -> None:
        """Overwrite the drift CSV with whatever points remain in `self.drift_layer`.

        The user marks a drifted point resolved by deleting it from the
        layer (napari's own point-deletion tool) -- so whatever's left when
        Save is clicked is the current set of still-unresolved drift points.
        """
        if self.drift_layer is None or self.drift_layer not in self.viewer.layers:
            pd.DataFrame(columns=["t", "z", "y", "x", "name"]).to_csv(csv_path, index=False)
            print(f"saved drift to {csv_path}")
            return

        points = self.drift_layer.data
        names = self.drift_layer.features["name"].to_numpy()
        drift_df = pd.DataFrame(
            {
                "t": points[:, 0],
                "z": points[:, 1],
                "y": points[:, 2],
                "x": points[:, 3],
                "name": names,
            }
        )
        drift_df.to_csv(csv_path, index=False)
        print(f"saved drift to {csv_path}")

    def _set_points_size(self, tracks_viewer: TracksViewer, size: int) -> None:
        points_layer = tracks_viewer.tracking_layers.points_layer
        if points_layer is not None:
            points_layer.current_size = size


def main() -> None:
    reg = registry(REGISTRY_PATH)

    viewer = napari.Viewer(title="Wormguides conflict fixer")
    widget = FixConflictsWidget(viewer, reg)
    viewer.window.add_dock_widget(widget, name="Fix Conflicts")
    napari.run()


if __name__ == "__main__":
    main()
