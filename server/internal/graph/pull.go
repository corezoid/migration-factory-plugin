// Package graph reads a Simulator graph layer — its actors and the edges
// between them — into the flat shape the export, index, plan and apply tools
// work from. Nothing here writes a layer back as a whole: a change reaches the
// platform one actor at a time, through the plan an ops file produces.
package graph

import (
	"context"
	"fmt"

	"migration-factory-plugin-mcp/internal/simulator"
)

// File is the YAML document: one layer, its actors and the edges between them.
type File struct {
	LayerID string  `yaml:"layerId"`
	Actors  []Actor `yaml:"actors"`
	Edges   []Edge  `yaml:"edges"`
}

// Actor is one node of the layer. ID is the server-assigned UUID.
type Actor struct {
	ID          string `yaml:"id"`
	Title       string `yaml:"title"`
	Description string `yaml:"description,omitempty"`
	FormID      int    `yaml:"formId,omitempty"`
	FormName    string `yaml:"formName,omitempty"`
	// Picture is the storage path of the node's image, empty when it has
	// none. The export lists the nodes that carry one so a run can fill the
	// rest and replace none.
	Picture  string   `yaml:"picture,omitempty"`
	Position Position `yaml:"position"`
}

// Position is an actor's pixel coordinate on the layer canvas.
type Position struct {
	X int `yaml:"x"`
	Y int `yaml:"y"`
}

// Edge is one link of the layer, by the UUIDs of its endpoints.
type Edge struct {
	Source string `yaml:"source"`
	Target string `yaml:"target"`
}

// PullGraph fetches every actor and edge of a layer, for callers that project
// the layer into something else (see ExportLayer).
func PullGraph(ctx context.Context, sim *simulator.Client, layerID string) (*File, error) {
	if sim == nil {
		return nil, fmt.Errorf("graph: pulling a layer needs a simulator client")
	}
	if layerID == "" {
		return nil, fmt.Errorf("graph: pulling a layer needs a layer id")
	}

	serverActors, err := sim.ListLayerActors(ctx, layerID)
	if err != nil {
		return nil, fmt.Errorf("graph: fetch layer actors: %w", err)
	}
	serverEdges, err := sim.ListLayerEdges(ctx, layerID)
	if err != nil {
		return nil, fmt.Errorf("graph: fetch layer edges: %w", err)
	}

	graph := &File{LayerID: layerID}
	for _, sa := range serverActors {
		graph.Actors = append(graph.Actors, Actor{
			ID:          sa.ID,
			Title:       sa.Title,
			Description: sa.Description,
			FormID:      sa.ResolvedFormID(),
			FormName:    sa.FormTitle,
			Picture:     sa.Picture,
			Position:    Position{X: int(sa.Position.X), Y: int(sa.Position.Y)},
		})
	}
	for _, se := range serverEdges {
		graph.Edges = append(graph.Edges, Edge{Source: se.Source, Target: se.Target})
	}
	return graph, nil
}
