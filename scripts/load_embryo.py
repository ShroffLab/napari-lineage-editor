from __future__ import annotations

from pathlib import Path

import dask
import napari
import pandas as pd
from dataset_registry import Registry
from funtracks.data_model.tracks import Tracks
from napari_track_edit.data_views import TracksViewer
from napari_track_edit.data_views.views.tree_view.tree_widget import TreeWidget
from qtpy.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

import napari_lineage_editor as nle
from napari_lineage_editor.data import nx_from_tracks, registry, tracks_from_lineage_df
from napari_lineage_editor.widgets import ComparisonWidget, CropWidget, EmbryoLoaderWidget

REGISTRY_PATH = Path("/Users/malinmayorc/projects/celegans-cell-id/wormguides-data/registry")
IMAGE_METADATA_TYPE = "image"
LINEAGE_METADATA_TYPE = "lineage"

# SMB round-trips dominate read latency here, not CPU decode time, so a
# handful of concurrent reads in flight helps -- but too many threads hammer
# the share and make everything slower, not faster.
_SMB_READ_THREADS = 4


class LineageEditorWidget(QWidget):
    def __init__(self, viewer: napari.Viewer, registry: Registry) -> None:
        super().__init__()
        self.viewer = viewer
        # widgets
        self.comparison_dock: object | None = None
        self.data_widget = EmbryoLoaderWidget(registry)
        self.crop_dock: object | None = None

        # book-keeping of data
        self.image_layers = []
        self.track_dfs: list[pd.DataFrame] = []
        self.tracks: list[Tracks] = []
        self.tracks_viewers: list[TracksViewer] = []
        self.track_names: list[str] = []
        self.view_only_tree_widgets: list[QWidget] = []

        # main tree widget, managed by nte
        tree_widget = TreeWidget(self.viewer)
        self.viewer.window.add_dock_widget(tree_widget, area="bottom")
        # layout
        layout = QVBoxLayout()
        layout.addWidget(self.data_widget)
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
        for dock in self.view_only_tree_widgets:
            self.viewer.window.remove_dock_widget(dock)
        TracksViewer.get_instance(self.viewer).clear_viewonly_tracks()

    def update_data(
        self,
        images: list[napari.layers.Image],
        track_dfs: list[pd.DataFrame],
        tracks_names: list[str],
    ):
        self.clear()
        self.track_dfs = track_dfs
        self.tracks = [tracks_from_lineage_df(df) for df in track_dfs]
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
            for vot, name in zip(self.view_only_tracks, self.view_only_tracks_names, strict=True):
                tv.add_viewonly_tracks(vot, name)
                overlay_tracks_viewer = tv.viewonly_tracks[name].tracks_viewer
                self.tracks_viewers.append(overlay_tracks_viewer)
                # ViewOnlyTracksGroup colors overlays by tracklet id by default;
                # flatten to one color so multiple overlaid lineages don't
                # compete visually with the editable lineage's own coloring.
                overlay_tracks_viewer.set_color_feature(None)
                self._open_tree_view(name, overlay_tracks_viewer)

        self.refresh_comparison_widget()
        self.refresh_crop_widget()
        self.configure()

    def _open_tree_view(self, tracks_name: str, tree_tracks_viewer: TracksViewer) -> None:
        """Open a `TreeWidget` dock for one lineage's `TracksViewer` (editable or view-only).

        Closes and replaces any previously-open tree view under the same
        name, so reloading the same representation doesn't accumulate
        duplicate docks.
        """
        tree_widget = TreeWidget(self.viewer, tracks_viewer=tree_tracks_viewer)
        dock = self.viewer.window.add_dock_widget(tree_widget, area="bottom", name=f"{tracks_name}")
        self.view_only_tree_widgets.append(dock)

    def refresh_comparison_widget(self) -> None:
        """Tear down and rebuild the comparison dock to match the currently-loaded lineages.

        Only shown once 2+ lineages are loaded -- `match_nodes`/`matches_to_df`
        need at least two lineages to compare. Always rebuilt from scratch
        (rather than updated in place) on every load, so stale matches from
        the previous load never linger.
        """
        if self.comparison_dock is not None:
            self.viewer.window.remove_dock_widget(self.comparison_dock)
            self.comparison_dock = None

        if len(self.view_only_tracks) < 1:
            return

        active_tracks_viewer = TracksViewer.get_instance(self.viewer)
        names = [self.active_tracks_name, *self.view_only_tracks_names]
        graphs = [nx_from_tracks(tracks) for tracks in self.tracks]
        dist_threshold = 10
        matched_df = nle.matches_to_df(names, graphs, nle.match_nodes(graphs, dist_threshold))
        comparison_widget = ComparisonWidget(active_tracks_viewer, matched_df)
        self.comparison_dock = self.viewer.window.add_dock_widget(
            comparison_widget, name="Comparison", area="right"
        )

    def refresh_crop_widget(self) -> None:
        """Tear down and rebuild the crop dock to match the currently-loaded lineages.

        Always rebuilt from scratch (rather than updated in place) on every
        load, so a stale slider range from the previous load never lingers.
        """
        if self.crop_dock is not None:
            self.viewer.window.remove_dock_widget(self.crop_dock)
            self.crop_dock = None

        if len(self.tracks) < 1:
            return

        crop_widget = CropWidget(self.tracks, self.track_names)
        crop_widget.range_changed.connect(self.update_tracks)
        self.crop_dock = self.viewer.window.add_dock_widget(crop_widget, name="Crop", area="right")

    def update_tracks(self, index: int, new_tracks: Tracks):
        self._update_tracks_viewer(index, new_tracks, view_only=index != 0)
        self.tracks[index] = new_tracks
        self.refresh_comparison_widget()

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
        if not view_only:
            points_layer = tracks_viewer.tracking_layers.points_layer
            if points_layer is not None:
                points_layer.size = 3

        for layer, visible in visible_by_layer.items():
            if layer in self.viewer.layers:
                layer.visible = visible

    def configure(self):
        tv = TracksViewer.get_instance(self.viewer)
        points_layer = tv.tracking_layers.points_layer
        if points_layer is not None:
            points_layer.size = 3

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
    loader_widget = LineageEditorWidget(viewer, reg)
    viewer.window.add_dock_widget(loader_widget, name="Lineage Editor")
    napari.run()


if __name__ == "__main__":
    main()
