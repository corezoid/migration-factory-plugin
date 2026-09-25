package graph

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"migration-factory-plugin-mcp/internal/simulator"
)

// actorStateFilter is the projection the write path reads a node with: its
// stored values and description, the form they are keyed by, and whether the
// node is still an empty placeholder. ActorSummaryFilter serves everything
// but "hole", and hole is what tells filling a placeholder apart from editing
// a real node.
//
// The description is read from the node rather than taken from the export,
// where it is collapsed to a single line: comparing against the collapsed
// form would make every multi-line description look like a change forever.
//
// The ref is read so a record reached by its stamped id can be told from one
// that already answers to its business key: a record made by hand has none,
// and the op's `ref:` is written into it so the next file finds it by ref.
// The picture is read because a picture op never overwrites one: a node that
// already carries an image got it from somewhere, and a site is not grounds
// for replacing it.
const actorStateFilter = "id,title,description,data,formId,formTitle,hole,ref,picture"

// dateLayout is how a date field is written. The graph vocabulary has one
// date type and no time zone, so a date is a calendar day and nothing else.
const dateLayout = "2006-01-02"

// Index is a layer in the shape an ops file addresses it: the tree its paths
// resolve against and the type dictionary its field names resolve against.
//
// It is read at plan time rather than loaded from graph.ids.json, so ops are
// always planned against the layer as it is now. The exported sidecar is for
// the reader who writes the ops; by the time they are applied it may be a
// week old, and a stale path -> uuid map writes into the wrong node silently.
type Index struct {
	LayerID string
	Paths   *PathIndex
	Types   *TypeSet
	// PathsFrom names where the addressable paths came from — the exported
	// ids sidecar or a live read of the layer.
	PathsFrom string
	// FromExport marks an index built from the sidecars in a directory,
	// which is what makes that directory something a write has to keep
	// current: the next run resolves its addresses against it.
	FromExport bool
	// Dir is where those sidecars were read from.
	Dir string
	// TypesFrom names where the type dictionary came from — the exported
	// schema file or the form API. The plan prints it: reading types from a
	// file is the one place an apply trusts something other than the layer.
	TypesFrom string
	Warnings  []string
}

// LoadIndex builds the index an ops file is planned against: the paths it
// addresses and the types its field names resolve to.
//
// dir is the export the ops were written against — normally the directory the
// ops file itself sits in. When it holds both sidecars, neither the layer nor
// the forms are read at all:
//
//	graph.ids.json     path -> uuid; paths are unique by construction, so the
//	                   file is the same map a layer read would build, and it
//	                   resolves an address to the node its author was looking
//	                   at rather than to whatever moved since
//	types.schema.yaml  field ids, types and writability, per form
//
// What is never taken from the export is a node's stored values: those are
// read live, per addressed node, because "already applied" is what lets an
// ops file be replayed without a journal.
func LoadIndex(ctx context.Context, sim *simulator.Client, layerID, dir string, concurrency int) (*Index, error) {
	if dir != "" {
		idx, found, err := loadIndexFromExport(layerID, dir)
		if err != nil {
			return nil, err
		}
		if found {
			return idx, nil
		}
	}

	layer, err := PullGraph(ctx, sim, layerID)
	if err != nil {
		return nil, err
	}
	tree, warnings, err := BuildTree(*layer)
	if err != nil {
		return nil, err
	}
	types, typeWarnings, err := ResolveTypes(ctx, sim, layer.Actors, concurrency)
	if err != nil {
		return nil, err
	}
	return &Index{
		LayerID:   layerID,
		Paths:     PathIndexFromTree(tree),
		Types:     types,
		PathsFrom: "the layer, read now",
		TypesFrom: "the form API",
		Warnings:  append(warnings, typeWarnings...),
	}, nil
}

// loadIndexFromExport builds the index from the two sidecars, or reports that
// they are not both there. Both or neither: resolving paths against a file
// while reading types from the API would mix two snapshots for no gain, and
// the API read needs the layer anyway to know which forms to ask for.
func loadIndexFromExport(layerID, dir string) (*Index, bool, error) {
	idsLayer, paths, foundIDs, err := LoadIDs(dir)
	if err != nil || !foundIDs {
		return nil, false, err
	}
	types, foundTypes, err := LoadTypesSchema(dir)
	if err != nil || !foundTypes {
		return nil, false, err
	}
	if idsLayer != layerID {
		return nil, false, fmt.Errorf("graph: %s describes layer %s, applying to %s",
			filepath.Join(dir, IDsFileName), idsLayer, layerID)
	}
	return &Index{
		LayerID:    layerID,
		Paths:      paths,
		Types:      types,
		FromExport: true,
		Dir:        dir,
		PathsFrom:  fmt.Sprintf("%s (%s)", filepath.Join(dir, IDsFileName), plural(paths.Len(), "node", "nodes")),
		TypesFrom:  fmt.Sprintf("%s (%s)", filepath.Join(dir, TypesFileName), plural(len(types.Types), "type", "types")),
	}, true, nil
}

