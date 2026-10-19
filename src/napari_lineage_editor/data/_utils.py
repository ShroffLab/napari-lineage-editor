from __future__ import annotations

from typing import TYPE_CHECKING
from pathlib import Path
from ._load_lineages import load_geff_lineage
import pandas as pd

if TYPE_CHECKING:
    from dataset_registry import Registry

REGISTRY_PATH = Path("/Users/malinmayorc/projects/celegans-cell-id/wormguides-data/registry")
IMAGE_METADATA_TYPE = "image"
LINEAGE_METADATA_TYPE = "lineage"
SULSTON_SAMPLE_NAME = "Sulston"

def get_sulston(registry: Registry) -> pd.DataFrame | None:
    """Path to the registry's "Sulston" sample's lineage representation, or `None` if absent."""
    sulston_id = "sulston-axolotl"
    tracklet_repr_id = "lineage-tree-cockatoo"
    return load_geff_lineage(registry.load_representation(sulston_id, tracklet_repr_id).path)
