from __future__ import annotations

from typing import TYPE_CHECKING, Any

import napari
from psygnal import Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from tracksdata import NodeAttr

from napari_lineage_editor.data import (
    get_sulston,
    load_images,
    load_lineage,
    tracks_from_lineage_df,
)

if TYPE_CHECKING:
    import pandas as pd
    from dataset_registry import Registry, Representation
from funtracks.data_model import Tracks

SULSTON_SAMPLE_NAME = "Sulston"


class CropWidget(QWidget):
    """One start/end time setting, applied to every loaded `Tracks` on "Crop".

    The spinboxes' allowed range spans the union of every track's own
    min/max time, since different lineages can start/end at different
    timepoints; cropping clips each track to whichever part of
    `[start, end)` it actually has.
    """

    def __init__(self) -> None:
        """
        Args:
            tracks_list: The loaded `Tracks` objects to crop, one shared
                start/end setting is applied to all of them.
            names: Display names for each entry in `tracks_list`, in the
                same order. Defaults to `"Tracks {i}"` if not given.
        """
        super().__init__()
        self._start_box = QSpinBox()

        self._end_box = QSpinBox()
        self._range_label = QLabel("(allowed range: )")

        row = QHBoxLayout()
        row.addWidget(QLabel("start"))
        row.addWidget(self._start_box)
        row.addWidget(QLabel("end"))
        row.addWidget(self._end_box)
        row.addWidget(self._range_label)

        layout = QVBoxLayout()
        layout.addLayout(row)
        self.setLayout(layout)

    def update_range(self, min_time: int, max_time: int) -> None:
        self._start_box.setRange(min_time, max_time)
        self._start_box.setValue(min_time)
        self._end_box.setRange(min_time, max_time)
        self._end_box.setValue(max_time)
        self._range_label.setText(f"(allowed range: {min_time}, {max_time})")


class EmbryoLoaderWidget(QWidget):
    new_data = Signal(Any, Any, Any)

    def __init__(self, registry: Registry, show_all_images: bool = False):
        super().__init__()
        self.registry = registry
        self.sulston_lineage = get_sulston(registry)
        self.show_all_images = show_all_images
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

        self._rep_checkboxes_layout = QVBoxLayout()

        self._crop_widget = CropWidget()

        load_button = QPushButton("Load")
        load_button.clicked.connect(self._on_load_clicked)

        self._load_into_memory_checkbox = QCheckBox("Load images into memory")
        self._load_into_memory_checkbox.setChecked(True)

        layout = QVBoxLayout()
        layout.addWidget(self._combo)
        layout.addLayout(self._rep_checkboxes_layout)
        layout.addWidget(self._crop_widget)
        layout.addWidget(self._load_into_memory_checkbox)
        layout.addWidget(load_button)
        self.setLayout(layout)

        self._on_sample_changed(self._combo.currentText())

    def _add_rep(self, rep: Representation) -> None:
        checkbox = QCheckBox(rep.name)
        checkbox.setChecked(True)
        checkbox.setProperty("representation_id", rep.id)
        self._rep_checkboxes_layout.addWidget(checkbox)

    def _on_sample_changed(self, sample_name: str) -> None:
        while (item := self._rep_checkboxes_layout.takeAt(0)) is not None:
            item.widget().deleteLater()

        sample = self._samples_by_name[sample_name]
        image_rep = self.registry.load_representation(sample.id, sample.metadata.image_rep)
        show_all = self.show_all_images or image_rep is None
        if not show_all:
            self._add_rep(image_rep)
        for rep_id in self.registry.list_representations(sample.id):
            rep = self.registry.load_representation(sample.id, rep_id)
            if rep.metadata.type == "image":
                if image_rep is None:
                    image_rep = rep
                if not show_all:
                    continue
            self._add_rep(rep)

        start_time = image_rep.metadata.start_time if image_rep is not None else 0
        end_time = image_rep.metadata.end_time if image_rep is not None else 500
        self._crop_widget.update_range(start_time, end_time)

    def _checked_rep_ids(self) -> list[str]:
        ids = []
        for i in range(self._rep_checkboxes_layout.count()):
            checkbox = self._rep_checkboxes_layout.itemAt(i).widget()
            if checkbox.isChecked():
                ids.append(checkbox.property("representation_id"))
        return ids

    def _load_image_rep(
        self, representation: Representation, start_time: int, end_time: int
    ) -> napari.layers.Image:
        images = load_images(representation, self.registry.path)
        # Both bounds are in the representation's own (real) timepoints, so
        # convert to its array-local indices before slicing -- its first
        # frame is at `start_time`, not 0.
        rep_start = representation.metadata.start_time
        local_start = start_time - rep_start
        local_end = end_time - rep_start + 1
        images = images[local_start:local_end]
        if self._load_into_memory_checkbox.isChecked():
            images = images.compute()
        # Offset the time axis by start_time so napari's shared time slider
        # lines up layers on the same *real* timepoint, even when
        # representations of the same embryo start at different t (e.g.
        # embryo 2's flat series starts at t=0, its per-slice at t=1).
        translate = [0] * images.ndim
        translate[0] = start_time
        return napari.layers.Image(
            images, name=representation.name, blending="additive", translate=translate
        )

    def _load_lineage_rep(self, representation: Representation) -> tuple[pd.DataFrame, str]:
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

    @staticmethod
    def crop_tracks(tracks: Tracks, start: int, end: int) -> Tracks:
        time_key = tracks.features.time_key
        subgraph = tracks.graph_full.filter(
            (NodeAttr(time_key) >= start) & (NodeAttr(time_key) < end)
        ).subgraph()
        return Tracks(
            subgraph.detach(),
            scale=tracks.scale,
            ndim=tracks.ndim,
            features=tracks.features,
            intensity_images=tracks._intensity_images,
            channel_names=tracks._channel_names,
        )

    def _on_load_clicked(self) -> None:
        start_time = self._crop_widget._start_box.value()
        end_time = self._crop_widget._end_box.value()

        images = []
        tracks_list = []
        tracks_names = []
        sample = self._samples_by_name[self._combo.currentText()]
        representation_ids = self._checked_rep_ids()
        if not representation_ids:
            raise RuntimeError(f"no representation checked for sample {sample.name!r}")
        for representation_id in representation_ids:
            representation = self.registry.load_representation(sample.id, representation_id)
            if representation.metadata.type == "image":
                images.append(self._load_image_rep(representation, start_time, end_time))
            elif representation.metadata.type == "lineage":
                track_df, name = self._load_lineage_rep(representation)
                tracks = self.crop_tracks(
                    tracks_from_lineage_df(track_df), start_time, end_time + 1
                )
                tracks_list.append(tracks)
                tracks_names.append(name)
        self.new_data.emit(images, tracks_list, tracks_names)
