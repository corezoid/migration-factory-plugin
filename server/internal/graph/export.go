package graph

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"migration-factory-plugin-mcp/internal/simulator"
)

// ExportOptions configure ExportLayer.
type ExportOptions struct {
	// LayerID is the layer to export. Required.
	LayerID string
	// Dir is where the files land, created when it does not exist yet; empty
	// means the current directory.
	Dir string
	// Concurrency caps the parallel form and actor reads. Zero uses 8.
	Concurrency int
}

// ExportResult reports what an export wrote and what it had to work around.
type ExportResult struct {
	LayerID string
	// The generated files.
	ValuesPath string
	IDsPath    string
	TypesPath  string

	Nodes           int
	Edges           int
	Types           int
	NodesWithValues int
	// Warnings are the anomalies the export resolved rather than failed on: a
	// layer that is not a tree, a form that could not be read, an actor whose
	// values are missing. An export with warnings is still usable — the
	// warnings say which parts of it to distrust.
	Warnings []string
}

// ExportLayer projects a Simulator layer into the three files the graph
// tooling reads:
//
//	graph.values.yaml   the layer: the tree, with every field value under its node
//	graph.ids.json      path -> uuid, the only place uuids survive
//	types.schema.yaml   the type dictionary the slugs in the tree resolve to
//
// The split is about what belongs in front of a reader. The raw layer is
// mostly uuids, canvas coordinates and colors — bytes a model cannot reason
// about and will copy incorrectly. graph.values.yaml keeps the hierarchy, the
// type of every node and everything the node actually holds, and is addressed
// by path; the uuids stay in the sidecar and the Simulator field ids in the
// schema, where the write path resolves them.
//
// It reads the layer, then one form per type and one actor per node, all
// concurrently: the layer route serves neither field schemas nor real values.
func ExportLayer(ctx context.Context, sim *simulator.Client, opts ExportOptions) (*ExportResult, error) {
	layer, err := PullGraph(ctx, sim, opts.LayerID)
	if err != nil {
		return nil, err
	}

	tree, warnings, err := BuildTree(*layer)
	if err != nil {
		return nil, err
	}
	if len(tree.Nodes) == 0 {
		// An export of nothing is not harmless: it writes `paths: {}` and
		// `types: {}`, and every apply and listing in that directory then
		// fails blaming the files. The usual cause is a wrong layer id or a
		// key for the wrong workspace, and that is what the reader needs to
		// hear.
		return nil, fmt.Errorf("graph: layer %s has no nodes — nothing to export; check the layer id "+
			"and that the API key belongs to its workspace", opts.LayerID)
	}

	types, typeWarnings, err := ResolveTypes(ctx, sim, layer.Actors, opts.Concurrency)
	if err != nil {
		return nil, err
	}
	warnings = append(warnings, typeWarnings...)

	actorIDs := make([]string, 0, len(tree.Nodes))
	for _, n := range tree.Nodes {
		actorIDs = append(actorIDs, n.Actor.ID)
	}
	values, valueWarnings, err := FetchValues(ctx, sim, actorIDs, opts.Concurrency)
	if err != nil {
		return nil, err
	}
	warnings = append(warnings, valueWarnings...)

	ids, err := RenderIDs(tree)
	if err != nil {
		return nil, err
	}
	schema, err := RenderTypesSchema(tree.LayerID, types)
	if err != nil {
		return nil, err
	}

	res := &ExportResult{
		LayerID:         opts.LayerID,
		ValuesPath:      filepath.Join(dirOrCwd(opts.Dir), ValuesFileName),
		IDsPath:         filepath.Join(dirOrCwd(opts.Dir), IDsFileName),
		TypesPath:       filepath.Join(dirOrCwd(opts.Dir), TypesFileName),
		Nodes:           len(tree.Nodes),
		Edges:           len(layer.Edges),
		Types:           len(types.Types),
		NodesWithValues: len(values),
		Warnings:        warnings,
	}

	if err := os.MkdirAll(dirOrCwd(opts.Dir), 0o750); err != nil {
		return nil, fmt.Errorf("graph: create %s: %w", dirOrCwd(opts.Dir), err)
	}

	if err := writeFile(res.ValuesPath, RenderValues(tree, types, values)); err != nil {
		return nil, err
	}
	if err := writeFile(res.IDsPath, ids); err != nil {
		return nil, err
	}
	if err := writeFile(res.TypesPath, schema); err != nil {
		return nil, err
	}
	return res, nil
}

func writeFile(path string, data []byte) error {
	if err := os.WriteFile(path, data, 0o600); err != nil {
		return fmt.Errorf("graph: write %s: %w", path, err)
	}
	return nil
}

func dirOrCwd(dir string) string {
	if dir == "" {
		return "."
	}
	return dir
}
