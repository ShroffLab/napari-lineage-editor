from __future__ import annotations

from typing import TYPE_CHECKING, Any

import napari
from psygnal import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from napari_lineage_editor.data import (
    get_sulston,
    load_images,
    load_lineage,
)

if TYPE_CHECKING:
    import pandas as pd
    from dataset_registry import Registry, Representation


SULSTON_SAMPLE_NAME = "Sulston"


class EmbryoLoaderWidget(QWidget):
    new_data = Signal(Any, Any, Any)

    def __init__(self, registry: Registry):
        super().__init__()
        self.registry = registry
        self.sulston_lineage = get_sulston(registry)
        self._samples_by_name = {
            sample.name: sample
            for sample in sorted(
                (registry.load_sample(sample_id) for sample_id in registry.list_samples()),
                key=lambda sample: sample.name,
            )
            if sample.name != SULSTON_SAMPLE_NAME
        }

        self._combo = QComboBox()
        self._combo.addItems(list(self._samples_by_name))
        self._combo.currentTextChanged.connect(self._on_sample_changed)

        self._representation_checkboxes_layout = QVBoxLayout()

        load_button = QPushButton("Load")
        load_button.clicked.connect(self._on_load_clicked)

        layout = QVBoxLayout()
        layout.addWidget(self._combo)
        layout.addLayout(self._representation_checkboxes_layout)
        layout.addWidget(load_button)
        self.setLayout(layout)

        self._on_sample_changed(self._combo.currentText())

    def _on_sample_changed(self, sample_name: str) -> None:
        while (item := self._representation_checkboxes_layout.takeAt(0)) is not None:
            item.widget().deleteLater()

        sample = self._samples_by_name[sample_name]
        for representation_id in self.registry.list_representations(sample.id):
            representation = self.registry.load_representation(sample.id, representation_id)
            checkbox = QCheckBox(representation.name)
            checkbox.setChecked(True)
            checkbox.setProperty("representation_id", representation.id)
            self._representation_checkboxes_layout.addWidget(checkbox)

    def _checked_representation_ids(self) -> list[str]:
        ids = []
        for i in range(self._representation_checkboxes_layout.count()):
            checkbox = self._representation_checkboxes_layout.itemAt(i).widget()
            if checkbox.isChecked():
                ids.append(checkbox.property("representation_id"))
        return ids

    def _load_image_representation(self, representation: Representation) -> napari.layers.Image:
        images = load_images(representation, self.registry.path)
        # Offset the time axis by start_time so napari's shared time slider
        # lines up layers on the same *real* timepoint, even when
        # representations of the same embryo start at different t (e.g.
        # embryo 2's flat series starts at t=0, its per-slice at t=1).
        translate = [0] * images.ndim
        translate[0] = representation.metadata.start_time
        return napari.layers.Image(
            images, name=representation.name, blending="additive", translate=translate
        )

    def _load_lineage_representation(
        self, representation: Representation
    ) -> tuple[pd.DataFrame, str]:
        """Load one lineage representation, either as the editable tracks or a view-only overlay.

        Only one lineage can be the editable tracks shown in the tree/table/
        menus at a time; every additional lineage checked in the same "Load"
        click is shown as a read-only overlay instead (see
        `TracksViewer.add_viewonly_tracks` -- plain Labels/Points/Tracks
        layers, no editing or tree/table support).

        Args:
            representation: The lineage representation to load.
        """
        lineage_df = load_lineage(representation, self.sulston_lineage, self.registry.path)

        return lineage_df, representation.name

    def _on_load_clicked(self) -> None:
        images = []
        track_dfs = []
        tracks_names = []
        sample = self._samples_by_name[self._combo.currentText()]
        representation_ids = self._checked_representation_ids()
        if not representation_ids:
            raise RuntimeError(f"no representation checked for sample {sample.name!r}")
        for representation_id in representation_ids:
            representation = self.registry.load_representation(sample.id, representation_id)
            if representation.metadata.type == "image":
                images.append(self._load_image_representation(representation))
            elif representation.metadata.type == "lineage":
                track, name = self._load_lineage_representation(representation)
                track_dfs.append(track)
                tracks_names.append(name)
        self.new_data.emit(images, track_dfs, tracks_names)
