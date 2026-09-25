package graph

import (
	"bytes"
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

// OpsFileName is the conventional name of a write: the edits a reader of
// graph.values.yaml authored, addressed by path instead of by uuid.
const OpsFileName = "graph.ops.yaml"

// The modes an ops file can ask for.
const (
	// ModeUpsert is the default: an `at:` address that matches no node is an
	// error, and a `type:` op creates the record its `ref:` does not find.
	ModeUpsert = "upsert"
	// ModeStrict is upsert plus the promise never to create: a `type:` op
	// whose `ref:` finds nothing fails instead of creating the record. It is
	// for replaying a file against a twin somebody else has since edited.
	ModeStrict = "strict"
)

// OpsFile is a write against one layer.
//
// It is written to be replayed: every op is an overwrite of a named field on
// a named node, so running the same file twice leaves the layer exactly where
// running it once did. That is why there is no idempotency journal here — the
// planner reads the current values and reports an op whose value is already
// stored as satisfied rather than writing it again.
type OpsFile struct {
	// Layer is the layer the ops address. Checked against the layer being
	// applied to, so a file cannot be aimed at the wrong twin by accident.
	Layer string `yaml:"layer"`
	// SourceDoc names the document the ops were extracted from. Free text,
	// carried into the plan for the reader; nothing keys off it.
	SourceDoc string `yaml:"source_doc,omitempty"`
	// Mode is upsert (default) or strict.
	Mode string `yaml:"mode,omitempty"`
	Ops  []Op   `yaml:"ops"`
	// Unrouted are the facts that matched no node. Not an error and not
	// applied — the review queue, and the most useful output of an import:
	// it is where the org model is missing a node.
	Unrouted []Unrouted `yaml:"unrouted,omitempty"`
}

// Op is one edit.
//
// It addresses its node one of two ways, and they are exclusive:
//
//	at:    a node that is on the layer, by any unique suffix of its path
//	type:  a record of that type, found by `ref:` and created when there is
//	       none — the way a fact is written when every node of its type on
//	       the layer is already taken by a different record
//
// plus any of `set:`, `rename:` and `describe:`. The rest of the vocabulary is
// parsed rather than rejected by the YAML decoder so that an unsupported op
// fails at planning with an explanation, next to its address, instead of
// failing the whole file with a decode error.
type Op struct {
	// ID is the uuid of the node this op writes to, and it wins over At
	// outright: when it is set the path is not consulted at all.
	//
	// An apply stamps it into every op that reaches a node, before writing
	// anything — see StampOps. That is what makes a file replayable across
	// its own renames: `rename:` moves the node's title, the title is the
	// last segment of its path, and so an op that renamed successfully has
	// an `at:` that names nothing the second time round. The uuid does not
	// move.
	//
	// At is left in place beside it, unread, because it is the only part of
	// an op a person can recognise the node from.
	ID string `yaml:"id,omitempty"`
	// At addresses an existing node by any unique suffix of its path.
	At string `yaml:"at,omitempty"`
	// Set overwrites fields, keyed by the field names in types.schema.yaml.
	// Writing "title" here is an error — use Rename.
	Set map[string]any `yaml:"set,omitempty"`
	// Rename changes a node's title, deliberately and visibly. It also
	// changes the node's address — the title is the last segment of its
	// path — so an op that renames is resolvable by either name: the one the
	// ops file was written against, and the one it renames to.
	Rename string `yaml:"rename,omitempty"`
	// Describe replaces the node's own description, the text the export
	// renders after "#" on the node's line.
	//
	// It is a key of its own rather than a name in `set:` because 22 of the
	// 50 types on a real layer carry a form field called "description", and
	// a key that means the form field on some nodes and the node itself on
	// others is a trap nobody can read their way out of.
	Describe string `yaml:"describe,omitempty"`
	// Picture is the address of the image the node is drawn with on the
	// canvas. It is a key of its own for the same reason Describe is — it is
	// a property of the actor, not a field of its form, and no `set:` key can
	// reach it.
	//
	// The image is copied, not linked: applying fetches it and uploads it
	// into the workspace's storage, because that is the only thing an actor's
	// picture can be. A node that already carries one is left alone.
	Picture string `yaml:"picture,omitempty"`
	// Type names the type of a record this op owns rather than finds: the
	// slug of a form in types.schema.yaml. It is the second way to address an
	// op, exclusive with At.
	//
	// The record is created off the layer — a record of its form, not a node
	// on the canvas. Placing it on the canvas needs the edge to its parent
	// too, and that endpoint is not wired; an actor on the canvas with no
	// edge is worse than none, because it is invisible in the tree and shows
	// up in the next export as a second root.
	Type string `yaml:"type,omitempty"`
	// Ref is the external business key of that record, and it is required
	// with Type. It is what makes creating idempotent: the plan reads the
	// actor by ref first and writes to the one it finds, so replaying a file —
	// or re-deriving it from the same document — creates nothing twice.
	Ref string `yaml:"ref,omitempty"`

	// Not supported in this version, kept so the planner can say so.
	Under  string         `yaml:"under,omitempty"`
	Create string         `yaml:"create,omitempty"`
	Append map[string]any `yaml:"append,omitempty"`
}

// Unrouted is a fact that fit no node.
type Unrouted struct {
	Text   string `yaml:"text"`
	Source string `yaml:"source,omitempty"`
	Guess  string `yaml:"guess,omitempty"`
	Reason string `yaml:"reason,omitempty"`
}

// ParseOps decodes an ops file.
//
// Unknown keys are a decode error rather than a shrug: a misspelt "sett:"
// that silently applies nothing is the one failure mode a writer cannot see
// in the diff, because the op simply is not in it.
func ParseOps(data []byte) (*OpsFile, error) {
	dec := yaml.NewDecoder(bytes.NewReader(data))
	dec.KnownFields(true)

	var f OpsFile
	if err := dec.Decode(&f); err != nil {
		return nil, fmt.Errorf("graph: parse ops: %w", err)
	}
	switch f.Mode {
	case "", ModeUpsert, ModeStrict:
	default:
		return nil, fmt.Errorf("graph: parse ops: mode %q is not %s or %s", f.Mode, ModeUpsert, ModeStrict)
	}
	if f.Mode == "" {
		f.Mode = ModeUpsert
	}
	return &f, nil
}

// LoadOps reads and decodes an ops file.
func LoadOps(path string) (*OpsFile, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("graph: read ops: %w", err)
	}
	return ParseOps(data)
}
