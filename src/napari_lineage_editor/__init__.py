"""Napari UI for editing c elegans lineages"""

from importlib.metadata import PackageNotFoundError, version

from ._actions import SetName, UserSetName, UserSwapNames
from ._name_annotator import NameAnnotator

try:
    __version__ = version("napari-lineage-editor")
except PackageNotFoundError:  # package is not installed
    __version__ = "uninstalled"

# Everything listed here becomes the public API and gets an API docs page.
# Implementation lives in underscore-prefixed modules; see CONTRIBUTING.md.
__all__ = [
    "NameAnnotator",
    "SetName",
    "UserSetName",
    "UserSwapNames",
    "__version__",
]
