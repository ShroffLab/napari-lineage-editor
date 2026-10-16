"""Napari UI for editing c elegans lineages"""

from importlib.metadata import PackageNotFoundError, version

from ._celegans_metadata import registry
from ._load_images import load_images
from ._load_lineages import (
    LINEAGE_NODE_NAME_MAP,
    load_geff_lineage,
    load_lineages,
    load_linked_lineage,
    name_to_parent_name,
    resolve_division_edges,
    tracks_from_lineage_df,
)

try:
    __version__ = version("napari-lineage-editor")
except PackageNotFoundError:  # package is not installed
    __version__ = "uninstalled"

# Everything listed here becomes the public API and gets an API docs page.
# Implementation lives in underscore-prefixed modules; see CONTRIBUTING.md.
__all__ = [
    "LINEAGE_NODE_NAME_MAP",
    "__version__",
    "load_geff_lineage",
    "load_images",
    "load_lineages",
    "load_linked_lineage",
    "name_to_parent_name",
    "registry",
    "resolve_division_edges",
    "tracks_from_lineage_df",
]
