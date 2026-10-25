"""Compare two lineage representations of the same sample with traccuracy,
group every resulting divergence into one "case", and write a text report
plus a GraphML + PNG per case.

A case is a connected component of the graph formed by: every node/edge
traccuracy flagged as FP or FN (plus one tracklet neighbor on each side,
for context), union both representations, plus an undirected "match" edge
between every pair of nodes the matcher paired up. That's exactly the
graph each case's plot is drawn from, so the report and the images always
agree on what a "case" is -- there's deliberately no further
categorization (shifted/swapped/missing-edge/etc.) on top; the plot shows
the shape of the divergence directly, which is clearer than naming it.

For each case, writes `<output_dir>/<sample_id>/case_<n>.graphml` (the
case's nodes/edges from both representations plus "match" edges between
corresponding nodes, suitable for Cytoscape/Gephi/networkx) and
`case_<n>.png` (t on x, x+y+z collapsed to a single plotting coordinate on
y, each representation's own display name in the legend). The text report
(`<sample_id>_report.txt`) has one line per case: each side's distinct
tracklet name(s) and time range(s).

Run with:
  uv run --with matplotlib scripts/compare_lineages.py <sample_id> <repr_id_a> <repr_id_b> [...]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd
import traccuracy
from matplotlib.patches import Patch
from traccuracy import EdgeFlag, NodeFlag
from traccuracy.matchers import PointMatcher
from traccuracy.metrics import BasicMetrics

from napari_lineage_editor.data import get_sulston, load_lineage, nx_from_lineage_df, registry

REGISTRY_PATH = "janelia://nrs.hhmi.org/shroff/Pharynx_Project/wormguides-data/registry"

GT_COLOR = "black"
PRED_COLOR = "tab:blue"
MATCH_COLOR = "grey"


def _error_node_ids(graph: nx.DiGraph, fp_fn_nodes, fp_fn_edges) -> set:
    """Nodes flagged FP or FN, plus each flagged node's immediate
    predecessor and successor in the full graph, for context."""
    nodes = set(fp_fn_nodes)
    for u, v in fp_fn_edges:
        nodes.add(u)
        nodes.add(v)
    for node_id in list(nodes):
        nodes.update(succ for _, succ in graph.out_edges(node_id))
        nodes.update(pred for pred, _ in graph.in_edges(node_id))
    return nodes


def _prefixed(sub: nx.DiGraph, source: str) -> nx.DiGraph:
    """Rename every node to `<source>:<original_id>`, keeping the original
    id and source label as attributes."""
    renamed = nx.relabel_nodes(sub, {n: f"{source}:{n}" for n in sub.nodes}, copy=True)
    for node_id, attrs in renamed.nodes(data=True):
        attrs["source"] = source
        attrs["source_id"] = node_id.split(":", 1)[1]
    return renamed


def _merged_graph_with_matches(gt_sub: nx.DiGraph, pred_sub: nx.DiGraph, mapping) -> nx.Graph:
    merged = nx.Graph()
    merged.add_nodes_from(gt_sub.nodes(data=True))
    merged.add_nodes_from(pred_sub.nodes(data=True))
    merged.add_edges_from(
        (u, v, {**d, "edge_type": "tracklet"}) for u, v, d in gt_sub.edges(data=True)
    )
    merged.add_edges_from(
        (u, v, {**d, "edge_type": "tracklet"}) for u, v, d in pred_sub.edges(data=True)
    )

    for gt_id, pred_id in mapping:
        gt_node, pred_node = f"gt:{gt_id}", f"pred:{pred_id}"
        if gt_node in merged and pred_node in merged:
            merged.add_edge(gt_node, pred_node, edge_type="match")

    return merged


def compare_lineages(
    gt_df: pd.DataFrame,
    pred_df: pd.DataFrame,
    gt_full_label: str,
    pred_full_label: str,
    dist_threshold=5,
) -> dict:
    """Match two lineages with traccuracy and group every resulting
    divergence into cases (connected components of flagged nodes/edges +
    match edges -- see module docstring).

    `gt_df`/`pred_df` are plain lineage dataframes (`time, cell_name, x, y,
    z, diameter, cell_id, parent_id`); `gt_full_label`/`pred_full_label`
    are their display names, used in the report and plot legends. "gt"
    (ground truth) and "pred" (prediction) are traccuracy's own
    terminology for the two sides of a matching -- which one is passed as
    which doesn't change the comparison's content.

    Returns a dict with:
    - gt_full_label, pred_full_label: the display names passed in.
    - cases: list of `networkx.Graph`, one per divergence -- each node
      carries `source` ("gt"/"pred"), `source_id`, `name`, `t`, `x`, `y`,
      `z`; each edge carries `edge_type` ("tracklet" or "match").
    """
    start_time = max(gt_df["time"].min(), pred_df["time"].min())
    end_time = min(gt_df["time"].max(), pred_df["time"].max())
    gt_cropped = gt_df[gt_df["time"].between(start_time, end_time)]
    pred_cropped = pred_df[pred_df["time"].between(start_time, end_time)]

    gt_graph = traccuracy.TrackingGraph(
        nx_from_lineage_df(gt_cropped), location_keys=["z", "y", "x"]
    )
    pred_graph = traccuracy.TrackingGraph(
        nx_from_lineage_df(pred_cropped), location_keys=["z", "y", "x"]
    )
    pm = PointMatcher(threshold=dist_threshold)
    _, matched = traccuracy.run_metrics(gt_graph, pred_graph, matcher=pm, metrics=[BasicMetrics()])

    fp_nodes = pred_graph.get_nodes_with_flag(NodeFlag.FALSE_POS)
    fn_nodes = gt_graph.get_nodes_with_flag(NodeFlag.FALSE_NEG)
    fp_edges = pred_graph.get_edges_with_flag(EdgeFlag.FALSE_POS)
    fn_edges = gt_graph.get_edges_with_flag(EdgeFlag.FALSE_NEG)

    gt_ids = _error_node_ids(gt_graph.graph, fn_nodes, fn_edges)
    pred_ids = _error_node_ids(pred_graph.graph, fp_nodes, fp_edges)

    # pull in each side's matched counterpart too, even if it has no FP/FN
    # flag of its own -- otherwise a match edge has nothing to connect to.
    gt_ids |= {
        m
        for n in pred_ids
        if (m := matched.get_pred_gt_match(n)) is not None and m in gt_graph.graph
    }
    pred_ids |= {
        m
        for n in gt_ids
        if (m := matched.get_gt_pred_match(n)) is not None and m in pred_graph.graph
    }

    gt_sub = _prefixed(gt_graph.graph.subgraph(gt_ids).copy(), "gt")
    pred_sub = _prefixed(pred_graph.graph.subgraph(pred_ids).copy(), "pred")
    merged = _merged_graph_with_matches(gt_sub, pred_sub, matched.mapping)

    cases = [merged.subgraph(component).copy() for component in nx.connected_components(merged)]

    return {
        "gt_full_label": gt_full_label,
        "pred_full_label": pred_full_label,
        "cases": cases,
    }


def _case_summary(case_graph: nx.Graph) -> str:
    """One line per case: each side's distinct tracklet name(s) and time
    range(s), e.g. `A: ABal [12-27]  B: ABal [12-25], ABar [12-25]`."""
    parts = []
    for source, tag in (("gt", "A"), ("pred", "B")):
        by_name: dict[str, list[int]] = {}
        for _, attrs in case_graph.nodes(data=True):
            if attrs["source"] == source:
                by_name.setdefault(attrs["name"], []).append(attrs["t"])
        ranges = ", ".join(f"{name} [{min(ts)}-{max(ts)}]" for name, ts in sorted(by_name.items()))
        if ranges:
            parts.append(f"{tag}: {ranges}")
    return "  ".join(parts)


def format_report(title: str, result: dict) -> str:
    cases = result["cases"]
    lines = [
        title,
        f"  A = {result['gt_full_label']}",
        f"  B = {result['pred_full_label']}",
        f"  {len(cases)} divergence(s) found." if cases else "  No divergences found.",
    ]
    # case numbers match the exported case_<n>.graphml/.png filenames (see
    # export_cases), so a reader can go straight from a report line to its plot.
    lines.extend(f"  [case {i}] {_case_summary(case)}" for i, case in enumerate(cases))
    return "\n".join(lines) + "\n"


def _collapsed_coord(attrs: dict) -> float:
    """Collapse x/y/z into a single 1D plotting coordinate.

    Using y alone can land two genuinely distinct tracklets at the same
    plotted position if they only differ in x or z; summing all three
    spreads out positions that are actually different in 3D, even if they
    happen to share one coordinate.
    """
    return attrs["x"] + attrs["y"] + attrs["z"]


def plot_case_graph(
    case_graph: nx.Graph, gt_full_label: str, pred_full_label: str, out_path: Path
) -> None:
    """Plot a case's nodes (t on x, x+y+z collapsed to a single plotting
    coordinate on y), gt in black and pred in blue, tracklet edges solid,
    and match edges dashed grey. Nodes at the same plotted position get a
    tiny offset so they don't fully overlap."""
    colors = {"gt": GT_COLOR, "pred": PRED_COLOR}
    labels = {"gt": gt_full_label, "pred": pred_full_label}
    all_y = [_collapsed_coord(attrs) for _, attrs in case_graph.nodes(data=True)]
    y_range = (max(all_y) - min(all_y)) if len(all_y) > 1 else 1.0
    y_offset = max(y_range, 1.0) * 0.03
    plot_dy = {"gt": -y_offset, "pred": y_offset}
    label_dy = {"gt": -max(y_range, 1.0) * 0.06, "pred": max(y_range, 1.0) * 0.06}

    def _pos(node_id):
        attrs = case_graph.nodes[node_id]
        return attrs["t"], _collapsed_coord(attrs) + plot_dy[attrs["source"]]

    # Label only the earliest-timepoint node per (source, name) tracklet --
    # labeling every node gets unreadable fast once a tracklet spans many
    # timepoints.
    first_node_per_tracklet: dict[tuple[str, str], str] = {}
    for node_id, attrs in sorted(case_graph.nodes(data=True), key=lambda item: item[1]["t"]):
        key = (attrs["source"], attrs["name"])
        first_node_per_tracklet.setdefault(key, node_id)
    label_nodes = set(first_node_per_tracklet.values())

    n_timepoints = len({attrs["t"] for _, attrs in case_graph.nodes(data=True)})
    fig, ax = plt.subplots(figsize=(max(6, n_timepoints * 0.9), 4))
    for node_id, attrs in case_graph.nodes(data=True):
        color = colors[attrs["source"]]
        x, y = _pos(node_id)
        ax.scatter(x, y, color=color, zorder=3)
        if node_id in label_nodes:
            ax.text(
                x,
                y + label_dy[attrs["source"]],
                attrs["name"],
                color=color,
                ha="center",
                va="center",
                fontsize=9,
            )

    for u, v, attrs in case_graph.edges(data=True):
        xs, ys = zip(_pos(u), _pos(v))
        if attrs["edge_type"] == "match":
            ax.plot(xs, ys, color=MATCH_COLOR, linestyle="dashed", zorder=1)
        else:
            ax.plot(xs, ys, color=colors[case_graph.nodes[u]["source"]], zorder=2)

    handles = [Patch(color=colors[s], label=labels[s]) for s in ("gt", "pred")]
    ax.legend(handles=handles, loc="best", fontsize=8)

    ax.set_xlabel("t")
    ax.set_yticks([])
    ax.margins(x=0.3, y=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def export_cases(result: dict, output_dir: Path) -> int:
    """Write one GraphML + PNG per case in `result["cases"]` to `output_dir`."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for i, case_graph in enumerate(result["cases"]):
        nx.write_graphml(case_graph, output_dir / f"case_{i}.graphml")
        plot_case_graph(
            case_graph,
            result["gt_full_label"],
            result["pred_full_label"],
            output_dir / f"case_{i}.png",
        )
    return len(result["cases"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare two lineage representations of the same sample."
    )
    parser.add_argument("sample_id", help="Sample id (e.g. 'wg-embryo-1-badger').")
    parser.add_argument("gt_repr_id", help="Representation id to use as ground truth.")
    parser.add_argument("pred_repr_id", help="Representation id to use as prediction.")
    parser.add_argument("--dist-threshold", type=float, default=200)
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("comparison"),
        help="Directory to write the report and case subgraphs/plots to (default: ./comparison).",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    reg = registry(REGISTRY_PATH)
    sulston = get_sulston(reg)

    gt_repres = reg.load_representation(args.sample_id, args.gt_repr_id)
    pred_repres = reg.load_representation(args.sample_id, args.pred_repr_id)
    gt_df = load_lineage(gt_repres, sulston, reg.path)
    pred_df = load_lineage(pred_repres, sulston, reg.path)

    result = compare_lineages(
        gt_df, pred_df, gt_repres.name, pred_repres.name, dist_threshold=args.dist_threshold
    )

    report = format_report(args.sample_id, result)
    report_path = args.output_dir / f"{args.sample_id}_report.txt"
    report_path.write_text(report)

    n_cases = export_cases(result, args.output_dir / args.sample_id)
    print(
        f"{args.sample_id}: wrote {report_path} and {n_cases} case(s) to {args.output_dir / args.sample_id}"
    )


if __name__ == "__main__":
    main()
