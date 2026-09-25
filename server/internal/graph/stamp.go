package graph

import (
	"fmt"
	"os"
	"slices"
	"strings"

	"gopkg.in/yaml.v3"
)

// StampOps writes the node uuid each op resolved to back into the ops file,
// into the ops that do not carry one yet.
//
// An apply does this before it writes anything, and that order is the whole
// point. `rename:` changes a node's title, the title is the last segment of
// its path, and so an op that renamed successfully has an `at:` addressing a
// node that no longer answers to that name — replaying the file would fail on
// exactly the ops that worked. The uuid does not move, and from the second
// run on it is what the op is addressed by.
//
// It edits the file by line rather than re-encoding it. A round trip through
// the YAML parser keeps the comments but drops the blank lines between ops,
// and an ops file is something a person reads and re-reads next to the plan;
// inserting one line per op at a position the parser reported leaves every
// other byte exactly where the author left it.
//
// It returns how many ops it stamped, and a warning for each one it could not
// place — those stay unstamped, which costs replayability for that op and
// nothing else.
func StampOps(path string, resolved map[int]string) (int, []string, error) {
	if len(resolved) == 0 {
		return 0, nil, nil
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		return 0, nil, fmt.Errorf("graph: stamp ops: %w", err)
	}
	info, err := os.Stat(path)
	if err != nil {
		return 0, nil, fmt.Errorf("graph: stamp ops: %w", err)
	}

	var doc yaml.Node
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		return 0, nil, fmt.Errorf("graph: stamp ops: %w", err)
	}
	seq := opsSequence(&doc)
	if seq == nil {
		return 0, nil, fmt.Errorf("graph: stamp ops: %s has no `ops:` list", path)
	}

	lines := strings.Split(string(raw), "\n")
	var (
		inserts  []insertion
		warnings []string
	)
	for i, opNode := range seq.Content {
		n := i + 1
		id := resolved[n]
		if id == "" || mappingValue(opNode, "id") != nil {
			continue
		}
		at, line := stampAnchor(opNode, lines)
		if at == nil {
			warnings = append(warnings, fmt.Sprintf(
				"op #%d keeps no `id:` — its `at:` is not a plain one-line address, so there is "+
					"nowhere obvious to put one; a replay after a rename will not find the node", n))
			continue
		}
		inserts = append(inserts, insertion{
			after: line,
			text:  strings.Repeat(" ", at.Column-1) + "id: " + id,
		})
	}
	if len(inserts) == 0 {
		return 0, warnings, nil
	}

	// Splice from the bottom up so an earlier insertion cannot move the line
	// a later one was measured against.
	slices.SortFunc(inserts, func(a, b insertion) int { return b.after - a.after })
	for _, ins := range inserts {
		if ins.after < 1 || ins.after > len(lines) {
			return 0, warnings, fmt.Errorf("graph: stamp ops: line %d is outside %s", ins.after, path)
		}
		lines = slices.Insert(lines, ins.after, ins.text)
	}

	if err := os.WriteFile(path, []byte(strings.Join(lines, "\n")), info.Mode().Perm()); err != nil {
		return 0, warnings, fmt.Errorf("graph: stamp ops: %w", err)
	}
	return len(inserts), warnings, nil
}

// insertion is one line to add, after the 1-based line number the parser
// reported for the op's address.
type insertion struct {
	after int
	text  string
}

// opsSequence finds the `ops:` list in a parsed document.
func opsSequence(doc *yaml.Node) *yaml.Node {
	if doc.Kind != yaml.DocumentNode || len(doc.Content) == 0 {
		return nil
	}
	seq := mappingValue(doc.Content[0], "ops")
	if seq == nil || seq.Kind != yaml.SequenceNode {
		return nil
	}
	return seq
}

