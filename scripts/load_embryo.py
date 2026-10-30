from __future__ import annotations

from typing import TYPE_CHECKING

import dask
import napari
from napari_track_edit.data_views import TracksViewer
from qtpy.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

from napari_lineage_editor.data import get_sulston, registry
from napari_lineage_editor.widgets import (
    CellNavigatorWidget,
    EmbryoLoaderWidget,
    TreeViewControlWidget,
)

if TYPE_CHECKING:
    from dataset_registry import Registry
    from funtracks.data_model.tracks import Tracks


REGISTRY_PATH = "janelia://nrs.hhmi.org/shroff/Pharynx_Project/wormguides-data/registry"
IMAGE_METADATA_TYPE = "image"
LINEAGE_METADATA_TYPE = "lineage"

# SMB round-trips dominate read latency here, not CPU decode time, so a
# handful of concurrent reads in flight helps -- but too many threads hammer
# the share and make everything slower, not faster.
_SMB_READ_THREADS = 4

_ACTIVE_POINTS_SIZE = 5
_VIEW_ONLY_POINTS_SIZE = 2

_NAME_TEXT_OFFSET_MAGNITUDE = 5
# Each tuple is one offset per data axis (t, z, y, x) -- TextManager.translation
# is indexed by the full ndim, not just the two currently-displayed axes, so a
# 2-tuple raises IndexError as soon as napari tries to compute text
# coordinates. Lineages cycle through these three directions (y only, x only,
# both) rather than growing the offset's magnitude, so every label stays the
# same size/legibility regardless of how many lineages are loaded.
_M = _NAME_TEXT_OFFSET_MAGNITUDE
_NAME_TEXT_OFFSETS = [(0, 0, -_M, 0), (0, 0, 0, -_M), (0, 0, -_M, -_M)]
_ACTIVE_NAME_TEXT_COLOR = "white"
_VIEW_ONLY_NAME_TEXT_COLORS = ["cyan", "magenta", "yellow", "orange"]


