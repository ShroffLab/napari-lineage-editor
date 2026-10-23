"""Compare two lineage representations of the same sample with traccuracy,
categorize every resulting divergence into one "case", and write a text
report plus a GraphML + PNG per case.

A case is one tracklet-level discrepancy -- or, for a shift/swap seen from
both sides, the merged pair of tracklets describing it. Categories:
- "extra tracklet": exists in B only, no matched endpoint in A at all.
- "dangling tracklet": exists in B only, matched to A at exactly one end.
- "missing edge": a single node (or a division-crossing pair) that matches
  A perfectly at both ends, but the connecting edge itself is absent in A.
- "shifted location": the same tracklet exists in both, just drifted.
- "swapped identity": tracklets cross -- a tracklet's matched counterpart
  at one end has a different name than at the other end.

For each case, writes `<output_dir>/<sample_id>/case_<n>.graphml` (the
case's nodes/edges from both representations plus "match" edges between
corresponding nodes, suitable for Cytoscape/Gephi/networkx) and
`case_<n>.png` (t on x, x+y+z collapsed to a single plotting coordinate on
y, each representation's own display name in the legend). The text report
(`<sample_id>_report.txt`) has a per-category count summary followed by
one line per case.

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

EXTRA_TRACKLET = "extra tracklet (no matched ends)"
DANGLING_TRACKLET = "dangling tracklet (one matched end)"
MISSING_EDGE = "missing edge"
SHIFTED_LOCATION = "shifted location"
SWAPPED_IDENTITY = "swapped identity"
ALL_CATEGORIES = (
    EXTRA_TRACKLET,
    DANGLING_TRACKLET,
    MISSING_EDGE,
    SHIFTED_LOCATION,
    SWAPPED_IDENTITY,
)

GT_COLOR = "black"
PRED_COLOR = "tab:blue"
MATCH_COLOR = "grey"


def _node_label(graph: nx.DiGraph, node_id) -> str:
    attrs = graph.nodes[node_id]
    return f"{attrs.get('name', '?')} (id={node_id}, t={attrs.get('t', '?')})"


def _nodes_to_tracklet_ranges(
    graph: nx.DiGraph, node_ids, flagged_edges, get_match, other_graph
) -> list[dict]:
    """Collapse flagged nodes into tracklet chains by walking flagged edges,
    e.g. `ABal [12-27]`.

    A chain is built by following `flagged_edges` (FP or FN edges) node by
    node; a node with no flagged edge to walk becomes its own length-1
    chain. This means a chain can cross a division (and so span two
    different `name`s) when the flagged edge itself does -- that's the
    degenerate case of a single missing edge between two otherwise-correct
    nodes, not a genuine multi-node tracklet.

    `get_match` is called on each chain's start and end node to look up the
    matched node id in `other_graph`, if any. The chain is categorized by
    how many ends are matched:
    - a length-1 chain (start_node == end_node) whose one node is matched:
      `MISSING_EDGE`.
    - both ends matched (multi-node chain): `SWAPPED_IDENTITY` if either
      matched end has a different name than that end's own node, else
      `SHIFTED_LOCATION`.
    - exactly one end matched: `DANGLING_TRACKLET`.
    - neither end matched: `EXTRA_TRACKLET`.

    Returns a list of dicts with keys: name, start_t, end_t, start_node,
    end_node, category, start_match, end_match (matched node id or None for
    each end).
    """
    node_ids = set(node_ids)
    next_by_node = {u: v for u, v in flagged_edges if u in node_ids and v in node_ids}
    has_incoming = set(next_by_node.values())

    chains = []
    for node_id in node_ids:
        if node_id in has_incoming:
            continue  # not a chain start
        chain = [node_id]
        while chain[-1] in next_by_node:
            chain.append(next_by_node[chain[-1]])
        chains.append(chain)

    def _match(node):
        match = get_match(node)
        return match if match is not None and match in other_graph else None

    records = []
    for chain in chains:
        start_node, end_node = chain[0], chain[-1]
        start_name = graph.nodes[start_node]["name"]
        end_name = graph.nodes[end_node]["name"]
        is_single_node = end_node == start_node
        start_match = _match(start_node)
        end_match = start_match if is_single_node else _match(end_node)

        if is_single_node:
            category = MISSING_EDGE if start_match is not None else EXTRA_TRACKLET
        else:
            n_matched = sum(m is not None for m in (start_match, end_match))
            if n_matched == 2:
                start_match_name = other_graph.nodes[start_match]["name"]
                end_match_name = other_graph.nodes[end_match]["name"]
                swapped = start_match_name != start_name or end_match_name != end_name
                category = SWAPPED_IDENTITY if swapped else SHIFTED_LOCATION
            elif n_matched == 1:
                category = DANGLING_TRACKLET
            else:
                category = EXTRA_TRACKLET

        records.append(
            {
                "name": start_name if start_name == end_name else f"{start_name}->{end_name}",
                "start_t": graph.nodes[start_node]["t"],
                "end_t": graph.nodes[end_node]["t"],
                "start_node": start_node,
                "end_node": end_node,
                "start_match": start_match,
                "end_match": end_match,
                "category": category,
            }
        )
    return records


def _merge_shift_and_swap_cases(
    fp_records, fn_records, gt_graph, pred_graph, gt_label, pred_label
) -> tuple[list[dict], list[dict], list[dict]]:
    """Merge gt/pred "shifted location"/"swapped identity" records that
    describe the same divergence, so each is reported once.

    Two records describe the same divergence when they're linked by matched
    endpoints: a pred tracklet's start/end match a gt node, and vice versa.
    A simple shift is a 1-pred-to-1-gt link; a true swap (two tracklets
    crossing) is a 2-to-2 link, since each side's tracklet matches into a
    *different* tracklet on the other side at each end. Build a graph with
    one node per record and an edge for every match link, then take
    connected components so both shapes merge into one reported group.

    Returns (merged_cases, unpaired_fp, unpaired_fn).
    """
    pairable = {SHIFTED_LOCATION, SWAPPED_IDENTITY}
    fp_pairable = [r for r in fp_records if r["category"] in pairable]
    fn_pairable = [r for r in fn_records if r["category"] in pairable]

    link_graph = nx.Graph()
    link_graph.add_nodes_from(("fp", i) for i in range(len(fp_pairable)))
    link_graph.add_nodes_from(("fn", i) for i in range(len(fn_pairable)))
    fn_by_node = {}
    for i, r in enumerate(fn_pairable):
        fn_by_node.setdefault(r["start_node"], []).append(i)
        fn_by_node.setdefault(r["end_node"], []).append(i)
    for fp_i, fp in enumerate(fp_pairable):
        for gt_match in {fp["start_match"], fp["end_match"]}:
            if gt_match is None:
                continue
            for fn_i in fn_by_node.get(gt_match, ()):
                link_graph.add_edge(("fp", fp_i), ("fn", fn_i))

    unpaired_fp = [r for r in fp_records if r["category"] not in pairable]
    unpaired_fn = [r for r in fn_records if r["category"] not in pairable]
    merged = []
    for component in nx.connected_components(link_graph):
        group = {"fp": [], "fn": []}
        for side, i in component:
            group[side].append((fp_pairable if side == "fp" else fn_pairable)[i])
        fps, fns = group["fp"], group["fn"]
        if not fps or not fns:
            unpaired_fp.extend(fps)
            unpaired_fn.extend(fns)
            continue

        category = (
            SWAPPED_IDENTITY
            if any(r["category"] == SWAPPED_IDENTITY for r in fps + fns)
            else SHIFTED_LOCATION
        )
        divergences = []
        for fp in fps:
            for which, pred_node, gt_node in (
                ("start", fp["start_node"], fp["start_match"]),
                ("end", fp["end_node"], fp["end_match"]),
            ):
                if gt_node is None or (which == "end" and fp["end_node"] == fp["start_node"]):
                    continue
                divergences.append(
                    f"{pred_label}:{_node_label(pred_graph.graph, pred_node)}"
                    f" -> {gt_label}:{_node_label(gt_graph.graph, gt_node)}"
                )
        pred_part = ", ".join(
            f"{pred_label}:{r['name']} [{r['start_t']}-{r['end_t']}]" for r in fps
        )
        gt_part = ", ".join(f"{gt_label}:{r['name']} [{r['start_t']}-{r['end_t']}]" for r in fns)
        summary = f"{pred_part} <-> {gt_part}"
        if divergences:
            summary += " (" + "; ".join(divergences) + ")"

        merged.append({"category": category, "summary": summary, "fp": fps, "fn": fns})

    # A shift/swap record that never paired with a counterpart on the other
    # side isn't a tracklet that exists (shifted or swapped) in both
    # representations -- it's a tracklet that exists in only one, which is
    # really a missing edge from the other representation's perspective.
    for r in unpaired_fp + unpaired_fn:
        if r["category"] in pairable:
            r["category"] = MISSING_EDGE

    return merged, unpaired_fp, unpaired_fn


def _case_summary(graph, other_graph, label, other_label, record) -> str:
    summary = f"{label}:{record['name']} [{record['start_t']}-{record['end_t']}]"
    divergences = []
    for which, node, match in (
        ("start", record["start_node"], record["start_match"]),
        ("end", record["end_node"], record["end_match"]),
    ):
        if match is None or (which == "end" and record["end_node"] == record["start_node"]):
            continue
        divergences.append(
            f"{which}={label}:{_node_label(graph, node)} -> {other_label}:{_node_label(other_graph, match)}"
        )
    if divergences:
        summary += " (" + "; ".join(divergences) + ")"
    return summary


def compare_lineages(
    gt_df: pd.DataFrame,
    pred_df: pd.DataFrame,
    gt_full_label: str,
    pred_full_label: str,
    dist_threshold=5,
) -> dict:
    """Match two lineages with traccuracy and categorize every resulting
    divergence into one "case" per tracklet-level discrepancy (or merged
    pair of tracklets, for shifts/swaps seen from both sides).

    `gt_df`/`pred_df` are plain lineage dataframes (`time, cell_name, x, y,
    z, diameter, cell_id, parent_id`); `gt_full_label`/`pred_full_label`
    are their display names. Case summaries use the short tags "A" (gt)
    and "B" (pred) instead of the full names, since a full name repeated
    in every case line gets unreadable fast -- the full names are kept
    alongside for the report header / plot legend. "gt" (ground truth) and
    "pred" (prediction) are traccuracy's own terminology for the two sides
    of a matching -- which one is passed as which doesn't change the
    comparison's content, only the case summaries' phrasing.

    Returns a dict with:
    - gt_graph, pred_graph, matched: the traccuracy objects, for further
      inspection or plotting.
    - gt_full_label, pred_full_label: the display names passed in.
    - cases: list of dicts, each with `category`, `summary` (one-line
      description using the "A"/"B" tags), and `fp`/`fn` (the underlying
      tracklet-range records, for plotting) -- the unified result to
      report or visualize.
    """
    gt_label, pred_label = "A", "B"
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

    fp_tracklet_nodes = set(fp_nodes)
    fn_tracklet_nodes = set(fn_nodes)
    for u, v in fp_edges:
        fp_tracklet_nodes.update((u, v))
    for u, v in fn_edges:
        fn_tracklet_nodes.update((u, v))

    fp_records = _nodes_to_tracklet_ranges(
        pred_graph.graph, fp_tracklet_nodes, fp_edges, matched.get_pred_gt_match, gt_graph.graph
    )
    fn_records = _nodes_to_tracklet_ranges(
        gt_graph.graph, fn_tracklet_nodes, fn_edges, matched.get_gt_pred_match, pred_graph.graph
    )

    merged, unpaired_fp, unpaired_fn = _merge_shift_and_swap_cases(
        fp_records, fn_records, gt_graph, pred_graph, gt_label, pred_label
    )

    cases = list(merged)
    for r in unpaired_fp:
        cases.append(
            {
                "category": r["category"],
                "summary": _case_summary(pred_graph.graph, gt_graph.graph, pred_label, gt_label, r),
                "fp": [r],
                "fn": [],
            }
        )
    for r in unpaired_fn:
        cases.append(
            {
                "category": r["category"],
                "summary": _case_summary(gt_graph.graph, pred_graph.graph, gt_label, pred_label, r),
                "fp": [],
                "fn": [r],
            }
        )

    return {
        "gt_graph": gt_graph,
        "pred_graph": pred_graph,
        "gt_full_label": gt_full_label,
        "pred_full_label": pred_full_label,
        "matched": matched,
        "cases": cases,
    }


def format_report(title: str, result: dict) -> str:
    cases = result["cases"]
    lines = [title, f"  A = {result['gt_full_label']}", f"  B = {result['pred_full_label']}"]

    counts = {
        category: sum(1 for c in cases if c["category"] == category) for category in ALL_CATEGORIES
    }
    summary = ", ".join(
        f"{category}={counts[category]}" for category in ALL_CATEGORIES if counts[category]
    )
    lines.append(f"  Summary: {summary}")
    if not any(counts.values()):
        lines.append("  No divergences found.")
        return "\n".join(lines) + "\n"

    for category in ALL_CATEGORIES:
        matching = [c for c in cases if c["category"] == category]
        if not matching:
            continue
        lines.append(f"  {category}:")
        for case in sorted(matching, key=lambda c: c["summary"]):
            lines.append(f"    {case['summary']}")
    return "\n".join(lines) + "\n"


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


def _collapsed_coord(attrs: dict) -> float:
    """Collapse x/y/z into a single 1D plotting coordinate.

    Using y alone can land two genuinely distinct tracklets at the same
    plotted position if they only differ in x or z; summing all three
    spreads out positions that are actually different in 3D, even if they
    happen to share one coordinate.
    """
    return attrs["x"] + attrs["y"] + attrs["z"]


def plot_case_graph(case_graph: nx.Graph, gt_label: str, pred_label: str, out_path: Path) -> None:
    """Plot a case's nodes (t on x, x+y+z collapsed to a single plotting
    coordinate on y), gt in black and pred in blue, tracklet edges solid,
    and match edges dashed grey. Nodes at the same plotted position get a
    tiny offset so they don't fully overlap."""
    colors = {"gt": GT_COLOR, "pred": PRED_COLOR}
    labels = {"gt": gt_label, "pred": pred_label}
    all_y = [_collapsed_coord(attrs) for _, attrs in case_graph.nodes(data=True)]
    y_range = (max(all_y) - min(all_y)) if len(all_y) > 1 else 1.0
    y_offset = max(y_range, 1.0) * 0.03
    plot_dy = {"gt": -y_offset, "pred": y_offset}
    label_dy = {"gt": -max(y_range, 1.0) * 0.06, "pred": max(y_range, 1.0) * 0.06}

    def _pos(node_id):
        attrs = case_graph.nodes[node_id]
        return attrs["t"], _collapsed_coord(attrs) + plot_dy[attrs["source"]]

    n_timepoints = len({attrs["t"] for _, attrs in case_graph.nodes(data=True)})
    fig, ax = plt.subplots(figsize=(max(6, n_timepoints * 0.9), 4))
    for node_id, attrs in case_graph.nodes(data=True):
        color = colors[attrs["source"]]
        x, y = _pos(node_id)
        ax.scatter(x, y, color=color, zorder=3)
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
    gt_graph, pred_graph = result["gt_graph"], result["pred_graph"]
    gt_full_label, pred_full_label = result["gt_full_label"], result["pred_full_label"]
    matched = result["matched"]

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

    output_dir.mkdir(parents=True, exist_ok=True)
    components = list(nx.connected_components(merged))
    for i, component in enumerate(components):
        case_graph = merged.subgraph(component).copy()
        nx.write_graphml(case_graph, output_dir / f"case_{i}.graphml")
        plot_case_graph(case_graph, gt_full_label, pred_full_label, output_dir / f"case_{i}.png")

    return len(components)


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
