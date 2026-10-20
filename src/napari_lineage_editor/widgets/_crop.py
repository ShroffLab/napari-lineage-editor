from __future__ import annotations

from funtracks.data_model import Tracks
from psygnal import Signal
from qtpy.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget
from tracksdata import NodeAttr


class CropWidget(QWidget):
    """One start/end time setting, applied to every loaded `Tracks` on "Crop".

    The spinboxes' allowed range spans the union of every track's own
    min/max time, since different lineages can start/end at different
    timepoints; cropping clips each track to whichever part of
    `[start, end)` it actually has.
    """

    range_changed = Signal(int, Tracks)
    """Emitted as `(track_index, cropped_tracks)` once per track, when "Crop" is clicked."""

    def __init__(self, tracks_list: list[Tracks], names: list[str] | None = None) -> None:
        """
        Args:
            tracks_list: The loaded `Tracks` objects to crop, one shared
                start/end setting is applied to all of them.
            names: Display names for each entry in `tracks_list`, in the
                same order. Defaults to `"Tracks {i}"` if not given.
        """
        super().__init__()
        # Copied so that reassigning the caller's list entries (e.g. to the
        # cropped Tracks after each range_changed) doesn't feed back here --
        # every crop must always start from the original, uncropped tracks.
        self.tracks_list = list(tracks_list)
        self.names = (
            names if names is not None else [f"Tracks {i}" for i in range(len(tracks_list))]
        )

        ranges = [
            (min(times), max(times))
            if len(times := tracks.get_times(tracks.nodes())) > 0
            else (0, 0)
            for tracks in tracks_list
        ]

        min_time = min((r[0] for r in ranges), default=0)
        max_time = max((r[1] for r in ranges), default=0)

        self._start_box = QSpinBox()
        self._start_box.setRange(min_time, max_time)
        self._start_box.setValue(min_time)

        self._end_box = QSpinBox()
        self._end_box.setRange(min_time, max_time)
        self._end_box.setValue(max_time)

        row = QHBoxLayout()
        row.addWidget(QLabel("start"))
        row.addWidget(self._start_box)
        row.addWidget(QLabel("end"))
        row.addWidget(self._end_box)
        row.addWidget(QLabel(f"(allowed range: {min_time} - {max_time})"))

        crop_button = QPushButton("Crop")
        crop_button.clicked.connect(self._on_crop_clicked)

        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(crop_button)
        self.setLayout(layout)

    def _on_crop_clicked(self) -> None:
        start_time = self._start_box.value()
        end_time = self._end_box.value()
        for track_index, tracks in enumerate(self.tracks_list):
            cropped_tracks = self.crop_tracks(tracks, start_time, end_time)
            self.range_changed.emit(track_index, cropped_tracks)

    def crop_tracks(self, tracks: Tracks, start, end) -> Tracks:
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