// Plan is what an ops file would do to a layer, worked out before anything is
// written. It is the whole of the dry run: every write, every op that is
// already satisfied, and every op that cannot be applied at all.
type Plan struct {
	LayerID   string
	SourceDoc string
	// PathsFrom and TypesFrom are where the index and the field schemas came
	// from, printed with the plan: these are the only inputs an apply trusts
	// other than the layer itself, and a reader may want to check their age.
	PathsFrom string
	TypesFrom string
	// Warnings are what the index worked around — a layer that is not a
	// clean tree, a form the schema file does not describe.
	Warnings []string
	// Actions are the writes, in the order the ops file lists them.
	Actions []*Action
	// Satisfied are the ops whose values the layer already holds. A replayed
	// ops file consists almost entirely of these — it is what makes the file
	// safe to run again without a journal of what ran before.
	Satisfied []*Satisfied
	// Errors are the ops that cannot be applied. By default their presence
	// stops the whole run.
	Errors []*OpFailure
	// Unrouted is carried through from the ops file untouched: facts that
	// matched no node, for a human to route.
	Unrouted []Unrouted
	// Resolved is every op that reached a node: its 1-based position in the
	// file, and the uuid it addresses. Actions and Satisfied both draw from
	// it, and an apply stamps it back into the file before writing — an op
	// the layer already agrees with needs its id just as much as one that
	// writes, because the next replay is where the path will have gone.
	Resolved map[int]string
}

// Action is one node's worth of change: everything one op writes, in a single
// update.
type Action struct {
	// OpIndex is the op's 1-based position in the file, the number the plan
	// and the error list refer to it by.
	OpIndex int

	Path    string
	ActorID string
	// Title is the node's current title — the left-hand side of a rename,
	// which the path's last segment cannot supply because it carries the
	// "@<hex>" discriminator of a same-titled sibling.
	Title string
	// FormID is the form to address the actor by, which for a multiform node
	// is the leaf form its own values are keyed under.
	FormID int
	Type   string

	// Rename is the new title, empty when the op does not rename.
	Rename string
	// Describe is the new description, empty when the op does not set one.
	Describe string
	// Description is the node's current description, the left-hand side of
	// that change.
	Description string
	// Picture is the address of the image to put on the node, empty when the
	// op does not set one or when the node already carries one. It is
	// resolved to a storage path at apply time and not before: a dry run must
	// not upload anything.
	Picture string
	// StoredPicture is that address once the image has been copied into the
	// workspace's storage — the string the actor actually carries. Empty
	// until the write, and empty afterwards when the image could not be
	// taken, which does not stop the rest of the action.
	StoredPicture string
	// Changes are the field writes, in the order the type declares them.
	Changes []Change
	// FillsHole marks a write into an empty placeholder slot: the node
	// already exists on the canvas with its edges, and the write — of a
	// value, a title or a description — is what turns it into a real actor.
	FillsHole bool
	// Create marks the one action that is not an overwrite: the record does
	// not exist yet and this run brings it into being, off the layer. ActorID
	// is empty until it has been written.
	Create bool
	// Ref is the business key a record is addressed by — the handle on an
	// actor that has no path on the canvas. On a create it is what the record
	// is made with; on an update it is set only when the record has none yet,
	// and the write gives it one.
	Ref string
}

// Change is one field write.
type Change struct {
	// Field is the name in types.schema.yaml, Key the Simulator key the
	// actor stores the value under — the same thing for most fields, and
	// "__form__<formId>:<id>" on a multiform node.
	Field string
	Key   string
	Value any
	// Old and New are the rendered values, for the diff.
	Old string
	New string
}

// Satisfied is an op the layer already agrees with.
type Satisfied struct {
	OpIndex int
	Path    string
}

// OpFailure is an op that cannot be applied, kept with the position and the
// address it named so the writer can find it in the file.
type OpFailure struct {
	OpIndex int
	At      string
	Err     error
}

// OK reports whether every op in the file can be applied.
func (p *Plan) OK() bool { return len(p.Errors) == 0 }

// writes reports whether the action still has anything to send. It can end up
// with nothing only one way: the op carried a picture and nothing else, and
// the image could not be taken.
func (a *Action) writes() bool {
	return a.Create || a.Rename != "" || a.Describe != "" || a.Ref != "" ||
		a.StoredPicture != "" || len(a.Changes) > 0
}

// Data renders an action's changes the way the update route takes them.
func (a *Action) Data() map[string]any {
	if len(a.Changes) == 0 {
		return nil
	}
	data := make(map[string]any, len(a.Changes))
	for _, c := range a.Changes {
		data[c.Key] = c.Value
	}
	return data
}

