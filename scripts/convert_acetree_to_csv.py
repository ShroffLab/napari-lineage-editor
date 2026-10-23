"""Convert an AceTree "nuclei" directory (one `t###-nuclei` CSV per
timepoint, AceTree's native per-frame format) into a single CSV with a
`time` column, matching the `time, cell_name, x, y, z, diameter` shape this
codebase's other lineage loaders expect.

Each `t###-nuclei` row's columns are (0-indexed): 0 index, 1 status,
2 predecessor index, 3 successor marker, 4 unused, 5 x, 6 y, 7 z,
8 diameter, 9 cell_name, ... Predecessor/successor indices are AceTree's
own frame-to-frame linking and are intentionally ignored here -- parent_id
is instead inferred later from name continuation plus the Sulston lineage
(the same pipeline `napari_lineage_editor.data._load_lineages._load_lineage`
already uses for other lineage CSVs). Rows with no name (unannotated
nuclei) are dropped.

Verified against the existing curated CSVs (by comparing x/y positions at
matching names across a range of time-offsets) that AceTree's raw `t###`
numbering already matches this codebase's loaded time convention 1:1 --
no time shift is needed.

Run with: uv run scripts/convert_acetree_to_csv.py <nuclei_dir> <output_csv>
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

# 0-indexed column positions in a t###-nuclei row.
_X_COL, _Y_COL, _Z_COL, _DIAMETER_COL, _NAME_COL = 5, 6, 7, 8, 9

# AceTree's placeholder name for a nucleus that hasn't been assigned a real
# lineage name yet (e.g. "Nuc1", "Nuc23") -- not a real cell identity.
_PLACEHOLDER_NAME = re.compile(r"^Nuc\d+$")


def load_acetree_dir(nuclei_dir: Path) -> pd.DataFrame:
    """Read every `t###-nuclei` file in `nuclei_dir` into one dataframe.

    Returns columns `time, cell_name, x, y, z, diameter`, one row per named
    nucleus per timepoint. Rows with no name, or with an AceTree
    placeholder name like `"Nuc1"` (not a real cell identity), are dropped.
    Any `(time, cell_name)` pair that still appears more than once is
    dropped entirely -- better to lose that one timepoint's name than
    guess which duplicate is correct.
    """
    rows = []
    for f in sorted(nuclei_dir.glob("t*-nuclei")):
        time = int(f.stem.split("-")[0].removeprefix("t"))
        for line in f.read_text().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) <= _NAME_COL:
                continue
            name = parts[_NAME_COL]
            if not name or _PLACEHOLDER_NAME.match(name):
                continue
            rows.append(
                {
                    "time": time,
                    "cell_name": name,
                    "x": float(parts[_X_COL]),
                    "y": float(parts[_Y_COL]),
                    "z": float(parts[_Z_COL]),
                    "diameter": float(parts[_DIAMETER_COL]),
                }
            )
    df = pd.DataFrame(rows)

    duplicated = df.duplicated(subset=["time", "cell_name"], keep=False)
    if duplicated.any():
        dupes = df.loc[duplicated, ["time", "cell_name"]].drop_duplicates()
        print(
            f"  dropping {duplicated.sum()} rows from {len(dupes)} duplicate (time, cell_name) pair(s):"
        )
        for _, row in dupes.iterrows():
            print(f"    t={row['time']} name={row['cell_name']}")
        df = df.loc[~duplicated]

    return df.sort_values(["time", "cell_name"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert an AceTree nuclei directory to a single lineage CSV."
    )
    parser.add_argument(
        "nuclei_dir",
        type=Path,
        help="Path to the AceTree 'nuclei' directory (containing t###-nuclei files).",
    )
    parser.add_argument("output_csv", type=Path, help="Path to write the combined CSV to.")
    args = parser.parse_args()

    df = load_acetree_dir(args.nuclei_dir)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.output_csv, index=False)
    print(f"wrote {len(df)} rows ({df['time'].min()}-{df['time'].max()}) to {args.output_csv}")


if __name__ == "__main__":
    main()