// mappingValue returns the value node under key, or nil.
func mappingValue(node *yaml.Node, key string) *yaml.Node {
	if node == nil || node.Kind != yaml.MappingNode {
		return nil
	}
	for i := 0; i+1 < len(node.Content); i += 2 {
		if node.Content[i].Value == key {
			return node.Content[i+1]
		}
	}
	return nil
}

// anchorKeys are the keys an id may be written under, in the order they are
// tried: the address of an op, whichever of the two kinds it is. `ref:` comes
// last because a record op carries both it and `type:`, and the id reads
// better directly under the type.
var anchorKeys = []string{"at", "type", "ref"}

// stampAnchor reports the key an id is written under, and the line to write it
// after. It insists the address is a scalar that starts and ends on the key's
// own line, because that is the one shape whose end this can be sure of — a
// folded or wrapped address would have the id land inside it.
//
// The parser reports where a scalar starts and nothing about where it ends,
// so the end is checked against the raw source: lines is the file split by
// line, the same slice the id is spliced into.
func stampAnchor(op *yaml.Node, lines []string) (key *yaml.Node, line int) {
	if op == nil || op.Kind != yaml.MappingNode {
		return nil, 0
	}
	for _, want := range anchorKeys {
		for i := 0; i+1 < len(op.Content); i += 2 {
			k, v := op.Content[i], op.Content[i+1]
			if k.Value != want {
				continue
			}
			if !oneLineScalar(k, v, lines) {
				// A `type:` this shape is not a thing anyone writes, but an
				// `at:` folded over two lines is, and falling through to the
				// next key would stamp the id under a sibling key instead of
				// reporting that there is nowhere to put it.
				return nil, 0
			}
			return k, k.Line
		}
	}
	return nil, 0
}

// oneLineScalar reports whether v is a scalar that both starts and ends on the
// line its key is on.
//
// A block scalar never does. A quoted scalar is walked from its opening quote
// to see that the closing one is on the same line — the line cannot simply be
// cut at a `#`, because "Lead #1" is an address. A plain scalar has no closing
// mark at all, so it ends on this line when the next line of content is not
// indented deeper than the key: a deeper line would be its continuation.
func oneLineScalar(k, v *yaml.Node, lines []string) bool {
	if v.Kind != yaml.ScalarNode || v.Line != k.Line || k.Line < 1 || k.Line > len(lines) {
		return false
	}
	switch v.Style {
	case yaml.LiteralStyle, yaml.FoldedStyle:
		return false
	case yaml.DoubleQuotedStyle:
		return quoteClosesOnLine(lines[k.Line-1], v.Column, '"')
	case yaml.SingleQuotedStyle:
		return quoteClosesOnLine(lines[k.Line-1], v.Column, '\'')
	}
	return !continuesOnNextLine(lines, k.Line, k.Column)
}

// quoteClosesOnLine reports whether the quoted scalar opening at column (the
// parser's, 1-based, counted in characters) is closed before the line ends. A
// backslash escapes the next character in double quotes; a doubled quote is
// the escape in single quotes.
func quoteClosesOnLine(line string, column int, quote rune) bool {
	runes := []rune(line)
	if column < 1 || column > len(runes) || runes[column-1] != quote {
		return false
	}
	for i := column; i < len(runes); i++ {
		switch runes[i] {
		case '\\':
			if quote == '"' {
				i++
			}
		case quote:
			if quote == '\'' && i+1 < len(runes) && runes[i+1] == '\'' {
				i++
				continue
			}
			return true
		}
	}
	return false
}

// continuesOnNextLine reports whether the first line of content after the key
// (1-based line) is indented past the key's column — the shape of a plain
// scalar wrapping onto a second line. Blank lines and comment lines are
// skipped: a plain scalar may span a blank line, and a comment cannot be part
// of one.
func continuesOnNextLine(lines []string, line, column int) bool {
	for _, next := range lines[line:] {
		trimmed := strings.TrimLeft(next, " \t")
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		return len([]rune(next))-len([]rune(trimmed)) >= column
	}
	return false
}