// PlanOps works out what an ops file would do, without writing anything.
//
// It reads the current values of every node an op addresses: a plan that does
// not know what is stored cannot tell a write from a no-op, and telling them
// apart is what lets the same ops file be replayed instead of journalled.
func PlanOps(ctx context.Context, sim *simulator.Client, idx *Index, ops *OpsFile, concurrency int) (*Plan, error) {
	if idx == nil || ops == nil {
		return nil, fmt.Errorf("graph: PlanOps needs an index and an ops file")
	}
	if ops.Layer != "" && ops.Layer != idx.LayerID {
		return nil, fmt.Errorf("graph: ops file targets layer %s, applying to %s", ops.Layer, idx.LayerID)
	}

	plan := &Plan{
		LayerID:   idx.LayerID,
		SourceDoc: ops.SourceDoc,
		PathsFrom: idx.PathsFrom,
		TypesFrom: idx.TypesFrom,
		Warnings:  idx.Warnings,
		Unrouted:  ops.Unrouted,
		Resolved:  make(map[int]string, len(ops.Ops)),
	}

	// Pass one resolves addresses, so pass two can read every node it needs
	// in one concurrent batch instead of one round trip per op.
	type resolved struct {
		op    Op
		n     int
		entry PathEntry
		// create is the type of a record this op owns and Simulator does not
		// hold yet: nothing to read, and nothing to diff against.
		create *Type
	}
	var targets []resolved
	// A record op is read one at a time rather than in the batch below: it is
	// addressed by ref, which the batch cannot express, and there are a
	// handful of them in a file where there are dozens of path addresses.
	seeded := map[string]*simulator.Actor{}
	claimed := map[string]int{}
	for i, op := range ops.Ops {
		n := i + 1
		fail := func(err error) {
			plan.Errors = append(plan.Errors, &OpFailure{OpIndex: n, At: opAddress(op), Err: err})
		}

		if op.Type != "" {
			t, err := resolveRecordOp(idx, op)
			if err != nil {
				fail(err)
				continue
			}
			if prev, dup := claimed[recordKey(t.FormID, op.Ref)]; dup {
				fail(fmt.Errorf("op #%d already claims ref %q on type %q — a ref is unique per "+
					"form, so these are the same record written twice", prev, op.Ref, t.Slug))
				continue
			}
			claimed[recordKey(t.FormID, op.Ref)] = n

			actor, err := findRecord(ctx, sim, t, op)
			switch {
			case err != nil:
				fail(err)
			case actor == nil && ops.Mode == ModeStrict:
				fail(fmt.Errorf("%s does not exist and mode is %s, which never creates", recordPath(op.Ref), ModeStrict))
			case actor == nil:
				targets = append(targets, resolved{op: op, n: n, create: t,
					entry: PathEntry{Path: recordPath(op.Ref)}})
			default:
				// The record is already there — an earlier run made it, or
				// somebody made it by hand. From here it is an ordinary
				// update, which is what makes the file replayable.
				seeded[actor.ID] = actor
				plan.Resolved[n] = actor.ID
				targets = append(targets, resolved{op: op, n: n,
					entry: PathEntry{ID: actor.ID, Path: recordPath(op.Ref)}})
			}
			continue
		}

		entry, err := resolveOp(idx, op)
		if err != nil {
			fail(err)
			continue
		}
		plan.Resolved[n] = entry.ID
		targets = append(targets, resolved{op: op, n: n, entry: entry})
	}

	ids := make([]string, 0, len(targets))
	seen := make(map[string]bool, len(targets))
	for _, t := range targets {
		if t.entry.ID == "" || seen[t.entry.ID] || seeded[t.entry.ID] != nil {
			continue
		}
		seen[t.entry.ID] = true
		ids = append(ids, t.entry.ID)
	}
	states, readErrs, err := fetchStates(ctx, sim, ids, concurrency)
	if err != nil {
		return nil, err
	}
	for id, actor := range seeded {
		states[id] = actor
	}
	pc := &planContext{ctx: ctx, sim: sim, idx: idx, states: states}

	for _, t := range targets {
		fail := func(err error) {
			plan.Errors = append(plan.Errors, &OpFailure{
				OpIndex: t.n, At: opAddress(t.op), Err: err,
			})
		}

		if t.create != nil {
			action, err := planCreate(pc, t.op, t.n, t.create)
			if err != nil {
				fail(err)
				continue
			}
			if action.Rename == "" {
				// Not an error — a record is legitimate without one — but an
				// untitled actor is what a person scrolling the form's
				// records cannot tell from any other.
				plan.Warnings = append(plan.Warnings, fmt.Sprintf(
					"%s is created without a `rename:`, so it has no title to be recognised by", action.Path))
			}
			plan.Actions = append(plan.Actions, action)
			continue
		}

		state := states[t.entry.ID]
		if state == nil {
			fail(fmt.Errorf("%s: %v — cannot tell a write from a no-op without the stored values",
				t.entry.Path, readErrs[t.entry.ID]))
			continue
		}
		// Both checks below read the address as a path, so they are for the
		// ops that have one: a record addressed by ref is not on the layer
		// and has neither a title in its address nor siblings to collide with.
		if t.op.Type == "" {
			// The index may be a snapshot, and this is the one drift that
			// matters: the node we are about to write to is no longer the one
			// the path named. A title that has merely been edited since the
			// export is not an error — the uuid still names the right node —
			// but it is the visible sign that the export and the layer have
			// parted.
			if want, got := PathTitle(t.entry.Path), strings.TrimSpace(state.Title); want != got && got != "" {
				plan.Warnings = append(plan.Warnings, fmt.Sprintf(
					"%s is titled %q on the layer — the index is older than the node", t.entry.Path, got))
			}

			if warning := renameCollision(idx, t.entry.Path, t.op.Rename); warning != "" {
				plan.Warnings = append(plan.Warnings, warning)
			}
		}

		action, err := planOp(pc, t.op, t.n, t.entry, state)
		if err != nil {
			fail(err)
			continue
		}
		if action == nil {
			plan.Satisfied = append(plan.Satisfied, &Satisfied{
				OpIndex: t.n, Path: t.entry.Path,
			})
			continue
		}
		plan.Actions = append(plan.Actions, action)
	}
	plan.Warnings = append(plan.Warnings, pc.warnings...)
	return plan, nil
}