class ViewWormguides(QWidget):
    def __init__(self, viewer: napari.Viewer, registry: Registry) -> None:
        super().__init__()
        self.viewer = viewer
        viewer.scene.camera.orientation = ("away", "down", "right")
        # widgets
        self.data_widget = EmbryoLoaderWidget(registry)
        self.cell_navigator_widget = CellNavigatorWidget(
            TracksViewer.get_instance(viewer), get_sulston(registry)
        )
        self.tree_view_control_widget = TreeViewControlWidget(viewer)

        # book-keeping of data
        self.image_layers = []
        self.tracks: list[Tracks] = []
        self.tracks_viewers: list[TracksViewer] = []
        self.track_names: list[str] = []

        # layout
        layout = QVBoxLayout()
        layout.addWidget(self.data_widget)
        layout.addWidget(self.cell_navigator_widget)
        layout.addWidget(self.tree_view_control_widget)
        self.setLayout(layout)

        # signals
        self.data_widget.new_data.connect(self.update_data)

    @property
    def active_tracks(self) -> Tracks | None:
        return self.tracks[0] if len(self.tracks) > 0 else None

    @property
    def active_tracks_name(self) -> str | None:
        return self.track_names[0] if len(self.track_names) > 0 else None

    @property
    def view_only_tracks(self) -> list[Tracks]:
        return self.tracks[1:]

    @property
    def view_only_tracks_names(self) -> list[str]:
        return self.track_names[1:]

    def clear(self):
        for layer in self.image_layers:
            self.viewer.layers.remove(layer)
        self.image_layers = []
        TracksViewer.get_instance(self.viewer).clear_viewonly_tracks()

    def update_data(
        self,
        images: list[napari.layers.Image],
        tracks_list: list[Tracks],
        tracks_names: list[str],
    ):
        self.clear()
        self.tracks = tracks_list
        self.tracks_viewers = []
        self.track_names = tracks_names
        self.image_layers = images

        for image in images:
            self.viewer.add_layer(image)

        tv = TracksViewer.get_instance(self.viewer)

        if self.active_tracks is not None:
            tv.update_tracks(self.active_tracks, self.active_tracks_name)
            self.tracks_viewers.append(tv)

        if len(self.view_only_tracks) > 0:
            for i, (vot, name) in enumerate(
                zip(self.view_only_tracks, self.view_only_tracks_names, strict=True)
            ):
                tv.add_viewonly_tracks(vot, name)
                overlay_tracks_viewer = tv.viewonly_tracks[name].tracks_viewer
                self.tracks_viewers.append(overlay_tracks_viewer)
                # ViewOnlyTracksGroup colors overlays by tracklet id by default;
                # flatten to one color so multiple overlaid lineages don't
                # compete visually with the editable lineage's own coloring.
                overlay_tracks_viewer.set_color_feature(None)
                self._set_points_size(overlay_tracks_viewer, _VIEW_ONLY_POINTS_SIZE)
                # Active lineage is index 0 (see below), so overlays start at 1.
                self._set_points_text(
                    overlay_tracks_viewer,
                    offset=_NAME_TEXT_OFFSETS[(i + 1) % len(_NAME_TEXT_OFFSETS)],
                    color=_VIEW_ONLY_NAME_TEXT_COLORS[i % len(_VIEW_ONLY_NAME_TEXT_COLORS)],
                )

        self._set_points_text(tv, offset=_NAME_TEXT_OFFSETS[0], color=_ACTIVE_NAME_TEXT_COLOR)

        self.tree_view_control_widget.set_lineages(
            dict(zip(self.track_names, self.tracks_viewers, strict=True)),
            checked_name=self.active_tracks_name,
        )

        self.configure()

    def update_tracks(self, index: int, new_tracks: Tracks):
        self._update_tracks_viewer(index, new_tracks, view_only=index != 0)
        self.tracks[index] = new_tracks

    def _update_tracks_viewer(self, index: int, new_tracks: Tracks, *, view_only: bool) -> None:
        """Replace a `TracksViewer`'s tracks, undoing side effects that assume it's the only one.

        `TracksViewer.update_tracks` assumes it's the viewer's one-and-only
        tracks and always:
        - resets `color_feature_key` back to the tracklet id, undoing
          whatever coloring was set before (e.g. the flat, single-color
          coloring `update_data` gives every view-only overlay via
          `set_color_feature(None)`). Always restored, for either kind of
          tracks viewer, so replacing tracks never silently changes how
          they're colored.
        - hides every other Labels/Points layer in the viewer, since it only
          knows to spare its *own* `viewonly_tracks`' layers -- but a
          view-only overlay's own `TracksViewer` instance has no
          `viewonly_tracks` of its own, so it hides the active tracks' and
          every other overlay's points layer too. Always restored, for
          either kind of tracks viewer.
        """
        tracks_viewer = self.tracks_viewers[index]
        color_feature_key = tracks_viewer.color_feature_key
        visible_by_layer = {layer: layer.visible for layer in self.viewer.layers}

        tracks_viewer.update_tracks(new_tracks, self.track_names[index])

        tracks_viewer.set_color_feature(color_feature_key)
        points_size = _VIEW_ONLY_POINTS_SIZE if view_only else _ACTIVE_POINTS_SIZE
        self._set_points_size(tracks_viewer, points_size)
        # `index` is 0 for the active lineage, 1+ for each view-only overlay
        # (see `update_data`), matching `_NAME_TEXT_OFFSETS`' own indexing.
        color = (
            _VIEW_ONLY_NAME_TEXT_COLORS[(index - 1) % len(_VIEW_ONLY_NAME_TEXT_COLORS)]
            if view_only
            else _ACTIVE_NAME_TEXT_COLOR
        )
        self._set_points_text(
            tracks_viewer, offset=_NAME_TEXT_OFFSETS[index % len(_NAME_TEXT_OFFSETS)], color=color
        )

        for layer, visible in visible_by_layer.items():
            if layer in self.viewer.layers:
                layer.visible = visible

    def _set_points_size(self, tracks_viewer: TracksViewer, size: int) -> None:
        """Set the points layer's default point size the same way its layer-controls slider does.

        `TrackPoints` (napari-track-edit's `Points` subclass) tracks its own
        `default_size`, updated via `set_point_size` -- but only ever called
        from the layer controls' "point size" slider's `current_size` event.
        Setting `.size` directly bypasses `default_size` entirely, so the
        slider never reflects the change and anything that re-derives sizes
        from `default_size` later (e.g. adding a new point) reverts to the
        stale value. Setting `current_size` instead goes through the same
        event `TrackPoints` already listens to, keeping the slider in sync.
        """
        points_layer = tracks_viewer.tracking_layers.points_layer
        if points_layer is not None:
            points_layer.current_size = size

    def _set_points_text(
        self, tracks_viewer: TracksViewer, *, offset: tuple[int, int, int, int], color: str
    ) -> None:
        """Label each point with its cell name, offset/colored so overlaid lineages stay legible.

        `offset`/`color` are per-lineage (see `_NAME_TEXT_OFFSETS`/
        `_VIEW_ONLY_NAME_TEXT_COLORS`), so the same cell's name in different
        overlaid lineages doesn't render as indistinguishable stacked text.
        """
        points_layer = tracks_viewer.tracking_layers.points_layer
        if points_layer is not None:
            points_layer.text = "Name"
            points_layer.text.translation = offset
            points_layer.text.color = color

    def configure(self):
        tv = TracksViewer.get_instance(self.viewer)
        self._set_points_size(tv, _ACTIVE_POINTS_SIZE)

        for image in self.image_layers:
            image.reset_contrast_limits()

        axis_labels = self.viewer.dims.axis_labels
        if "z" in axis_labels:
            z_axis = axis_labels.index("z")
            margin_left = list(self.viewer.dims.margin_left_step)
            margin_right = list(self.viewer.dims.margin_right_step)
            margin_left[z_axis] = 5
            margin_right[z_axis] = 5
            self.viewer.dims.margin_left_step = tuple(margin_left)
            self.viewer.dims.margin_right_step = tuple(margin_right)


def main() -> None:
    dask.config.set(scheduler="threads", num_workers=_SMB_READ_THREADS)

    reg = registry(REGISTRY_PATH)

    viewer = napari.Viewer(title="Wormguides lineage editor")
    loader_widget = ViewWormguides(viewer, reg)
    viewer.window.add_dock_widget(loader_widget, name="Lineage Editor")

    # DEBUG: pre-configure the shared OrthoViewManager so the plugin menu's
    # "Toggle/Show Orthogonal Views" uses napari-track-edit's TrackGraph-aware
    # copy_layer/sync_filters instead of the unconfigured default.
    from napari_track_edit.data_views.views.ortho_views import initialize_ortho_views

    initialize_ortho_views(viewer)

    napari.run()


if __name__ == "__main__":
    main()
