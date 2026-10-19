from __future__ import annotations

from typing import TYPE_CHECKING

from psygnal import Signal
from qtpy.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from napari_lineage_editor._match_lineages import get_conflicts

if TYPE_CHECKING:
    import pandas as pd
    from napari_track_edit.data_views import TracksViewer


class ComparisonWidget(QWidget):
    """Table widget: one row per tracklet whose name disagrees across loaded lineages.

    Built from a `matches_to_df`-style `matched_df` (two-level `(lineage
    name, "id" | "name")` column `MultiIndex`, main/editable lineage first):
    `get_conflicts` narrows it to disagreeing rows, and each lineage's
    "name" column becomes a table column. Clicking a row selects that
    tracklet's main-lineage node in `active_tracks`.

    Matching is by exact position (see `matches_to_df`) -- lineages are
    assumed to agree on cell positions; the zenodo lineages' real position
    discrepancies (see `wormguides-data-archaeology.md`) aren't accounted
    for yet, so a genuinely-matching cell may not be found there until
    position tolerance is added.
    """

    row_clicked = Signal(str)

    def __init__(self, active_tracks: TracksViewer, matched_df: pd.DataFrame) -> None:
        """
        Args:
            active_tracks: The editable lineage's `TracksViewer`, whose
                selection is driven by clicking a row.
            matched_df: A `matches_to_df` result, main/editable lineage
                first.
        """
        super().__init__()
        self.active_tracks = active_tracks

        conflicts = get_conflicts(matched_df)
        self._main_name, *_ = conflicts.columns.get_level_values(0).unique()
        self._ids_by_name = dict(
            zip(
                conflicts[(self._main_name, "name")],
                conflicts[(self._main_name, "id")],
                strict=True,
            )
        )

        self.lineages = list(conflicts.columns.get_level_values(0).unique())
        self._table = QTableWidget(len(conflicts), len(self.lineages))
        self._table.setHorizontalHeaderLabels(self.lineages)
        self._table.cellClicked.connect(self._on_cell_clicked)
        for row_index, (_, row) in enumerate(conflicts.iterrows()):
            for column_index, lineage in enumerate(self.lineages):
                self._table.setItem(
                    row_index, column_index, QTableWidgetItem(row[(lineage, "name")])
                )

        layout = QVBoxLayout()
        layout.addWidget(self._table)
        self.setLayout(layout)

    def _on_cell_clicked(self, row: int, _column: int) -> None:
        main_column = self.lineages.index(self._main_name)
        name = self._table.item(row, main_column).text()
        self.row_clicked.emit(name)
        node = self._ids_by_name.get(name)
        if node is not None:
            self.active_tracks.selected_nodes.add(node)
