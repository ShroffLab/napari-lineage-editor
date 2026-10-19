"""Napari UI for editing c elegans lineages"""

from importlib.metadata import PackageNotFoundError, version

from ._actions import SetName, UserResolveDiv, UserSetName, UserSwapNames
from ._celegans_metadata import registry
from ._load_images import load_images
from ._load_lineages import (
    children_of_name,
    load_geff_lineage,
    load_lineages,
    load_linked_lineage,
    name_to_parent_name,
    resolve_division_edges,
    suggest_child_names,
    tracks_from_lineage_df,
)

try:
    __version__ = version("napari-lineage-editor")
except PackageNotFoundError:  # package is not installed
    __version__ = "uninstalled"

# Everything listed here becomes the public API and gets an API docs page.
# Implementation lives in underscore-prefixed modules; see CONTRIBUTING.md.
__all__ = [
    "SetName",
    "UserResolveDiv",
    "UserSetName",
    "UserSwapNames",
    "__version__",
    "children_of_name",
    "load_geff_lineage",
    "load_images",
    "load_lineages",
    "load_linked_lineage",
    "name_to_parent_name",
    "registry",
    "resolve_division_edges",
    "suggest_child_names",
    "tracks_from_lineage_df",
]