// renameCollision reports a rename that gives a node the title a sibling
// already carries. It is not an error — the layer allows it — but it changes
// the address of the sibling as well: the next export has to tell the two
// apart, and both take an "@<hex>" discriminator they did not have before.
func renameCollision(idx *Index, path, rename string) string {
	if rename == "" {
		return ""
	}
	sibling := Rename(path, rename)
	if sibling == path || !idx.Paths.Has(sibling) {
		return ""
	}
	return fmt.Sprintf("renaming %s to %q joins a sibling of that name — both take an %q "+
		"discriminator in the next export, changing the address of each", path, rename, discriminator)
}

// planContext carries what planning one op needs beyond the op itself: the
// index, and a reader for actors the plan turns out to need — the target of a
// ref field, whose type has to be checked before the reference is written.
type planContext struct {
	ctx    context.Context
	sim    *simulator.Client
	idx    *Index
	states map[string]*simulator.Actor
	// warnings are what planning one op has to say to the reader without
	// failing it — an op partly dropped, and why.
	warnings []string
}

// warn records a note about an op that is still going ahead.
func (pc *planContext) warn(format string, args ...any) {
	pc.warnings = append(pc.warnings, fmt.Sprintf(format, args...))
}

// state reads an actor, once. The addressed nodes are already in the map from
// the batch read; a ref target usually is not, and refs are rare enough that
// one extra round trip beats a second planning pass.
func (pc *planContext) state(id string) (*simulator.Actor, error) {
	if a, ok := pc.states[id]; ok {
		return a, nil
	}
	a, err := pc.sim.GetActor(pc.ctx, id, actorStateFilter)
	if err != nil {
		return nil, err
	}
	pc.states[id] = a
	return a, nil
}

// unsupportedVocabulary rejects the op shapes this version cannot carry out,
// whichever way the op is addressed.
func unsupportedVocabulary(op Op) error {
	switch {
	case op.Under != "" || op.Create != "":
		// Placing a node on the canvas needs two writes: the actor, and the
		// edge that hangs it under its parent. Only the first has an endpoint
		// in the client, and a node on the canvas with no edge is worse than
		// no node at all — it is invisible in the tree and shows up in the
		// next export as a second root. A record that is on no canvas at all
		// is a different thing, and that is what `type:` writes.
		return fmt.Errorf("`under:`/`create:` is not supported: the layer edge endpoint " +
			"is not wired, so a created node would sit on the canvas unlinked from its parent. " +
			"Add the node in Simulator (or as a hole) and address it with `at:`, or give the fact " +
			"a record of its own with `type:` and `ref:`")
	case len(op.Append) > 0:
		return fmt.Errorf("`append:` is not supported: use `set:` with the full field value")
	}
	return nil
}

// resolveOp turns an op into the node it addresses, rejecting the op shapes
// this version cannot carry out.
func resolveOp(idx *Index, op Op) (PathEntry, error) {
	if err := unsupportedVocabulary(op); err != nil {
		return PathEntry{}, err
	}
	switch {
	case op.ID == "" && op.At == "":
		return PathEntry{}, fmt.Errorf("op has no address: give it an `at:` path or an `id:`")
	case len(op.Set) == 0 && op.Rename == "" && op.Describe == "" && op.Picture == "":
		return PathEntry{}, fmt.Errorf("op does nothing: no `set:`, `rename:`, `describe:` or `picture:`")
	}

	// A stamped op is addressed by uuid and the path is not consulted: `at:`
	// is what the node was called when the file was written, and the whole
	// point of the stamp is that it may not be called that any more.
	if op.ID != "" {
		return idx.Paths.ByID(op.ID)
	}

	entry, err := idx.Paths.Resolve(op.At)
	if err == nil || op.Rename == "" || !errors.Is(err, ErrNoMatch) {
		// An ambiguous address is never retried: a second guess at a node
		// the writer has not chosen between is worse than the error.
		return entry, err
	}

	// A rename moves the node's address, because the title is the last
	// segment of the path. An ops file replayed after its own rename
	// addresses a node that no longer answers to that name, so the name it
	// renames to is tried as well — and resolving there means the rename has
	// already been applied, which planOp will see and report as satisfied.
	renamed, renamedErr := idx.Paths.Resolve(Rename(op.At, op.Rename))
	if renamedErr != nil {
		return entry, err
	}
	return renamed, nil
}

