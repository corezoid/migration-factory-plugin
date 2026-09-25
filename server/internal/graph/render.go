package graph

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// The names of the generated files.
const (
	// IDsFileName is the path -> uuid sidecar. It consists entirely of tokens
	// a model cannot use — keep it out of context.
	IDsFileName = "graph.ids.json"
	// TypesFileName is the type dictionary behind the slugs in the tree.
	TypesFileName = "types.schema.yaml"
)

// indent is the leading whitespace of a tree line: two spaces for the block
// scalar itself, then two per level.
func indent(depth int) string { return "  " + strings.Repeat("  ", depth) }

// treeLine renders one node: its label, its type, and the node's own
// description when it has one. The values follow underneath.
func treeLine(n *Node, types *TypeSet) string {
	line := n.Label + " [" + types.Slug(n.Actor.FormID) + "]"
	if desc := oneLine(n.Actor.Description); desc != "" {
		line += "  # " + desc
	}
	return line
}

// formatValue renders one field value on a single line. Nothing is truncated:
// the tree is the only place the layer's data lives now, and a value cut in
// half is worse than a long line. Newlines are collapsed because the line
// grammar is what makes the block readable.
func formatValue(v any) string {
	var s string
	switch val := v.(type) {
	case nil:
		return ""
	case string:
		s = val
	case bool:
		s = strconv.FormatBool(val)
	case float64:
		s = strconv.FormatFloat(val, 'f', -1, 64)
	case int:
		s = strconv.Itoa(val)
	case map[string]any:
		s = labelOf(val)
	case []any:
		var parts []string
		for _, item := range val {
			if p := formatValue(item); p != "" {
				parts = append(parts, p)
			}
		}
		s = strings.Join(parts, ", ")
	default:
		raw, err := json.Marshal(val)
		if err != nil {
			return ""
		}
		s = string(raw)
	}

	return oneLine(s)
}

// labelOf renders a structured value the way the UI shows it. A select stores
// its choices as {title, value} objects and a reference as {title, id}; the
// title is the part a reader recognises, and the raw JSON is noise on a line
// meant to be skimmed.
func labelOf(m map[string]any) string {
	for _, key := range []string{"title", "name", "label", "value"} {
		if s, ok := m[key].(string); ok && strings.TrimSpace(s) != "" {
			return s
		}
	}
	raw, err := json.Marshal(m)
	if err != nil {
		return ""
	}
	return string(raw)
}

// oneLine collapses whitespace so a value or description cannot break the
// line grammar.
func oneLine(s string) string { return strings.Join(strings.Fields(s), " ") }

// RenderIDs renders graph.ids.json — the only artifact holding uuids.
// Paths are written in tree order, so a diff between two exports reads like
// the tree rather than like a shuffled map.
func RenderIDs(tree *Tree) ([]byte, error) {
	var b strings.Builder
	b.WriteString("{\n")
	layer, err := jsonString(tree.LayerID)
	if err != nil {
		return nil, err
	}
	fmt.Fprintf(&b, " \"layer\": %s,\n", layer)
	fmt.Fprintf(&b, " \"sep\": %q,\n", PathSep)
	b.WriteString(" \"paths\": {\n")
	for i, n := range tree.Nodes {
		key, err := jsonString(n.Path)
		if err != nil {
			return nil, err
		}
		sep := ",\n"
		if i == len(tree.Nodes)-1 {
			sep = "\n"
		}
		fmt.Fprintf(&b, "  %s: %q%s", key, n.Actor.ID, sep)
	}
	b.WriteString(" }\n}\n")
	return []byte(b.String()), nil
}

// jsonString encodes one string as a JSON literal without HTML escaping —
// every path here contains the ">" of the separator, and \u003e would make the
// file unreadable for the humans who diff it.
func jsonString(s string) (string, error) {
	var b strings.Builder
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(s); err != nil {
		return "", fmt.Errorf("graph: encode %q: %w", s, err)
	}
	return strings.TrimRight(b.String(), "\n"), nil
}

// ParseIDs reads back what RenderIDs wrote: the layer id and its path -> uuid
// map, in file order.
//
// Paths are unique by construction — BuildTree gives same-titled siblings a
// "@<hex>" discriminator — so this map is the whole of what addressing needs,
// and an ops file written against an export resolves against the same snapshot
// its author was looking at.
func ParseIDs(data []byte) (string, *PathIndex, error) {
	// A plain map would lose the file's order, and the order is the tree's:
	// it is what makes an ambiguity list read top-down like the layer.
	var doc struct {
		Layer string          `json:"layer"`
		Sep   string          `json:"sep"`
		Paths json.RawMessage `json:"paths"`
	}
	if err := json.Unmarshal(data, &doc); err != nil {
		return "", nil, fmt.Errorf("graph: parse %s: %w", IDsFileName, err)
	}
	if doc.Layer == "" {
		return "", nil, fmt.Errorf("graph: %s names no layer", IDsFileName)
	}
	if doc.Sep != "" && doc.Sep != PathSep {
		return "", nil, fmt.Errorf("graph: %s uses separator %q, this build addresses with %q",
			IDsFileName, doc.Sep, PathSep)
	}

	entries, err := orderedPairs(doc.Paths)
	if err != nil {
		return "", nil, fmt.Errorf("graph: parse %s: %w", IDsFileName, err)
	}
	if len(entries) == 0 {
		return "", nil, fmt.Errorf("graph: %s holds no paths", IDsFileName)
	}
	return doc.Layer, newPathIndex(entries), nil
}

// orderedPairs decodes a JSON object as a list of key/value pairs, keeping the
// order they appear in.
func orderedPairs(raw json.RawMessage) ([]PathEntry, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	if tok, err := dec.Token(); err != nil {
		return nil, err
	} else if tok != json.Delim('{') {
		return nil, fmt.Errorf("paths is %v, want an object", tok)
	}

	var entries []PathEntry
	for dec.More() {
		key, err := dec.Token()
		if err != nil {
			return nil, err
		}
		var id string
		if err := dec.Decode(&id); err != nil {
			return nil, err
		}
		path, ok := key.(string)
		if !ok {
			return nil, fmt.Errorf("path key %v is not a string", key)
		}
		entries = append(entries, PathEntry{Path: path, ID: id})
	}
	return entries, nil
}

// LoadIDs reads the path index from a directory holding an export, reporting
// whether the file is there at all.
func LoadIDs(dir string) (string, *PathIndex, bool, error) {
	data, err := os.ReadFile(filepath.Join(dirOrCwd(dir), IDsFileName))
	if os.IsNotExist(err) {
		return "", nil, false, nil
	}
	if err != nil {
		return "", nil, false, fmt.Errorf("graph: read %s: %w", IDsFileName, err)
	}
	layerID, idx, err := ParseIDs(data)
	return layerID, idx, true, err
}
