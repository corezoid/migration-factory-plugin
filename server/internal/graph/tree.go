package graph

import (
	"fmt"
	"sort"
	"strings"
)

// PathSep joins the titles of a node's ancestors into its address. It is
// " > " and not "/" because titles contain slashes ("CORPORATION / HOLDING",
// "Sales / Business Development") and a slash separator would split those
// paths silently.
const PathSep = " > "

// discriminator marks the uuid prefix appended to a label when a sibling
// carries the same title.
const discriminator = "@"

// Tree is a layer as a hierarchy — the shape graph.values.yaml renders. The
// layer arrives as a flat actor list plus edges; the indentation in the
// exported file is the only place the hierarchy survives, so it is built once
// here and every artifact projects from it.
type Tree struct {
	LayerID string
	Roots   []*Node
	// Nodes is every node in walk order: depth-first from each root, siblings
	// ordered by canvas position, which is the order the viewer shows.
	Nodes []*Node
	// ByPath addresses a node by its full path.
	ByPath map[string]*Node
}

// Node is one actor in the tree.
type Node struct {
	Actor Actor
	// Label is the actor's title, plus "@<hex>" when a sibling shares it.
	Label string
	// Path is Label joined to the ancestors' labels with PathSep.
	Path     string
	Depth    int
	Children []*Node
}

// ID is the actor's UUID.
func (n *Node) ID() string { return n.Actor.ID }