// planOp builds the action for one resolved op, or nil when the layer already
// holds everything the op asks for.
func planOp(pc *planContext, op Op, n int, entry PathEntry, state *simulator.Actor) (*Action, error) {
	// The form to address the actor by comes from the actor itself, not from
	// the index: on a multiform node it is the leaf form its own values are
	// keyed under, and that is readable only from the data the node carries.
	formID := state.ResolvedFormID()
	action := &Action{
		OpIndex:     n,
		Path:        entry.Path,
		ActorID:     entry.ID,
		Title:       state.Title,
		Description: state.Description,
		FormID:      formID,
		Type:        pc.idx.Types.Slug(formID),
	}

	if op.Rename != "" && strings.TrimSpace(op.Rename) != strings.TrimSpace(state.Title) {
		action.Rename = op.Rename
	}
	// The description is compared verbatim: it is multi-line free text, and
	// a trailing newline someone added in the source document is a change
	// nobody wants to be told about, but a leading one is not worth guessing
	// about either. Trim the ends, keep the middle.
	if op.Describe != "" && strings.TrimSpace(op.Describe) != strings.TrimSpace(state.Description) {
		action.Describe = op.Describe
	}
	// A record reached by its stamped `id:` may carry no ref at all — it was
	// made by hand, and `find_records` handed out the uuid because nothing
	// else could find it. The op's `ref:` is written into it here, once:
	// otherwise the next ops file, addressing it by ref alone, reads a 404
	// and creates the record a second time.
	if op.Type != "" && op.ID != "" && state.Ref == "" {
		action.Ref = op.Ref
	}

	// A picture is filled, never replaced. The stored one came from
	// somewhere — an earlier run, a person, another source — and an image
	// found on a web page does not outrank it.
	if op.Picture != "" {
		if err := validatePictureURL(op.Picture); err != nil {
			return nil, fmt.Errorf("%s: %w", entry.Path, err)
		}
		if strings.TrimSpace(state.Picture) == "" {
			action.Picture = op.Picture
		} else {
			pc.warn("%s already carries a picture, so %s was not taken — a stored image is not "+
				"replaced from a web page", entry.Path, op.Picture)
		}
	}

	if len(op.Set) > 0 {
		t, ok := pc.idx.Types.Type(formID)
		if !ok || len(t.Fields) == 0 {
			return nil, fmt.Errorf("%s: form %d is not in %s — re-export, or the write would go "+
				"against a field nobody can check", entry.Path, formID, pc.idx.TypesFrom)
		}
		changes, err := planSet(pc, op, t, state, entry.Path)
		if err != nil {
			return nil, err
		}
		action.Changes = changes
	}

	if action.Rename == "" && action.Describe == "" && action.Ref == "" &&
		action.Picture == "" && len(action.Changes) == 0 {
		return nil, nil
	}
	// A hole is an empty placeholder slot, and any write at all ends that:
	// once someone has named, described or filled it, it is a node somebody
	// is working on rather than a slot nobody has claimed.
	//
	// This is decided here rather than per kind of change on purpose. The
	// check sits after the nothing-to-do return above, so it is reached only
	// when the run actually writes — an op the layer already agrees with
	// leaves the flag as it found it.
	action.FillsHole = state.Hole
	return action, nil
}

// planSet checks an op's field writes against a node's stored values and
// returns the ones that would change something.
//
// It is shared by the two kinds of op. An update diffs against what the node
// holds; a create passes an empty actor, so every value reads as a change and
// storageKey returns the plain field id. Neither wants its own copy of the
// enum, date and reference checks.
func planSet(pc *planContext, op Op, t *Type, state *simulator.Actor, path string) ([]Change, error) {
	if _, forbidden := op.Set["title"]; forbidden {
		return nil, fmt.Errorf("%s: do not write `title` in `set:` — use `rename:`", path)
	}

	// Every field of the op is checked, and all the complaints are reported
	// together: fixing one typo per run is the slowest possible way to find
	// out about the other three.
	var (
		changes  []Change
		problems []string
	)

	// Fields in the order the form declares them, so two plans of the same
	// ops file read the same way.
	for _, f := range t.Fields {
		raw, ok := op.Set[f.Name]
		if !ok {
			continue
		}
		change, err := planChange(pc, f, raw, state)
		switch {
		case err != nil:
			problems = append(problems, err.Error())
		case change != nil:
			changes = append(changes, *change)
		}
	}

	// Anything left is a name the type does not have.
	if unknown := unknownFields(op.Set, t); len(unknown) > 0 {
		problems = append(problems, "type "+strconv.Quote(t.Slug)+" has "+describeUnknown(unknown, t))
	}
	if len(problems) > 0 {
		return nil, fmt.Errorf("%s: %s", path, strings.Join(problems, "; "))
	}
	return changes, nil
}

// planChange coerces one value and compares it with what is stored, returning
// nil when the layer already holds it.
func planChange(pc *planContext, f *FieldSpec, raw any, state *simulator.Actor) (*Change, error) {
	if !f.Writable {
		return nil, fmt.Errorf("field %q is not writable", f.Name)
	}
	value, err := coerceValue(pc, f, raw)
	if err != nil {
		return nil, err
	}

	key := storageKey(state.Data, f.ID)
	old := formatValue(state.Data[key])
	next := formatValue(value)
	if old == next {
		return nil, nil
	}
	return &Change{Field: f.Name, Key: key, Value: value, Old: old, New: next}, nil
}

// storageKey is the key an actor stores a field under. It is the field id,
// except on a multiform node, where another form's fields are keyed
// "__form__<thatFormId>:<fieldId>" — and a write has to use the same key the
// node already carries or it lands beside the value instead of on it.
func storageKey(data map[string]any, fieldID string) string {
	if _, ok := data[fieldID]; ok {
		return fieldID
	}
	suffix := ":" + fieldID
	for key := range data {
		if strings.HasPrefix(key, "__form__") && strings.HasSuffix(key, suffix) {
			return key
		}
	}
	return fieldID
}

