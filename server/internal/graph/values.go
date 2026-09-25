package graph

import (
	"context"
	"fmt"
	"strings"

	"migration-factory-plugin-mcp/internal/simulator"
)

// Values holds the field values of a layer's actors: actor uuid -> field id
// -> value. Empty values are dropped, so a node missing from the map simply
// has nothing filled in.
type Values map[string]map[string]any

// FetchValues reads the values of every actor in the list, concurrently.
//
// They do not come with the layer: the paginated layer route serves a trimmed
// projection of `data` (one key per node, all null), so the values behind a
// node need the actor route. That is one read per node — the reason an export
// is concurrent at all.
func FetchValues(ctx context.Context, sim *simulator.Client, actorIDs []string, concurrency int) (Values, []string, error) {
	if sim == nil {
		return nil, nil, fmt.Errorf("graph: FetchValues needs a simulator client")
	}

	actors := make([]*simulator.Actor, len(actorIDs))
	errs := make([]error, len(actorIDs))
	parallel(len(actorIDs), concurrency, func(i int) {
		actors[i], errs[i] = sim.GetActorSummary(ctx, actorIDs[i])
	})

	var warnings []string
	values := make(Values, len(actorIDs))
	for i, id := range actorIDs {
		if errs[i] != nil {
			warnings = append(warnings, fmt.Sprintf("actor %s: %v — node rendered without values", id, errs[i]))
			continue
		}
		if vals := cleanValues(actors[i].Data); len(vals) > 0 {
			values[id] = vals
		}
	}
	return values, warnings, nil
}

// cleanValues drops the empty entries and normalises the keys: a multiform
// actor stores another form's fields under "__form__<formId>:<fieldId>", and
// the field id is what the schema names.
func cleanValues(data map[string]any) map[string]any {
	out := make(map[string]any, len(data))
	for key, value := range data {
		if isEmptyValue(value) {
			continue
		}
		if rest, ok := strings.CutPrefix(key, "__form__"); ok {
			if i := strings.Index(rest, ":"); i > 0 {
				key = rest[i+1:]
			}
		}
		out[key] = value
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

func isEmptyValue(v any) bool {
	switch val := v.(type) {
	case nil:
		return true
	case string:
		return strings.TrimSpace(val) == ""
	case []any:
		return len(val) == 0
	case map[string]any:
		return len(val) == 0
	default:
		return false
	}
}
