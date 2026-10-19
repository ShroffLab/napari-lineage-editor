from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ._load_lineages import load_sulston_lineage

if TYPE_CHECKING:
    import networkx as nx
    from dataset_registry import Registry

REGISTRY_PATH = Path("/Users/malinmayorc/projects/celegans-cell-id/wormguides-data/registry")
IMAGE_METADATA_TYPE = "image"
LINEAGE_METADATA_TYPE = "lineage"
SULSTON_SAMPLE_NAME = "Sulston"


def get_sulston(registry: Registry) -> nx.DiGraph:
    """Path to the registry's "Sulston" sample's lineage representation, or `None` if absent."""
    sulston_id = "sulston-axolotl"
    tracklet_repr_id = "lineage-tree-cockatoo"
    return load_sulston_lineage(registry.load_representation(sulston_id, tracklet_repr_id).path)