// BuildTree arranges a pulled layer into a tree. It returns warnings rather
// than failing on a layer that is not a clean tree — a node with two parents,
// several roots, an edge pointing at nothing, a cycle — because a half-built
// twin is exactly when the map is most useful. Each anomaly is resolved
// deterministically so two exports of an unchanged layer are identical.
func BuildTree(f File) (*Tree, []string, error) {
	if f.LayerID == "" {
		return nil, nil, fmt.Errorf("graph: BuildTree needs a layer id")
	}

	actors := make(map[string]Actor, len(f.Actors))
	order := make(map[string]int, len(f.Actors))
	for i, a := range f.Actors {
		if _, dup := actors[a.ID]; dup {
			return nil, nil, fmt.Errorf("graph: actor %s appears twice in the layer", a.ID)
		}
		actors[a.ID] = a
		order[a.ID] = i
	}

	var warnings []string
	warn := func(format string, args ...any) {
		warnings = append(warnings, fmt.Sprintf(format, args...))
	}

	children := make(map[string][]string, len(f.Actors))
	parent := make(map[string]string, len(f.Actors))
	for _, e := range f.Edges {
		if _, ok := actors[e.Source]; !ok {
			warn("edge from unknown actor %s dropped", e.Source)
			continue
		}
		if _, ok := actors[e.Target]; !ok {
			warn("edge to unknown actor %s dropped", e.Target)
			continue
		}
		if e.Source == e.Target {
			warn("self-edge on %s (%s) dropped", e.Target, actors[e.Target].Title)
			continue
		}
		if first, taken := parent[e.Target]; taken {
			warn("%s (%s) has two parents: kept %s, dropped %s",
				e.Target, actors[e.Target].Title, first, e.Source)
			continue
		}
		parent[e.Target] = e.Source
		children[e.Source] = append(children[e.Source], e.Target)
	}

	// Siblings follow the canvas: top to bottom, then left to right, with the
	// layer's own order breaking a tie so the output never depends on map
	// iteration.
	sortIDs := func(ids []string) {
		sort.SliceStable(ids, func(i, j int) bool {
			a, b := actors[ids[i]], actors[ids[j]]
			switch {
			case a.Position.Y != b.Position.Y:
				return a.Position.Y < b.Position.Y
			case a.Position.X != b.Position.X:
				return a.Position.X < b.Position.X
			default:
				return order[ids[i]] < order[ids[j]]
			}
		})
	}
	for _, kids := range children {
		sortIDs(kids)
	}

	var roots []string
	for _, a := range f.Actors {
		if _, hasParent := parent[a.ID]; !hasParent {
			roots = append(roots, a.ID)
		}
	}
	sortIDs(roots)
	if len(roots) > 1 {
		warn("%d root nodes — the layer is a forest, not a single tree", len(roots))
	}

	tree := &Tree{LayerID: f.LayerID, ByPath: make(map[string]*Node, len(f.Actors))}
	visited := make(map[string]bool, len(f.Actors))

	// labelSiblings resolves same-titled siblings once per parent, before any
	// of them is walked, since a label depends on the whole group.
	labelSiblings := func(ids []string) map[string]string {
		groups := make(map[string][]string, len(ids))
		for _, id := range ids {
			groups[actors[id].Title] = append(groups[actors[id].Title], id)
		}
		labels := make(map[string]string, len(ids))
		for _, id := range ids {
			group := groups[actors[id].Title]
			if len(group) == 1 {
				labels[id] = actors[id].Title
				continue
			}
			if strings.Contains(actors[id].Title, discriminator) {
				warn("title %q already contains %q — the discriminator may read ambiguously",
					actors[id].Title, discriminator)
			}
			labels[id] = actors[id].Title + discriminator + uuidPrefixes(group)[id]
		}
		return labels
	}

	var walk func(id, prefix string, depth int, parentNode *Node, label string)
	walk = func(id, prefix string, depth int, parentNode *Node, label string) {
		if visited[id] {
			warn("%s (%s) reached twice — cycle broken", id, actors[id].Title)
			return
		}
		visited[id] = true

		path := prefix + label
		if _, clash := tree.ByPath[path]; clash {
			// Unreachable for a well-formed layer: labels are unique per
			// parent, so paths are unique by construction. Keep the fallback
			// rather than overwrite a node and lose it from the index.
			path += discriminator + strings.ReplaceAll(id, "-", "")
			warn("path collision resolved with the full uuid: %s", path)
		}

		node := &Node{Actor: actors[id], Label: label, Path: path, Depth: depth}
		tree.ByPath[path] = node
		tree.Nodes = append(tree.Nodes, node)
		if parentNode == nil {
			tree.Roots = append(tree.Roots, node)
		} else {
			parentNode.Children = append(parentNode.Children, node)
		}

		kids := children[id]
		labels := labelSiblings(kids)
		for _, kid := range kids {
			walk(kid, path+PathSep, depth+1, node, labels[kid])
		}
	}

	rootLabels := labelSiblings(roots)
	for _, id := range roots {
		walk(id, "", 0, nil, rootLabels[id])
	}

	// Anything still unvisited sits in a cycle with no way in. Promote them,
	// in canvas order, so no actor is missing from the map.
	if len(visited) < len(f.Actors) {
		var orphans []string
		for _, a := range f.Actors {
			if !visited[a.ID] {
				orphans = append(orphans, a.ID)
			}
		}
		sortIDs(orphans)
		warn("%d actors are only reachable through a cycle — promoted to roots", len(orphans))
		labels := labelSiblings(orphans)
		for _, id := range orphans {
			if !visited[id] {
				walk(id, "", 0, nil, labels[id])
			}
		}
	}

	return tree, warnings, nil
}

// uuidPrefixes returns the shortest uuid prefix (at least 4 hex characters)
// that tells a group of same-titled siblings apart.
//
// Positional numbering ("~2", "~3") would be shorter but depends on sort
// order: moving a node on the canvas would silently reassign identities
// between two exports. A uuid prefix is stable against layout.
func uuidPrefixes(ids []string) map[string]string {
	hex := make(map[string]string, len(ids))
	for _, id := range ids {
		hex[id] = strings.ReplaceAll(id, "-", "")
	}
	for n := 4; n <= 32; n++ {
		seen := make(map[string]bool, len(ids))
		out := make(map[string]string, len(ids))
		collision := false
		for _, id := range ids {
			p := hex[id]
			if n < len(p) {
				p = p[:n]
			}
			if seen[p] {
				collision = true
				break
			}
			seen[p] = true
			out[id] = p
		}
		if !collision {
			return out
		}
	}
	// Identical uuids cannot happen — the actor map is keyed by them.
	return hex
}
