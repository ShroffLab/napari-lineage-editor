"""Napari UI for editing c elegans lineages"""

from importlib.metadata import PackageNotFoundError, version

from ._actions import SetName, UserResolveDiv, UserSetName, UserSwapNames
from ._match_lineages import get_conflicts, match_nodes, matches_to_df

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
    "get_conflicts",
    "match_nodes",
    "matches_to_df",
]
