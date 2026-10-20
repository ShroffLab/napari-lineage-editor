from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import pandas as pd
from psygnal import Signal
from qtpy.QtCore import Qt
from qtpy.QtWidgets import QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

if TYPE_CHECKING:
    from napari_track_edit.data_views import TracksViewer

_CONFLICT_INDEX_ROLE = Qt.ItemDataRole.UserRole


class ComparisonWidget(QWidget):
    """Table widget: one row per tracklet whose name disagrees across loaded lineages.

    Built from a `get_conflicts`-style `conflicts` dataframe (two-level
    `(lineage name, "id" | "name")` column `MultiIndex`, main/editable
    lineage first, already narrowed to disagreeing rows) -- either computed
    live from loaded tracks (`get_conflicts(matches_to_df(...))`) or loaded
    straight from a precomputed CSV (see `scripts/export_lineage_conflicts.py`
    and `scripts/fix_conflicts.py`). Each lineage's "name" column becomes a
    table column, plus a "Resolved" button column: clicking it removes that
    row from both the table and `self.conflicts`, so a caller that persists
    `self.conflicts` (e.g. back to the conflicts CSV) drops resolved rows
    for good. Clicking elsewhere in a row selects that tracklet's
    main-lineage node in `active_tracks`.

    Matching is by exact position (see `matches_to_df`) -- lineages are
    assumed to agree on cell positions; the zenodo lineages' real position
    discrepancies (see `wormguides-data-archaeology.md`) aren't accounted
    for yet, so a genuinely-matching cell may not be found there until
    position tolerance is added.
    """

    row_clicked = Signal(str)
    row_resolved = Signal(str)

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
        self.conflicts = conflicts.reset_index(drop=True)

        self._main_name, *_ = self.conflicts.columns.get_level_values(0).unique()
        self.lineages = list(self.conflicts.columns.get_level_values(0).unique())

        # Keyed by the conflicts dataframe's own index, not by row position
        # in the table or by name: rows are removed from the table as
        # they're resolved, shifting later rows' table positions, and the
        # same main-lineage name can appear in multiple conflict rows (e.g.
        # disagreeing with different other lineages).
        self._table = QTableWidget(len(self.conflicts), len(self.lineages) + 1)
        self._table.setHorizontalHeaderLabels([*self.lineages, "Resolved"])
        self._table.cellClicked.connect(self._on_cell_clicked)
        for row_index, conflict_index in enumerate(self.conflicts.index):
            row = self.conflicts.loc[conflict_index]
            for column_index, lineage in enumerate(self.lineages):
                name = row[(lineage, "name")]
                # No match found for this lineage: a real None when computed
                # live via matches_to_df, but round-trips through a CSV (see
                # scripts/export_lineage_conflicts.py) as a float NaN, which
                # QTableWidgetItem doesn't accept.
                text = None if pd.isna(name) else str(name)
                item = QTableWidgetItem(text)
                item.setData(_CONFLICT_INDEX_ROLE, conflict_index)
                self._table.setItem(row_index, column_index, item)

            resolved_button = QPushButton("Resolved")
            resolved_button.clicked.connect(partial(self._on_resolved_clicked, conflict_index))
            self._table.setCellWidget(row_index, len(self.lineages), resolved_button)

        layout = QVBoxLayout()
        layout.addWidget(self._table)
        self.setLayout(layout)

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column >= len(self.lineages):
            return

        main_column = self.lineages.index(self._main_name)
        item = self._table.item(row, main_column)
        name = item.text()
        self.row_clicked.emit(name)

        conflict_index = item.data(_CONFLICT_INDEX_ROLE)
        node = self.conflicts.loc[conflict_index, (self._main_name, "id")]
        if pd.notna(node):
            self.active_tracks.selected_nodes.add(node)

    def _on_resolved_clicked(self, conflict_index: int) -> None:
        main_column = self.lineages.index(self._main_name)
        name = None
        for row_index in range(self._table.rowCount()):
            item = self._table.item(row_index, main_column)
            if item is not None and item.data(_CONFLICT_INDEX_ROLE) == conflict_index:
                name = item.text()
                self._table.removeRow(row_index)
                break

        self.conflicts = self.conflicts.drop(index=conflict_index)
        if name is not None:
            self.row_resolved.emit(name)