// coerceValue turns a value from the ops file into the shape the field takes,
// refusing anything the type cannot hold.
func coerceValue(pc *planContext, f *FieldSpec, raw any) (any, error) {
	if isEmptyValue(raw) {
		// replaceEmpty=false is what keeps a partial write from wiping the
		// fields it does not mention; the same flag means an empty value
		// would be dropped rather than clear the field. Saying so beats
		// planning a write that quietly does nothing.
		return nil, fmt.Errorf("field %q: empty value — clearing a field is not supported, "+
			"do it in Simulator", f.Name)
	}

	if want, ok := refTarget(f.Type); ok {
		path, isString := raw.(string)
		if !isString {
			return nil, fmt.Errorf("field %q wants ref(%s) — write the target's path, not %T", f.Name, want, raw)
		}
		target, err := pc.idx.Paths.Resolve(path)
		if err != nil {
			return nil, fmt.Errorf("field %q: %w", f.Name, err)
		}
		// The target's type is what makes a reference checkable, and only
		// the target itself knows its form — so a ref costs one read.
		state, err := pc.state(target.ID)
		if err != nil {
			return nil, fmt.Errorf("field %q: read the ref target %q: %w", f.Name, target.Path, err)
		}
		if got := pc.idx.Types.Slug(state.ResolvedFormID()); got != want {
			return nil, fmt.Errorf("field %q wants ref(%s), %q is [%s]", f.Name, want, target.Path, got)
		}
		// The shape a reference is stored in: the uuid, and the title the UI
		// shows beside it. Read back by labelOf, which is what the plan
		// compares against.
		return map[string]any{"id": target.ID, "title": state.Title}, nil
	}

	if options, ok := enumOptions(f.Type); ok {
		got := fmt.Sprint(raw)
		for _, o := range options {
			if o == got {
				return o, nil
			}
		}
		return nil, fmt.Errorf("field %q: %q is not one of %s", f.Name, got, strings.Join(options, ", "))
	}

	switch f.Type {
	case TypeInt:
		return coerceInt(f, raw)
	case TypeNumber:
		return coerceNumber(f, raw)
	case TypeBool:
		return coerceBool(f, raw)
	case TypeDate:
		return coerceDate(f, raw)
	case TypeString, TypeText:
		return coerceString(f, raw)
	}
	// Unknown types come from a widget nobody has mapped yet; fieldType
	// already degrades those to string, so this is only reachable if the
	// vocabulary grows without this switch.
	return nil, fmt.Errorf("field %q: unknown field type %q", f.Name, f.Type)
}

func coerceInt(f *FieldSpec, raw any) (any, error) {
	switch v := raw.(type) {
	case int:
		return int64(v), nil
	case int64:
		return v, nil
	case float64:
		if v != float64(int64(v)) {
			return nil, fmt.Errorf("field %q: %v is not a whole number", f.Name, v)
		}
		return int64(v), nil
	case string:
		n, err := strconv.ParseInt(strings.TrimSpace(v), 10, 64)
		if err != nil {
			return nil, fmt.Errorf("field %q: %q is not an int", f.Name, v)
		}
		return n, nil
	}
	return nil, fmt.Errorf("field %q: %v is not an int", f.Name, raw)
}

func coerceNumber(f *FieldSpec, raw any) (any, error) {
	switch v := raw.(type) {
	case int:
		return float64(v), nil
	case int64:
		return float64(v), nil
	case float64:
		return v, nil
	case string:
		n, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
		if err != nil {
			return nil, fmt.Errorf("field %q: %q is not a number", f.Name, v)
		}
		return n, nil
	}
	return nil, fmt.Errorf("field %q: %v is not a number", f.Name, raw)
}

func coerceBool(f *FieldSpec, raw any) (any, error) {
	switch v := raw.(type) {
	case bool:
		return v, nil
	case string:
		b, err := strconv.ParseBool(strings.TrimSpace(v))
		if err != nil {
			return nil, fmt.Errorf("field %q: %q is not a bool", f.Name, v)
		}
		return b, nil
	}
	return nil, fmt.Errorf("field %q: %v is not a bool", f.Name, raw)
}

// coerceDate normalises a date to YYYY-MM-DD. An unquoted 2024-03-01 in YAML
// decodes as a time.Time, a quoted one as a string, and the layer must not be
// able to tell which way the ops file was written.
func coerceDate(f *FieldSpec, raw any) (any, error) {
	switch v := raw.(type) {
	case time.Time:
		return v.Format(dateLayout), nil
	case string:
		s := strings.TrimSpace(v)
		for _, layout := range []string{dateLayout, time.RFC3339, "2006-01-02T15:04:05"} {
			if t, err := time.Parse(layout, s); err == nil {
				return t.Format(dateLayout), nil
			}
		}
		return nil, fmt.Errorf("field %q: %q is not a date (want %s)", f.Name, v, dateLayout)
	}
	return nil, fmt.Errorf("field %q: %v is not a date", f.Name, raw)
}

