package graph

import (
	"errors"
	"fmt"
	"strings"
)

// ErrNoMatch is what an address that names no node fails with. A caller that
// has a second address to try — an op whose node may already carry the title
// it renames to — tells this apart from an ambiguity, which must never be
// retried with a guess.
var ErrNoMatch = errors.New("no node matches")

// PathEntry is one addressable node: its path and the uuid behind it.
type PathEntry struct {
	Path string
	ID   string
}

// PathIndex is what an ops file addresses: every path of a layer, in tree
// order, and the uuid each one names.
//
// It comes either from a live read of the layer or from the graph.ids.json an
// export wrote. Those are the same map — paths are unique by construction, so
// the file is a faithful snapshot rather than a lossy one — and resolving
// against the snapshot the ops were written against is what makes an address
// mean the node its author was looking at.
type PathIndex struct {
	entries []PathEntry
	byPath  map[string]string
	byID    map[string]string
}

func newPathIndex(entries []PathEntry) *PathIndex {
	idx := &PathIndex{
		entries: entries,
		byPath:  make(map[string]string, len(entries)),
		byID:    make(map[string]string, len(entries)),
	}
	for _, e := range entries {
		idx.byPath[e.Path] = e.ID
		idx.byID[e.ID] = e.Path
	}
	return idx
}

// ByID returns the node with this uuid. It is the addressing an op uses once
// it has been stamped: a uuid survives the renames and re-parentings that
// invalidate a path, so the lookup either finds the node or the node is gone
// from the layer — there is no near miss to guess at.
func (p *PathIndex) ByID(id string) (PathEntry, error) {
	id = strings.TrimSpace(id)
	if id == "" {
		return PathEntry{}, fmt.Errorf("empty node id")
	}
	path, ok := p.byID[id]
	if !ok {
		return PathEntry{}, fmt.Errorf("%w with id %s — it is not on this layer "+
			"(deleted, or the id belongs to another layer)", ErrNoMatch, id)
	}
	return PathEntry{Path: path, ID: id}, nil
}

// PathIndexFromTree builds the index from a layer read.
func PathIndexFromTree(t *Tree) *PathIndex {
	entries := make([]PathEntry, 0, len(t.Nodes))
	for _, n := range t.Nodes {
		entries = append(entries, PathEntry{Path: n.Path, ID: n.ID()})
	}
	return newPathIndex(entries)
}

// Len is the number of addressable nodes.
func (p *PathIndex) Len() int { return len(p.entries) }

// Has reports whether a full path is in the index, by title: a node whose
// label already carries an "@<hex>" discriminator is found by the plain path
// its title spells, which is what a rename would collide with.
func (p *PathIndex) Has(path string) bool {
	if _, ok := p.byPath[path]; ok {
		return true
	}
	for _, e := range p.entries {
		if stripDiscriminators(e.Path) == path {
			return true
		}
	}
	return false
}

// Rename returns an address with its last segment replaced — the address the
// same node answers to once a rename has been applied.
func Rename(addr, title string) string {
	if at := strings.LastIndex(addr, PathSep); at >= 0 {
		return addr[:at+len(PathSep)] + title
	}
	return title
}

// Resolve turns an address into the node it names. An address is a full path
// or any suffix of one — "HRS > Finance > Employee #1" and "Employee #1" both
// reach the same node as long as only one path ends that way.
//
// Ambiguity is never guessed: two matches is an error listing both, because
// the alternative is writing a document's facts into the wrong node and
// leaving no trace that it happened.
func (p *PathIndex) Resolve(addr string) (PathEntry, error) {
	paths := make([]string, len(p.entries))
	for i, e := range p.entries {
		paths[i] = e.Path
	}
	hit, err := matchPath(paths, addr)
	if err != nil {
		return PathEntry{}, err
	}
	return PathEntry{Path: hit, ID: p.byPath[hit]}, nil
}

// matchPath is the addressing rule, shared by every index: exact path, then
// unique suffix, then the same again with the "@<hex>" discriminators dropped.
func matchPath(paths []string, addr string) (string, error) {
	addr = strings.TrimSpace(addr)
	if addr == "" {
		return "", fmt.Errorf("empty address")
	}

	// The separator in front of the suffix keeps the match on a segment
	// boundary: " > Employee #1" cannot match "Employee #10".
	suffix := PathSep + addr
	var hits []string
	for _, path := range paths {
		if path == addr || strings.HasSuffix(path, suffix) {
			hits = append(hits, path)
		}
	}

	if len(hits) == 0 {
		// Nothing matched the labels, so try the titles: whoever wrote
		// "Employee #1" has not seen the "@<hex>" a same-titled sibling
		// carries, and "no node matches" is the wrong answer when there are
		// in fact two.
		for _, path := range paths {
			if bare := stripDiscriminators(path); bare == addr || strings.HasSuffix(bare, suffix) {
				hits = append(hits, path)
			}
		}
	}

	switch len(hits) {
	case 1:
		return hits[0], nil
	case 0:
		if bare, ok := withoutTypeSuffix(addr); ok {
			// graph.values.yaml prints "COMPANY [company]", and the "[type]"
			// is a legend, not a path segment. Copying the tree line whole is
			// the single most common way to write an address that cannot
			// match, and saying so beats making the writer rediscover the
			// path from graph.ids.json.
			return "", fmt.Errorf("%w %q — the %q in graph.values.yaml names the node's type, "+
				"not a part of its path; address it as %q",
				ErrNoMatch, addr, addr[len(bare):], bare)
		}
		return "", fmt.Errorf("%w %q", ErrNoMatch, addr)
	}

	var b strings.Builder
	fmt.Fprintf(&b, "ambiguous address %q — %d matches:", addr, len(hits))
	for _, path := range hits {
		b.WriteString("\n      " + path)
	}
	return "", errors.New(b.String())
}

// stripDiscriminators removes the "@<hex>" a label carries when a sibling
// shares its title, leaving the path as someone reading the layer in
// Simulator would write it.
func stripDiscriminators(path string) string {
	if !strings.Contains(path, discriminator) {
		return path
	}
	segments := strings.Split(path, PathSep)
	for i, segment := range segments {
		if at := strings.LastIndex(segment, discriminator); at > 0 && isHex(segment[at+1:]) {
			segments[i] = segment[:at]
		}
	}
	return strings.Join(segments, PathSep)
}

// isHex reports whether s is a discriminator's worth of hex digits — at least
// the four BuildTree starts from, so an "@" inside a real title is left alone.
func isHex(s string) bool {
	if len(s) < 4 {
		return false
	}
	for _, r := range s {
		if (r < '0' || r > '9') && (r < 'a' || r > 'f') {
			return false
		}
	}
	return true
}

// PathTitle is the last segment of a path with its discriminator dropped —
// the node's own title, and what a stored title is checked against to notice
// that an export has gone stale.
func PathTitle(path string) string {
	bare := stripDiscriminators(path)
	if at := strings.LastIndex(bare, PathSep); at >= 0 {
		return bare[at+len(PathSep):]
	}
	return bare
}

// withoutTypeSuffix strips a trailing " [slug]" from an address and reports
// whether there was one — the legend graph.values.yaml prints after every
// node, which is not part of the path.
func withoutTypeSuffix(addr string) (string, bool) {
	if !strings.HasSuffix(addr, "]") {
		return addr, false
	}
	open := strings.LastIndex(addr, "[")
	if open <= 0 || addr[open-1] != ' ' {
		return addr, false
	}
	bare := strings.TrimSpace(addr[:open-1])
	if bare == "" {
		return addr, false
	}
	return bare, true
}
