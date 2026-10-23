from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import geff
import networkx as nx
from janelia_pathlib import from_janelia_uri, to_janelia_uri

if TYPE_CHECKING:
    from dataset_registry import Registry

REGISTRY_PATH = Path("/Users/malinmayorc/projects/celegans-cell-id/wormguides-data/registry")
IMAGE_METADATA_TYPE = "image"
LINEAGE_METADATA_TYPE = "lineage"
SULSTON_SAMPLE_NAME = "Sulston"
REGISTRY_PATH_PREFIX = "registry://"

# The geff lineage has a handful of names stored with a lowercase typo in
# the second letter ("Ab..." instead of "AB..."), which makes has_node()
# fail for callers using the correct casing (e.g. names read from curated
# CSVs). Fix up just these known cases rather than guessing at a general
# casing rule, since Sulston names otherwise rely on meaningful lowercase
# suffixes (a/p/l/r/d/v).
SULSTON_NAME_FIXUPS = {
    "Abprppppppa": "ABprppppppa",
    "Abaraaaapaa": "ABaraaaapaa",
    "Abalppaappp": "ABalppaappp",
    "Abplppppppa": "ABplppppppa",
}

# The geff lineage is also missing a handful of real divisions outright --
# not a typo, the child nodes just don't exist in the tree at all, even
# though the parent does. Patch in the missing parent/child edges by name
# (confirmed present with these exact parents in the more complete TSV
# lineage at /Users/malinmayorc/data/celegans-cell-id/lineage_hermaphrodite.tsv).
SULSTON_MISSING_DIVISIONS = {
    "ABplapappppa": ["ABplapappppaa", "ABplapappppap"],
}


def from_relative_path(
    path: str,
    registry_root: Path,
) -> str:
    """Resolve a representation's `path` relative to the registry root if it starts with registry://

    Lets registry TOML files store paths relative to the registry's own
    location (e.g. `registry://annotation-versions/foo.csv`) instead of an
    absolute path baked in for whoever last wrote the file -- so cloning the
    registry onto another machine doesn't require rewriting every path.
    """
    if path.startswith(REGISTRY_PATH_PREFIX):
        return str(registry_root / path.removeprefix(REGISTRY_PATH_PREFIX))
    return path


def to_relative_path(
    path: Path,
    registry_root: Path,
) -> str:
    rel_path = path.relative_to(registry_root, walk_up=True)
    # Not `Path(REGISTRY_PATH_PREFIX) / rel_path`: pathlib collapses the
    # "//" in "registry://" during normalization, producing "registry:/..."
    # instead of "registry://...".
    return REGISTRY_PATH_PREFIX + str(rel_path)


def to_local_path(uri: str, registry_root: Path) -> Path:
    return Path(from_janelia_uri(from_relative_path(uri, registry_root=registry_root)))


def to_uri(local_path: Path, registry_root: Path, relative: bool = False) -> str:
    if relative:
        return to_relative_path(local_path, registry_root)
    else:
        return to_janelia_uri(local_path)


def get_sulston(registry: Registry) -> nx.DiGraph:
    """Path to the registry's "Sulston" sample's lineage representation, or `None` if absent."""
    sulston_id = "sulston-axolotl"
    tracklet_repr_id = "lineage-tree-cockatoo"
    path = to_local_path(
        registry.load_representation(sulston_id, tracklet_repr_id).path, registry.path
    )
    graph, _metadata = geff.read(path, backend="networkx")

    names = {
        node_id: SULSTON_NAME_FIXUPS.get(name, name)
        for node_id, name in nx.get_node_attributes(graph, "name").items()
    }
    graph = nx.relabel_nodes(graph, names)

    for parent_name, child_names in SULSTON_MISSING_DIVISIONS.items():
        for child_name in child_names:
            graph.add_edge(parent_name, child_name)

    return graph