func coerceString(f *FieldSpec, raw any) (any, error) {
	switch v := raw.(type) {
	case string:
		return v, nil
	case bool:
		return strconv.FormatBool(v), nil
	case int:
		return strconv.Itoa(v), nil
	case int64:
		return strconv.FormatInt(v, 10), nil
	case float64:
		return strconv.FormatFloat(v, 'f', -1, 64), nil
	case time.Time:
		return v.Format(dateLayout), nil
	}
	// A list or a mapping under a text field is a structure the writer meant
	// to put somewhere else; flattening it would bury that mistake.
	return nil, fmt.Errorf("field %q: %T cannot be written to a %s field", f.Name, raw, f.Type)
}

// refTarget reads the slug out of "ref(<slug>)".
func refTarget(fieldType string) (string, bool) {
	rest, ok := strings.CutPrefix(fieldType, "ref(")
	if !ok {
		return "", false
	}
	return strings.CutSuffix(rest, ")")
}

// enumOptions reads the options out of "enum[a, b, c]".
func enumOptions(fieldType string) ([]string, bool) {
	rest, ok := strings.CutPrefix(fieldType, "enum[")
	if !ok {
		return nil, false
	}
	rest, ok = strings.CutSuffix(rest, "]")
	if !ok {
		return nil, false
	}
	options := strings.Split(rest, ",")
	for i := range options {
		options[i] = strings.TrimSpace(options[i])
	}
	return options, true
}

// unknownFields lists the names in a `set:` that the type does not have.
func unknownFields(set map[string]any, t *Type) []string {
	known := make(map[string]bool, len(t.Fields))
	for _, f := range t.Fields {
		known[f.Name] = true
	}
	var unknown []string
	for name := range set {
		if !known[name] {
			unknown = append(unknown, name)
		}
	}
	sort.Strings(unknown)
	return unknown
}

// describeUnknown names the fields a type does not have, with the nearest
// real field suggested for each — a misspelt name is the common case, and the
// schema is too long to scan by eye.
func describeUnknown(unknown []string, t *Type) string {
	parts := make([]string, 0, len(unknown))
	for _, name := range unknown {
		part := "no field " + strconv.Quote(name)
		if near := nearestField(name, t); near != "" {
			part += " (did you mean " + strconv.Quote(near) + "?)"
		}
		parts = append(parts, part)
	}
	return strings.Join(parts, "; ")
}

// nearestField is the field a misspelt name most likely meant: the closest by
// edit distance, and only when it is close enough that the suggestion is
// worth making. A type has fifty fields whose names all look alike, so a bad
// guess here costs more than no guess.
func nearestField(name string, t *Type) string {
	// A third of the name may differ, and never more than three characters:
	// "frist_name" reaches "first_name", "notes" does not reach "name".
	budget := min(3, max(1, len(name)/3))
	best := ""
	for _, f := range t.Fields {
		if d := editDistance(name, f.Name); d <= budget {
			best, budget = f.Name, d-1
		}
	}
	return best
}

// editDistance is Levenshtein, one row at a time.
func editDistance(a, b string) int {
	prev := make([]int, len(b)+1)
	curr := make([]int, len(b)+1)
	for j := range prev {
		prev[j] = j
	}
	for i := 1; i <= len(a); i++ {
		curr[0] = i
		for j := 1; j <= len(b); j++ {
			cost := 1
			if a[i-1] == b[j-1] {
				cost = 0
			}
			curr[j] = min(prev[j]+1, min(curr[j-1]+1, prev[j-1]+cost))
		}
		prev, curr = curr, prev
	}
	return prev[len(b)]
}

// opAddress is the address an op names, for an error about an op that has not
// been resolved yet.
func opAddress(op Op) string {
	switch {
	case op.At != "":
		return op.At
	case op.Type != "" && op.Ref != "":
		return recordPath(op.Ref) + " [" + op.Type + "]"
	case op.ID != "":
		return op.ID
	case op.Under != "":
		return op.Under + PathSep + op.Create
	}
	return ""
}

// fetchStates reads the current state of the addressed nodes, concurrently.
// A node that cannot be read is reported per node rather than failing the
// plan: one unreadable actor should not hide the other forty ops.
func fetchStates(ctx context.Context, sim *simulator.Client, ids []string, concurrency int) (map[string]*simulator.Actor, map[string]error, error) {
	if sim == nil {
		return nil, nil, fmt.Errorf("graph: planning ops needs a simulator client")
	}

	actors := make([]*simulator.Actor, len(ids))
	errs := make([]error, len(ids))
	parallel(len(ids), concurrency, func(i int) {
		actors[i], errs[i] = sim.GetActor(ctx, ids[i], actorStateFilter)
	})

	states := make(map[string]*simulator.Actor, len(ids))
	failures := make(map[string]error)
	for i, id := range ids {
		if errs[i] != nil {
			failures[id] = errs[i]
			continue
		}
		states[id] = actors[i]
	}
	return states, failures, nil
}

// planValueWidth caps a value in the diff. graph.values.yaml truncates
// nothing because it is the only copy of the data; a plan is a summary, and a
// 2 000-character description would bury the ten changes around it.
const planValueWidth = 72

