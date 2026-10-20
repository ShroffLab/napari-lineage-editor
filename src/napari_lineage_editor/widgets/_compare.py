from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
from psygnal import Signal
from qtpy.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

if TYPE_CHECKING:
    from napari_track_edit.data_views import TracksViewer


class ComparisonWidget(QWidget):
    """Table widget: one row per tracklet whose name disagrees across loaded lineages.

    Built from a `get_conflicts`-style `conflicts` dataframe (two-level
    `(lineage name, "id" | "name")` column `MultiIndex`, main/editable
    lineage first, already narrowed to disagreeing rows) -- either computed
    live from loaded tracks (`get_conflicts(matches_to_df(...))`) or loaded
    straight from a precomputed CSV (see `scripts/export_lineage_conflicts.py`
    and `scripts/fix_conflicts.py`). Each lineage's "name" column becomes a
    table column. Clicking a row selects that tracklet's main-lineage node
    in `active_tracks`.

    Matching is by exact position (see `matches_to_df`) -- lineages are
    assumed to agree on cell positions; the zenodo lineages' real position
    discrepancies (see `wormguides-data-archaeology.md`) aren't accounted
    for yet, so a genuinely-matching cell may not be found there until
    position tolerance is added.
    """

    row_clicked = Signal(str)

    def __init__(self, active_tracks: TracksViewer, conflicts: pd.DataFrame) -> None:
        """
        Args:
            active_tracks: The editable lineage's `TracksViewer`, whose
                selection is driven by clicking a row.
            conflicts: A `get_conflicts` result (or an equivalently-shaped
                dataframe loaded from a precomputed CSV), main/editable
                lineage first.
        """
        super().__init__()
        self.active_tracks = active_tracks

        self._main_name, *_ = conflicts.columns.get_level_values(0).unique()
        # Keyed by row index, not by name: the same main-lineage name can
        # appear in multiple conflict rows (e.g. disagreeing with different
        # other lineages), and a dict keyed by name would collapse those
        # rows to whichever one's id happened to be inserted last.
        self._ids_by_row = list(conflicts[(self._main_name, "id")])

        self.lineages = list(conflicts.columns.get_level_values(0).unique())
        self._table = QTableWidget(len(conflicts), len(self.lineages))
        self._table.setHorizontalHeaderLabels(self.lineages)
        self._table.cellClicked.connect(self._on_cell_clicked)
        for row_index, (_, row) in enumerate(conflicts.iterrows()):
            for column_index, lineage in enumerate(self.lineages):
                name = row[(lineage, "name")]
                # No match found for this lineage: a real None when computed
                # live via matches_to_df, but round-trips through a CSV (see
                # scripts/export_lineage_conflicts.py) as a float NaN, which
                # QTableWidgetItem doesn't accept.
                text = None if pd.isna(name) else str(name)
                self._table.setItem(row_index, column_index, QTableWidgetItem(text))

        layout = QVBoxLayout()
        layout.addWidget(self._table)
        self.setLayout(layout)

    def _on_cell_clicked(self, row: int, _column: int) -> None:
        main_column = self.lineages.index(self._main_name)
        name = self._table.item(row, main_column).text()
        self.row_clicked.emit(name)
        node = self._ids_by_row[row]
        if pd.notna(node):
            self.active_tracks.selected_nodes.add(node)
