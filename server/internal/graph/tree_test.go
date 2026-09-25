package graph

import (
	"strings"
	"testing"
)

// node is a compact literal for the trees these tests build.
func node(id, title string, formID, x, y int) Actor {
	return Actor{ID: id, Title: title, FormID: formID, Position: Position{X: x, Y: y}}
}

func edge(source, target string) Edge { return Edge{Source: source, Target: target} }

const (
	uuidA = "aaaaaaaa-1111-4111-8111-111111111111"
	uuidB = "bbbbbbbb-2222-4222-8222-222222222222"
	uuidC = "cccccccc-3333-4333-8333-333333333333"
	uuidD = "dddddddd-4444-4444-8444-444444444444"
)

func TestBuildTreeOrdersSiblingsByCanvasPosition(t *testing.T) {
	f := File{
		LayerID: testLayerID,
		Actors: []Actor{
			node(uuidA, "Root", 1, 0, 0),
			node(uuidB, "Lower", 1, 0, 200),
			node(uuidC, "Upper", 1, 50, 100),
			node(uuidD, "Upper left", 1, 10, 100),
		},
		Edges: []Edge{edge(uuidA, uuidB), edge(uuidA, uuidC), edge(uuidA, uuidD)},
	}

	tree, warnings, err := BuildTree(f)
	if err != nil {
		t.Fatalf("BuildTree: %v", err)
	}
	if len(warnings) != 0 {
		t.Errorf("warnings = %v, want none", warnings)
	}

	var got []string
	for _, n := range tree.Nodes {
		got = append(got, n.Label)
	}
	want := []string{"Root", "Upper left", "Upper", "Lower"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("walk order = %v, want %v (y first, then x)", got, want)
	}
	if p := tree.Nodes[1].Path; p != "Root > Upper left" {
		t.Errorf("path = %q, want %q", p, "Root > Upper left")
	}
	if tree.ByPath["Root > Lower"].ID() != uuidB {
		t.Error("ByPath does not resolve to the actor")
	}
}

func TestBuildTreeDisambiguatesSameTitledSiblings(t *testing.T) {
	f := File{
		LayerID: testLayerID,
		Actors: []Actor{
			node(uuidA, "Docs", 1, 0, 0),
			node(uuidB, "Document #1", 2, 0, 10),
			node(uuidC, "Document #1", 2, 0, 20),
		},
		Edges: []Edge{edge(uuidA, uuidB), edge(uuidA, uuidC)},
	}

	tree, _, err := BuildTree(f)
	if err != nil {
		t.Fatalf("BuildTree: %v", err)
	}

	for _, want := range []string{"Docs > Document #1@bbbb", "Docs > Document #1@cccc"} {
		if _, ok := tree.ByPath[want]; !ok {
			t.Errorf("missing path %q; got %v", want, paths(tree))
		}
	}
}

func TestBuildTreeKeepsUniqueTitlesUndecorated(t *testing.T) {
	f := File{
		LayerID: testLayerID,
		Actors:  []Actor{node(uuidA, "Docs", 1, 0, 0), node(uuidB, "Document #1", 2, 0, 10)},
		Edges:   []Edge{edge(uuidA, uuidB)},
	}

	tree, _, err := BuildTree(f)
	if err != nil {
		t.Fatalf("BuildTree: %v", err)
	}
	if _, ok := tree.ByPath["Docs > Document #1"]; !ok {
		t.Errorf("paths = %v, want an undecorated label", paths(tree))
	}
}

func TestBuildTreeWarnsInsteadOfFailingOnABrokenLayer(t *testing.T) {
	f := File{
		LayerID: testLayerID,
		Actors: []Actor{
			node(uuidA, "Root", 1, 0, 0),
			node(uuidB, "Child", 1, 0, 10),
			node(uuidC, "Second root", 1, 0, 20),
		},
		Edges: []Edge{
			edge(uuidA, uuidB),
			edge(uuidC, uuidB),           // second parent for B
			edge(uuidA, "missing-actor"), // dangling target
			edge("missing-actor", uuidA), // dangling source
			edge(uuidC, uuidC),           // self-edge
		},
	}

	tree, warnings, err := BuildTree(f)
	if err != nil {
		t.Fatalf("BuildTree: %v", err)
	}
	if len(tree.Nodes) != 3 {
		t.Errorf("nodes = %d, want all 3 actors in the map", len(tree.Nodes))
	}

	joined := strings.Join(warnings, "\n")
	for _, want := range []string{"two parents", "unknown actor", "self-edge", "root nodes"} {
		if !strings.Contains(joined, want) {
			t.Errorf("warnings are missing %q:\n%s", want, joined)
		}
	}
	if _, ok := tree.ByPath["Root > Child"]; !ok {
		t.Errorf("the first parent did not win: %v", paths(tree))
	}
}

func TestBuildTreePromotesNodesTrappedInACycle(t *testing.T) {
	f := File{
		LayerID: testLayerID,
		Actors:  []Actor{node(uuidA, "Root", 1, 0, 0), node(uuidB, "B", 1, 0, 10), node(uuidC, "C", 1, 0, 20)},
		// B -> C -> B is a cycle with no way in from Root.
		Edges: []Edge{edge(uuidB, uuidC), edge(uuidC, uuidB)},
	}

	tree, warnings, err := BuildTree(f)
	if err != nil {
		t.Fatalf("BuildTree: %v", err)
	}
	if len(tree.Nodes) != 3 {
		t.Errorf("nodes = %d, want every actor mapped; got %v", len(tree.Nodes), paths(tree))
	}
	if !strings.Contains(strings.Join(warnings, "\n"), "cycle") {
		t.Errorf("warnings = %v, want the cycle reported", warnings)
	}
}

func TestBuildTreeRejectsADuplicateActor(t *testing.T) {
	f := File{LayerID: testLayerID, Actors: []Actor{node(uuidA, "One", 1, 0, 0), node(uuidA, "Again", 1, 0, 1)}}
	if _, _, err := BuildTree(f); err == nil {
		t.Fatal("BuildTree accepted the same actor twice")
	}
}

func TestUUIDPrefixesWidenOnCollision(t *testing.T) {
	// Same first 4 hex characters, different fifth.
	ids := []string{"abcd1111-0000-4000-8000-000000000000", "abcd2222-0000-4000-8000-000000000000"}
	got := uuidPrefixes(ids)
	if len(got[ids[0]]) != 5 || got[ids[0]] == got[ids[1]] {
		t.Errorf("prefixes = %v, want 5 distinct hex characters", got)
	}
}

func paths(tree *Tree) []string {
	var out []string
	for _, n := range tree.Nodes {
		out = append(out, n.Path)
	}
	return out
}