// Render writes the plan the way a dry run prints it: a line per action, the
// before and after of every field it touches, then what was already applied,
// what failed, and what nobody could route.
func (p *Plan) Render() string {
	var b strings.Builder
	b.WriteString("plan for layer " + p.LayerID)
	if p.SourceDoc != "" {
		b.WriteString("  (source: " + p.SourceDoc + ")")
	}
	b.WriteString("\n  " + p.summary() + "\n")
	if p.PathsFrom != "" {
		b.WriteString("  paths from " + p.PathsFrom + "\n")
	}
	if p.TypesFrom != "" {
		b.WriteString("  field schemas from " + p.TypesFrom + "\n")
	}
	for _, w := range p.Warnings {
		b.WriteString("  ! " + w + "\n")
	}

	for _, a := range p.Actions {
		b.WriteString("\n  " + a.mark() + " " + a.Path + "  [" + a.Type + "]")
		switch {
		case a.Create:
			b.WriteString("  (new record of form " + strconv.Itoa(a.FormID) + ", not on the layer)")
		case a.FillsHole:
			b.WriteString("  (fills a placeholder hole)")
		}
		b.WriteString("\n")
		if a.Rename != "" {
			b.WriteString("      title: " + shorten(a.Title) + " -> " + shorten(a.Rename) + "\n")
		}
		if a.Describe != "" {
			b.WriteString("      description: " + shorten(oneLine(a.Description)) +
				" -> " + shorten(oneLine(a.Describe)) + "\n")
		}
		if a.Ref != "" && !a.Create {
			b.WriteString("      ref: — -> " + shorten(a.Ref) + "\n")
		}
		if a.Picture != "" {
			b.WriteString("      picture: " + shorten(a.Picture) + "\n")
		}
		for _, c := range a.Changes {
			b.WriteString("      " + c.Field + ": " + shorten(c.Old) + " -> " + shorten(c.New) + "\n")
		}
	}

	if p.creates() > 0 {
		b.WriteString("\n  a new record is created as an actor of its form and is not placed on the\n" +
			"  canvas: it will not appear in graph.values.yaml, and `ref:` plus the id stamped\n" +
			"  into the ops file are the only handles on it. Nothing here undoes a create.\n")
	}

	if len(p.Satisfied) > 0 {
		b.WriteString("\n  already applied — the layer holds these values:\n")
		for _, s := range p.Satisfied {
			b.WriteString("      " + s.Path + "\n")
		}
	}

	if len(p.Errors) > 0 {
		b.WriteString("\n  " + plural(len(p.Errors), "error", "errors") + ":\n")
		for _, e := range p.Errors {
			b.WriteString("    op #" + strconv.Itoa(e.OpIndex))
			if e.At != "" {
				b.WriteString("  " + strconv.Quote(e.At))
			}
			b.WriteString("\n      " + strings.ReplaceAll(e.Err.Error(), "\n", "\n      ") + "\n")
		}
	}

	if len(p.Unrouted) > 0 {
		b.WriteString("\n  unrouted — " + plural(len(p.Unrouted), "fact", "facts") +
			" matched no node; this is where the model has holes:\n")
		for _, u := range p.Unrouted {
			b.WriteString("      " + shorten(oneLine(u.Text)) + "\n")
			if u.Guess != "" || u.Reason != "" {
				b.WriteString("        guess: " + firstNonEmpty(u.Guess, "-"))
				if u.Reason != "" {
					b.WriteString(" — " + oneLine(u.Reason))
				}
				b.WriteString("\n")
			}
		}
	}
	return b.String()
}

// creates counts the actions that bring a record into being.
func (p *Plan) creates() int {
	n := 0
	for _, a := range p.Actions {
		if a.Create {
			n++
		}
	}
	return n
}

// summary is the one-line count at the head of a plan.
func (p *Plan) summary() string {
	writes, renames, describes, holes, pictures := 0, 0, 0, 0, 0
	for _, a := range p.Actions {
		if a.Picture != "" {
			// Counted for creates too: a record arriving with a face is the
			// same write as a node getting one.
			pictures++
		}
		if a.Create {
			continue
		}
		if len(a.Changes) > 0 {
			writes++
		}
		if a.Rename != "" {
			renames++
		}
		if a.Describe != "" {
			describes++
		}
		if a.FillsHole {
			holes++
		}
	}

	var parts []string
	add := func(n int, one, many string) {
		if n > 0 {
			parts = append(parts, plural(n, one, many))
		}
	}
	add(p.creates(), "record to create", "records to create")
	add(writes, "node to update", "nodes to update")
	add(renames, "rename", "renames")
	add(describes, "description", "descriptions")
	add(holes, "hole to fill", "holes to fill")
	add(pictures, "picture to take", "pictures to take")
	add(len(p.Satisfied), "op already applied", "ops already applied")
	add(len(p.Errors), "error", "errors")
	if len(parts) == 0 {
		return "nothing to do"
	}
	return strings.Join(parts, ", ")
}

// mark is the character an action is listed with: a create, a fill, a field
// write or a bare rename.
func (a *Action) mark() string {
	switch {
	case a.Create:
		return "*"
	case a.FillsHole:
		return "+"
	case len(a.Changes) > 0:
		return "~"
	}
	return ">"
}

// shorten renders a value for the diff: an unset value as an em dash, a long
// one cut to planValueWidth.
func shorten(s string) string {
	if s == "" {
		return "—"
	}
	if len([]rune(s)) > planValueWidth {
		s = string([]rune(s)[:planValueWidth]) + "…"
	}
	return strconv.Quote(s)
}

func plural(n int, one, many string) string {
	if n == 1 {
		return "1 " + one
	}
	return strconv.Itoa(n) + " " + many
}
