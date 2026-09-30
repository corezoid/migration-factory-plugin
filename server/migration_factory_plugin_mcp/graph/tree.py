"""Builds the addressable tree out of a layer's flat actors+edges: parent
links from edges (first edge into a node wins, dangling/self edges dropped),
siblings ordered top-to-bottom then left-to-right by canvas position, and a
path per node using the same discriminator rule graph.values.yaml documents
(``Title``, or ``Title@<hex>`` for same-titled siblings under the same
parent — never positional numbering, so a node's discriminator doesn't shift
just because a sibling moved on the canvas).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .paths import DISCRIMINATOR, PATH_SEP, PathEntry, PathIndex


@dataclass
class Position:
    x: int = 0
    y: int = 0


@dataclass
class Actor:
    id: str
    title: str = ""
    description: str = ""
    form_id: int = 0
    form_title: str = ""
    picture: str = ""
    position: Position = field(default_factory=Position)


@dataclass
class Edge:
    source: str
    target: str


@dataclass
class File:
    layer_id: str
    actors: list[Actor] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


@dataclass
class Node:
    id: str
    label: str
    path: str
    actor: Actor
    parent_id: Optional[str] = None


@dataclass
class Tree:
    layer_id: str
    nodes: dict  # id -> Node, in insertion (walk) order (Python 3.7+ dicts preserve it)
    order: list  # walk-order list of ids, same order as nodes but explicit
    roots: list
    children_of: dict  # id -> list[id], sorted sibling order

    def children(self, node_id: Optional[str]) -> list:
        return self.children_of.get(node_id, [])


def _uuid_prefixes(ids: list) -> dict:
    """Shortest hex-prefix length (starting at 4) that disambiguates every id
    in the group — never positional numbering."""
    no_hyphen = {i: i.replace("-", "") for i in ids}
    length = 4
    while True:
        prefixes = {i: no_hyphen[i][:length] for i in ids}
        if len(set(prefixes.values())) == len(ids) or length >= 32:
            return prefixes
        length += 1


def build_tree(file: File) -> tuple[Tree, list]:
    actor_by_id = {a.id: a for a in file.actors}
    order_index = {a.id: i for i, a in enumerate(file.actors)}
    parent: dict = {}
    children: dict = defaultdict(list)
    warnings: list = []

    for e in file.edges:
        if e.source not in actor_by_id or e.target not in actor_by_id:
            warnings.append(f"edge {e.source} -> {e.target}: dangling endpoint, dropped")
            continue
        if e.source == e.target:
            warnings.append(f"actor {e.source}: self-edge, dropped")
            continue
        if e.target in parent:
            warnings.append(
                f"actor {e.target}: already has a parent ({parent[e.target]}); "
                f"second edge from {e.source} dropped"
            )
            continue
        parent[e.target] = e.source
        children[e.source].append(e.target)

    def sib_key(node_id: str):
        pos = actor_by_id[node_id].position
        return (pos.y, pos.x, order_index[node_id])

    for src in list(children.keys()):
        children[src].sort(key=sib_key)

    roots = sorted((a.id for a in file.actors if a.id not in parent), key=sib_key)
    if len(roots) > 1:
        warnings.append("layer is a forest, not a single tree")

    nodes: dict = {}
    walk_order: list = []
    by_path: dict = {}
    visited: set = set()

    def label_siblings(ids: list) -> dict:
        groups: dict = defaultdict(list)
        for nid in ids:
            groups[actor_by_id[nid].title].append(nid)
        labels: dict = {}
        for title, group_ids in groups.items():
            if len(group_ids) == 1:
                labels[group_ids[0]] = title
            else:
                prefixes = _uuid_prefixes(group_ids)
                for nid in group_ids:
                    labels[nid] = f"{title}{DISCRIMINATOR}{prefixes[nid]}"
        return labels

    def walk_children(child_ids: list, parent_path: str, parent_id: Optional[str]) -> None:
        if not child_ids:
            return
        labels = label_siblings(child_ids)
        for cid in child_ids:
            if cid in visited:
                warnings.append(f"actor {cid}: reached twice — a cycle in the edges, second reach dropped")
                continue
            visited.add(cid)
            label = labels[cid]
            path = label if not parent_path else parent_path + PATH_SEP + label
            if path in by_path:
                warnings.append(f"path collision at {path!r}: disambiguated with the full id")
                path = f"{path}{DISCRIMINATOR}{cid}"
            by_path[path] = cid
            nodes[cid] = Node(id=cid, label=label, path=path, actor=actor_by_id[cid], parent_id=parent_id)
            walk_order.append(cid)
            walk_children(children.get(cid, []), path, cid)

    walk_children(roots, "", None)

    unvisited = [a.id for a in file.actors if a.id not in visited]
    if unvisited:
        promoted = sorted(unvisited, key=sib_key)
        warnings.append(
            f"{len(promoted)} actor(s) unreachable from any root (a cycle in the edges) "
            "promoted to roots: " + ", ".join(promoted)
        )
        walk_children(promoted, "", None)

    tree = Tree(
        layer_id=file.layer_id,
        nodes=nodes,
        order=walk_order,
        roots=roots,
        children_of=dict(children),
    )
    return tree, warnings


def path_index_from_tree(tree: Tree) -> PathIndex:
    return PathIndex.build([PathEntry(path=tree.nodes[nid].path, id=nid) for nid in tree.order])
