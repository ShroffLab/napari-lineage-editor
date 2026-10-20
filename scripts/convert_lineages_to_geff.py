from __future__ import annotations

import argparse

from funtracks.import_export import write_to_geff

from napari_lineage_editor.data import get_sulston, registry
from napari_lineage_editor.data._load_lineages import load_lineage, tracks_from_lineage_df
from napari_lineage_editor.data._utils import to_local_path, to_uri

REGISTRY_PATH = "janelia://nrs.hhmi.org/shroff/Pharynx_Project/wormguides-data/registry"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a lineage representation's CSV to a geff store, and point the "
            "representation's path at the geff output so loading no longer has to "
            "rebuild the Tracks graph on every load."
        )
    )
    parser.add_argument("sample_id")
    parser.add_argument("repr_id")
    args = parser.parse_args()

    reg = registry(REGISTRY_PATH)
    repres = reg.load_representation(args.sample_id, args.repr_id)
    sulston = get_sulston(reg)

    lineage_df = load_lineage(repres, sulston, reg.path)
    tracks = tracks_from_lineage_df(lineage_df)

    csv_path = to_local_path(repres.path, registry_root=reg.path)
    geff_path = csv_path.with_suffix(".geff")
    write_to_geff(tracks, geff_path, overwrite=True)

    geff_uri = to_uri(geff_path, registry_root=reg.path, relative=True)
    reg.set_representation_fields(args.sample_id, args.repr_id, path=geff_uri)
    print(f"wrote {geff_path} and updated representation path to {geff_uri}")


if __name__ == "__main__":
    main()
