"""Pick a wormguides embryo from the dataset registry and load its data into napari.

Opens an empty napari viewer with a dock widget listing every sample in the
registry except "Sulston" (the canonical reference lineage, not tied to one
imaged embryo). Picking a sample lists every one of its representations --
image or lineage -- as checkboxes; clicking "Load" loads every checked
representation: image representations become their own dask-backed layer
(so e.g. the per-slice and flattened-per-timepoint versions of the same
embryo can be overlaid for comparison), lineage representations become their
own `Tracks` entry in `napari-track-edit`'s tracks list (division edges
resolved against the registry's "Sulston" sample, if present). Loading a
different sample replaces all previously-loaded image layers and tracks.
"""

from pathlib import Path

import dask
import napari
from dataset_registry import Registry
from napari_track_edit.data_views import TracksViewer
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import napari_lineage_editor as nle

REGISTRY_PATH = Path("/Users/malinmayorc/projects/celegans-cell-id/wormguides-data/registry")
IMAGE_METADATA_TYPE = "image"
LINEAGE_METADATA_TYPE = "lineage"
SULSTON_SAMPLE_NAME = "Sulston"

# SMB round-trips dominate read latency here, not CPU decode time, so a
# handful of concurrent reads in flight helps -- but too many threads hammer
# the share and make everything slower, not faster.
_SMB_READ_THREADS = 4


def _sulston_geff_path(registry: Registry) -> str | None:
    """Path to the registry's "Sulston" sample's lineage representation, or `None` if absent."""
    for sample_id in registry.list_samples():
        if registry.load_sample(sample_id).name == SULSTON_SAMPLE_NAME:
            representation_ids = registry.list_representations(sample_id)
            if representation_ids:
                return registry.load_representation(sample_id, representation_ids[0]).path
    return None


class EmbryoLoaderWidget(QWidget):
    """Dock widget: pick a registry sample, check which representations to load, load them.

    Every checked representation becomes either its own image layer (so e.g.
    the per-slice and flattened-per-timepoint versions of the same embryo
    can be overlaid for comparison) or its own `Tracks` entry in
    `napari-track-edit`'s tracks list, depending on the representation's
    `type`. Loading replaces every image layer and tracks entry this widget
    previously added -- switching samples (or representations) doesn't
    leave stale ones behind.
    """

    def __init__(self, viewer: napari.Viewer, registry: Registry) -> None:
        super().__init__()
        self._viewer = viewer
        self._registry = registry
        self._loaded_layer_names: list[str] = []
        # Maps a loaded tracks entry's display name to the QListWidgetItem
        # napari-track-edit's TracksList created for it, so re-loading the
        # same representation re-selects that item instead of re-adding a
        # duplicate -- there's no public "remove by name" API to rely on
        # instead (TracksList.remove_tracks takes the item, not a name).
        self._tracks_items_by_name: dict[str, object] = {}
        # Maps id(tracks) -> {cell name: tracklet id}, built once per loaded
        # lineage (name maps 1:1 to tracklet) so the "go to start/end" lookup
        # doesn't rescan every node on every click.
        self._track_ids_by_name_by_tracks: dict[int, dict[str, int]] = {}
        self._sulston_geff_path = _sulston_geff_path(registry)

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

        layout = QVBoxLayout()
        layout.addWidget(self._combo)
        layout.addLayout(self._representation_checkboxes_layout)
        layout.addWidget(load_button)
        layout.addWidget(self._name_edit)
        layout.addLayout(name_nav_buttons)
        self.setLayout(layout)

        self._on_sample_changed(self._combo.currentText())

    def _on_sample_changed(self, sample_name: str) -> None:
        while (item := self._representation_checkboxes_layout.takeAt(0)) is not None:
            item.widget().deleteLater()

        sample = self._samples_by_name[sample_name]
        for representation_id in self._registry.list_representations(sample.id):
            representation = self._registry.load_representation(sample.id, representation_id)
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

    def _load_image_representation(self, sample_name: str, representation) -> None:
        images = nle.load_images(representation.path, representation.metadata)
        layer_name = f"{sample_name} / {representation.name}"
        # Offset the time axis by start_time so napari's shared time slider
        # lines up layers on the same *real* timepoint, even when
        # representations of the same embryo start at different t (e.g.
        # embryo 2's flat series starts at t=0, its per-slice at t=1).
        translate = [0] * images.ndim
        translate[0] = representation.metadata.start_time
        self._viewer.add_image(images, name=layer_name, blending="additive", translate=translate)
        self._loaded_layer_names.append(layer_name)

    def _load_lineage_representation(self, sample_name: str, representation) -> None:
        tracks_name = f"{sample_name} / {representation.name}"
        tracks_viewer = TracksViewer.get_instance(self._viewer)
        tracks_list = tracks_viewer.tracks_list

        existing_item = self._tracks_items_by_name.get(tracks_name)
        if existing_item is not None:
            tracks_list.tracks_list.setCurrentItem(existing_item)
            return

        if representation.metadata.has_links:
            lineage = nle.load_linked_lineage(representation.path)
            tracks = nle.tracks_from_lineage_df(
                lineage, node_name_map=nle.LOAD_LINKED_LINEAGE_NODE_NAME_MAP
            )
        else:
            lineage = nle.load_lineages(
                representation.path, geff_lineage_path=self._sulston_geff_path
            )
            tracks = nle.tracks_from_lineage_df(lineage)
        tracks_list.add_tracks(tracks, tracks_name)
        new_item = tracks_list.tracks_list.item(tracks_list.tracks_list.count() - 1)
        self._tracks_items_by_name[tracks_name] = new_item

        if "name" in tracks.features:
            self._refresh_name_cache(tracks)
            points_layer = tracks_viewer.tracking_layers.points_layer
            if points_layer is not None:
                points_layer.text = "Name"

    def _refresh_name_cache(self, tracks) -> None:
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
        (`TracksViewer.tracks`), using the `{name: track_id}` cache built
        when that lineage was loaded (name maps 1:1 to tracklet).

        Args:
            end: If `True`, select the tracklet's last (max time) node;
                otherwise its first (min time) node.
        """
        tracks_viewer = TracksViewer.get_instance(self._viewer)
        tracks = tracks_viewer.tracks
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
        tracks_viewer.selected_nodes.add(node)

    def _on_load_clicked(self) -> None:
        sample = self._samples_by_name[self._combo.currentText()]
        representation_ids = self._checked_representation_ids()
        if not representation_ids:
            raise RuntimeError(f"no representation checked for sample {sample.name!r}")

        for layer_name in self._loaded_layer_names:
            if layer_name in self._viewer.layers:
                del self._viewer.layers[layer_name]
        self._loaded_layer_names = []

        for representation_id in representation_ids:
            representation = self._registry.load_representation(sample.id, representation_id)
            if representation.metadata.type == IMAGE_METADATA_TYPE:
                self._load_image_representation(sample.name, representation)
            elif representation.metadata.type == LINEAGE_METADATA_TYPE:
                self._load_lineage_representation(sample.name, representation)

        self._viewer.title = sample.name


def main() -> None:
    dask.config.set(scheduler="threads", num_workers=_SMB_READ_THREADS)

    registry = nle.registry(REGISTRY_PATH)

    viewer = napari.Viewer(title="Wormguides embryo viewer")
    viewer.window.add_dock_widget(EmbryoLoaderWidget(viewer, registry), name="Embryo loader")
    napari.run()


if __name__ == "__main__":
    main()
