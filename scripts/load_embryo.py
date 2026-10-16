"""Pick a wormguides embryo from the dataset registry and load its images into napari.

Opens an empty napari viewer with a dock widget listing every sample in the
registry. Picking an embryo lists every `type="image"` representation it has
(e.g. "Images" and "Images (flat)") as checkboxes; clicking "Load" lazily
loads every checked representation as its own dask-backed layer, so e.g. the
per-slice and flattened-per-timepoint versions of the same embryo can be
overlaid in napari for comparison. Loading a different embryo replaces all
previously-loaded layers.
"""

from functools import lru_cache
from pathlib import Path

import dask
import dask.array as da
import napari
import tifffile
from dask import delayed
from dataset_registry import Registry
from qtpy.QtWidgets import QCheckBox, QComboBox, QPushButton, QVBoxLayout, QWidget

import napari_lineage_editor as nle
from napari_lineage_editor._celegans_metadata import CelegansRepMetadata

REGISTRY_PATH = Path("/Users/malinmayorc/projects/celegans-cell-id/wormguides-data/registry")
IMAGE_METADATA_TYPE = "image"

# SMB round-trips dominate read latency here, not CPU decode time, so a
# handful of concurrent reads in flight helps -- but too many threads hammer
# the share and make everything slower, not faster.
_SMB_READ_THREADS = 4

# Caches whole frames (one timepoint, or one timepoint-slice for embryo 3) so
# napari re-rendering the same slice while scrubbing the time/z sliders
# doesn't refetch it over SMB every time.
_FRAME_CACHE_SIZE = 32


def _image_representations(registry: Registry, sample_id: str) -> list:
    """Every `type="image"` representation of `sample_id`, in registry order."""
    return [
        representation
        for representation_id in registry.list_representations(sample_id)
        if (representation := registry.load_representation(sample_id, representation_id))
        .metadata.type
        == IMAGE_METADATA_TYPE
    ]


class EmbryoLoaderWidget(QWidget):
    """Dock widget: pick a registry sample, check which image representations to load, load them.

    Every checked representation becomes its own layer, so e.g. a
    per-slice-tiff and a flattened-per-timepoint-tiff representation of the
    same embryo can be overlaid for comparison. Loading replaces every layer
    this widget previously added -- switching embryos (or representations)
    doesn't leave stale layers behind.
    """

    def __init__(self, viewer: napari.Viewer, registry: Registry) -> None:
        super().__init__()
        self._viewer = viewer
        self._registry = registry
        self._loaded_layer_names: list[str] = []

        self._samples_by_name = {
            sample.name: sample
            for sample in sorted(
                (registry.load_sample(sample_id) for sample_id in registry.list_samples()),
                key=lambda sample: sample.name,
            )
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
        for representation in _image_representations(self._registry, sample.id):
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

    def _on_load_clicked(self) -> None:
        sample = self._samples_by_name[self._combo.currentText()]
        representation_ids = self._checked_representation_ids()
        if not representation_ids:
            raise RuntimeError(f"no image representation checked for sample {sample.name!r}")

        for layer_name in self._loaded_layer_names:
            if layer_name in self._viewer.layers:
                del self._viewer.layers[layer_name]
        self._loaded_layer_names = []

        for representation_id in representation_ids:
            representation = self._registry.load_representation(sample.id, representation_id)
            images = load_images(representation.path, representation.metadata)
            layer_name = f"{sample.name} / {representation.name}"
            # Offset the time axis by start_time so napari's shared time
            # slider lines up layers on the same *real* timepoint, even when
            # representations of the same embryo start at different t (e.g.
            # embryo 2's flat series starts at t=0, its per-slice at t=1).
            translate = [0] * images.ndim
            translate[0] = representation.metadata.start_time
            self._viewer.add_image(
                images, name=layer_name, blending="additive", translate=translate
            )
            self._loaded_layer_names.append(layer_name)

        self._viewer.title = sample.name


@lru_cache(maxsize=_FRAME_CACHE_SIZE)
def _read_frame_cached(path: Path) -> object:
    """`tifffile.imread(path)`, cached so repeat reads of the same frame skip the SMB round-trip."""
    return tifffile.imread(path)


def _lazy_frame(path: Path, shape: tuple[int, ...], dtype: object) -> da.Array:
    """Wrap a single cached `tifffile.imread` call as a shaped, lazily-evaluated dask array."""
    return da.from_delayed(delayed(_read_frame_cached)(path), shape=shape, dtype=dtype)


def load_images(image_dir: str, metadata: CelegansRepMetadata) -> da.Array:
    """Lazily stack `image_dir`'s per-timepoint (or per-timepoint-per-slice) tiffs.

    No directory listing happens -- every file path is built directly from
    `metadata`'s explicit `start_time`/`end_time` (and `start_z`/`end_z`, if
    set) ranges, since the data lives on a slow network share.

    Args:
        image_dir: Directory containing the tiff files.
        metadata: This representation's `file_pattern` and time/z ranges.
            `file_pattern` contains `{t}`, and -- when each timepoint is
            itself a stack of per-slice 2D tiffs rather than one 3D tiff --
            `{z}` too.

    Returns:
        A dask array stacking every timepoint (and, if present, every slice)
        along new leading axes, with no file read until computed.
    """
    if metadata.file_pattern is None:
        raise RuntimeError("representation metadata has no 'file_pattern'")
    timepoints = range(metadata.start_time, metadata.end_time + 1)

    if metadata.start_z is None:
        paths = [
            Path(image_dir) / metadata.file_pattern.format(t=t) for t in timepoints
        ]
        sample_frame = tifffile.imread(paths[0])
        frames = [_lazy_frame(path, sample_frame.shape, sample_frame.dtype) for path in paths]
        return da.stack(frames)

    z_slices = range(metadata.start_z, metadata.end_z + 1)
    sample_frame = tifffile.imread(
        Path(image_dir) / metadata.file_pattern.format(t=timepoints[0], z=z_slices[0])
    )
    volumes = []
    for t in timepoints:
        slices = [
            _lazy_frame(
                Path(image_dir) / metadata.file_pattern.format(t=t, z=z),
                sample_frame.shape,
                sample_frame.dtype,
            )
            for z in z_slices
        ]
        volumes.append(da.stack(slices))
    return da.stack(volumes)


def main() -> None:
    dask.config.set(scheduler="threads", num_workers=_SMB_READ_THREADS)

    registry = nle.registry(REGISTRY_PATH)

    viewer = napari.Viewer(title="Wormguides embryo viewer")
    viewer.window.add_dock_widget(EmbryoLoaderWidget(viewer, registry), name="Embryo loader")
    napari.run()


if __name__ == "__main__":
    main()
