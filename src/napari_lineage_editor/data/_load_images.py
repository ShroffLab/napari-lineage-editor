from functools import lru_cache
from pathlib import Path

import dask.array as da
import tifffile
from dask import delayed

from napari_lineage_editor.data._celegans_metadata import CelegansRepMetadata

# Caches whole frames (one timepoint, or one timepoint-slice for embryo 3) so
# napari re-rendering the same slice while scrubbing the time/z sliders
# doesn't refetch it over SMB every time.
_FRAME_CACHE_SIZE = 32


@lru_cache(maxsize=_FRAME_CACHE_SIZE)
def _read_frame_cached(path: Path) -> object:
    """`tifffile.imread(path)`, cached so repeat reads of the same frame skip the SMB round-trip."""
    return tifffile.imread(path)


def _lazy_frame(path: Path, shape: tuple[int, ...], dtype: object) -> da.Array:
    """Wrap a single cached `tifffile.imread` call as a shaped, lazily-evaluated dask array."""
    return da.from_delayed(delayed(_read_frame_cached)(path), shape=shape, dtype=dtype)


def load_images(image_dir: str, metadata: CelegansRepMetadata) -> da.Array:
    """Lazily stack `image_dir`'s per-timepoint (or per-timepoint-per-slice) tiffs.

    No directory listing happens -- every file path is built directly from
    `metadata`'s explicit `start_time`/`end_time` (and `start_z`/`end_z`, if
    set) ranges, since the data lives on a slow network share.

    Args:
        image_dir: Directory containing the tiff files.
        metadata: This representation's `file_pattern` and time/z ranges.
            `file_pattern` contains `{t}`, and -- when each timepoint is
            itself a stack of per-slice 2D tiffs rather than one 3D tiff --
            `{z}` too.

    Returns:
        A dask array stacking every timepoint (and, if present, every slice)
        along new leading axes, with no file read until computed.
    """
    if metadata.file_pattern is None:
        raise RuntimeError("representation metadata has no 'file_pattern'")
    timepoints = range(metadata.start_time, metadata.end_time + 1)

    if metadata.start_z is None:
        paths = [Path(image_dir) / metadata.file_pattern.format(t=t) for t in timepoints]
        sample_frame = tifffile.imread(paths[0])
        frames = [_lazy_frame(path, sample_frame.shape, sample_frame.dtype) for path in paths]
        return da.stack(frames)

    z_slices = range(metadata.start_z, metadata.end_z + 1)
    sample_frame = tifffile.imread(
        Path(image_dir) / metadata.file_pattern.format(t=timepoints[0], z=z_slices[0])
    )
    volumes = []
    for t in timepoints:
        slices = [
            _lazy_frame(
                Path(image_dir) / metadata.file_pattern.format(t=t, z=z),
                sample_frame.shape,
                sample_frame.dtype,
            )
            for z in z_slices
        ]
        volumes.append(da.stack(slices))
    return da.stack(volumes)
