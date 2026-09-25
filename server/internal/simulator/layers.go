package simulator

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"strconv"
	"strings"
)

// This file holds the graph-layer read endpoints: the two halves of a layer —
// its nodes (actors placed on the canvas) and its edges (the links between
// them) — both served by the paginated /graph_layers/paginated/{layerId}
// route under a `type` switch.

// layerPageLimit is the page size the layer listings walk with. The route
// pages, and a layer routinely holds more nodes than one page. maxLayerPages
// caps the walk — ten thousand items — so a route that ignores offset and
// serves its first page forever ends in an error rather than a spin.
const (
	layerPageLimit = 50
	maxLayerPages  = 200
)

// Layer listing types — the `type` query parameter of the paginated route.
const (
	layerTypeNodes = "nodes"
	layerTypeEdges = "edges"
)

// LayerCoord is a pixel coordinate on the layer canvas. The backend stores
// some positions as fractions (e.g. -1543.58) and others as integers, so both
// are accepted and a fraction is rounded to the nearest pixel — a plain int
// field would fail the decode with "cannot unmarshal number into int" as soon
// as one node on the layer carried a fractional coordinate.
type LayerCoord int

// UnmarshalJSON accepts an integer or a fractional JSON number.
func (n *LayerCoord) UnmarshalJSON(b []byte) error {
	var f float64
	if err := json.Unmarshal(b, &f); err != nil {
		return err
	}
	*n = LayerCoord(math.Round(f))
	return nil
}

// LayerPosition is a node's coordinate on the layer canvas.
type LayerPosition struct {
	X LayerCoord `json:"x"`
	Y LayerCoord `json:"y"`
}

// LayerActor is one node of a layer: the actor plus its placement.
type LayerActor struct {
	ID          string `json:"id"`
	Title       string `json:"title"`
	Description string `json:"description"`
	FormID      int    `json:"formId"`
	// FormTitle is the name of the node's form, served with the node — the
	// only place a layer read gives one, and enough to name a type without a
	// form lookup.
	FormTitle string         `json:"formTitle"`
	Data      map[string]any `json:"data"`
	// Picture is the image the node is drawn with, as a path in the
	// workspace's storage. Read for one question the export has to answer —
	// which nodes still have a blank face — since a picture is a property of
	// the actor and no form field carries it.
	Picture  string        `json:"picture"`
	Position LayerPosition `json:"position"`
}

// ResolvedFormID returns the form id to use for API calls on this node. In a
// form-tree (UAT) workspace the node's own data is keyed by the leaf form
// ("__form__408962:view"), and that leaf id — not the top-level formId, which
// names the root — is what the actor routes expect.
func (a LayerActor) ResolvedFormID() int { return resolvedFormID(a.Data, a.FormID) }

// resolvedFormID reads the leaf form out of an actor's data keys, falling back
// to the form the record names.
func resolvedFormID(data map[string]any, formID int) int {
	for key := range data {
		if rest, ok := strings.CutPrefix(key, "__form__"); ok {
			if idx := strings.Index(rest, ":"); idx > 0 {
				if id, err := strconv.Atoi(rest[:idx]); err == nil && id > 0 {
					return id
				}
			}
		}
	}
	return formID
}

// LayerEdge is one link of a layer.
type LayerEdge struct {
	ID     string `json:"id"`
	Source string `json:"source"`
	Target string `json:"target"`
}

// ListLayerActors returns every node of a layer, walking the pages of
// GET /graph_layers/paginated/{layerId}?type=nodes.
func (c *Client) ListLayerActors(ctx context.Context, layerID string) ([]LayerActor, error) {
	return listLayerItems[LayerActor](ctx, c, layerID, layerTypeNodes)
}

// ListLayerEdges returns every edge of a layer, walking the pages of
// GET /graph_layers/paginated/{layerId}?type=edges.
func (c *Client) ListLayerEdges(ctx context.Context, layerID string) ([]LayerEdge, error) {
	return listLayerItems[LayerEdge](ctx, c, layerID, layerTypeEdges)
}

// listLayerItems walks the paginated layer route until a short page ends it.
func listLayerItems[T any](ctx context.Context, c *Client, layerID, itemType string) ([]T, error) {
	if err := validateLayerID(layerID); err != nil {
		return nil, err
	}

	var all []T
	for n := 0; n < maxLayerPages; n++ {
		query := url.Values{
			"type":   {itemType},
			"limit":  {strconv.Itoa(layerPageLimit)},
			"offset": {strconv.Itoa(n * layerPageLimit)},
		}
		var page listEnvelope[T]
		if err := c.get(ctx, "/graph_layers/paginated/"+seg(layerID), query, &page); err != nil {
			return nil, err
		}
		all = append(all, page.Data...)
		if len(page.Data) < layerPageLimit {
			return all, nil
		}
	}
	return nil, fmt.Errorf("simulator: layer %s listing did not end after %d pages — the route is not paging", layerID, maxLayerPages)
}

// validateLayerID rejects a malformed layer id before the request goes out. A
// layer is an actor, so it carries the same full UUID — and the same
// misleading 403 when it is shortened.
func validateLayerID(layerID string) error {
	if layerID == "" {
		return fmt.Errorf("simulator: layer id is required")
	}
	if !actorUUIDRe.MatchString(layerID) {
		return fmt.Errorf("simulator: layer id %q is not a full UUID (8-4-4-4-12)", layerID)
	}
	return nil
}
