package graph

import (
	"context"
	"fmt"
	"log"
	"path/filepath"

	"migration-factory-plugin-mcp/internal/simulator"
)

// ApplyOptions configure a run of an ops file.
type ApplyOptions struct {
	// LayerID is the layer to write to. Optional when the ops file names one;
	// when both do, they must agree.
	LayerID string
	// FromDir is the export the ops were written against — the directory
	// holding types.schema.yaml. ApplyOpsFile defaults it to the ops file's
	// own directory, which is where an export and the ops written from it
	// live together. Empty means read the types from the form API.
	//
	// Only the type dictionary is taken from there. The tree and the stored
	// values are always read live: the tree is what makes an ambiguous
	// address an error rather than a wrong write, and the values are what
	// let the same ops file be replayed without a journal.
	FromDir string
	// DryRun plans and returns without writing anything. The plan is the
	// whole point of the dry run — show it to whoever authored the ops
	// before the layer changes.
	DryRun bool
	// Partial applies the ops that validate even when others do not. Off by
	// default: half an imported document leaves a twin nobody can reason
	// about, and there is no rollback here.
	Partial bool
	// OpsPath is the file the ops were read from, stamped with the resolved
	// node ids before a write. Empty — ApplyOps called with ops already in
	// memory — skips the stamping: there is no file to record them in.
	OpsPath string
	// KeepExport leaves the export in FromDir as it is after a write.
	//
	// By default a successful write rewrites it, because the run has just
	// made it wrong: the next ops file resolves its addresses against
	// graph.ids.json and compares nothing against graph.values.yaml, and a
	// snapshot that disagrees with the layer is the one input this design
	// cannot check. The refresh is a full export — the layer, every form and
	// every node — so it costs far more than the write did, and it is only
	// paid when something was actually written.
	KeepExport bool
	// Concurrency caps the parallel reads the plan makes. Zero uses 8.
	Concurrency int
	// WorkspaceID is the workspace a picture is uploaded into. Empty asks the
	// form the actor belongs to, which costs one read and is right whenever
	// the key and the layer agree — the configured value is for the caller
	// that knows, and it is checked against nothing.
	WorkspaceID string
	// GroupID is the group every record a run creates is shared to. A record
	// is an actor of its form and on no layer, so the share the layer carries
	// never reaches it — without this the people the layer is shared with
	// would read a create in result.json and find nothing on the form. Zero
	// shares nothing.
	GroupID int
}

// ApplyResult reports what a run planned and what it wrote.
type ApplyResult struct {
	Plan *Plan
	// Applied are the actions that reached Simulator, in the order they went.
	Applied []*Action
	// Failed are the writes that were attempted and refused. Everything
	// before the first failure stayed applied — the API has no transaction,
	// so a partial write is reported rather than pretended away.
	Failed []*ActionFailure
	// Export reports the refresh of the sidecars the write made stale, when
	// there was one to refresh.
	Export *ExportResult
	// PictureWarnings are the images an op asked for and the run could not
	// take: a refused fetch, a file too small to be a picture of anything, an
	// image another node in the same run already carries. None of them stops
	// the op — the values are the substance of a write and the face is not —
	// so they are reported rather than raised.
	PictureWarnings []string
	// ShareWarnings are the records the run created and could not share to
	// the group. Same rule as a picture: the record is the substance and it is
	// already there, so a share that failed is reported rather than raised.
	ShareWarnings []string
	// Stamped is how many ops had their node id written into the file before
	// the run, and StampWarnings what could not be stamped.
	Stamped       int
	StampWarnings []string
	// Result is the cumulative tally of everything applied from this ops
	// file, across every run of it, and ResultPath the file it lives in.
	// ResultAdded is this run's contribution — ids the tally had not seen
	// before, which is less than len(Applied) when a run rewrites a node an
	// earlier run already touched.
	Result      *RunResult
	ResultPath  string
	ResultAdded int
}

// touchedTheLayer reports whether anything the run wrote changed the layer.
//
// A created record is an actor of its form and is on no layer, so a run of
// nothing but creates leaves the export exactly as correct as it found it —
// and refreshing it would re-read every node and every form for nothing.
func (r *ApplyResult) touchedTheLayer() bool {
	for _, a := range r.Applied {
		if !a.Create {
			return true
		}
	}
	return false
}

// ActionFailure is one write the API refused.
type ActionFailure struct {
	Action *Action
	Err    error
}

// ApplyOpsFile reads an ops file and applies it, taking the type dictionary
// from the export sitting next to it.
func ApplyOpsFile(ctx context.Context, sim *simulator.Client, path string, opts ApplyOptions) (*ApplyResult, error) {
	ops, err := LoadOps(path)
	if err != nil {
		return nil, err
	}
	if opts.FromDir == "" {
		opts.FromDir = filepath.Dir(path)
	}
	if opts.OpsPath == "" {
		opts.OpsPath = path
	}
	return ApplyOps(ctx, sim, ops, opts)
}

// ApplyOps plans an ops file against the layer as it is now and, unless the
// run is a dry run, writes it.
//
// Every write is a PatchActor: the fields named in the op, and nothing else.
// The ops file is safe to replay — a value the layer already holds is planned
// as satisfied and never sent, so a second run of an unchanged document
// writes nothing at all.
func ApplyOps(ctx context.Context, sim *simulator.Client, ops *OpsFile, opts ApplyOptions) (*ApplyResult, error) {
	layerID, err := applyLayerID(ops, opts)
	if err != nil {
		return nil, err
	}

	idx, err := LoadIndex(ctx, sim, layerID, opts.FromDir, opts.Concurrency)
	if err != nil {
		return nil, err
	}
	plan, err := PlanOps(ctx, sim, idx, ops, opts.Concurrency)
	if err != nil {
		return nil, err
	}

	res := &ApplyResult{Plan: plan}
	switch {
	case opts.DryRun:
		return res, nil
	case !plan.OK() && !opts.Partial:
		return res, fmt.Errorf("graph: %s — nothing written, fix them or run with Partial",
			plural(len(plan.Errors), "op cannot be applied", "ops cannot be applied"))
	}

	if plan.creates() > 0 && opts.OpsPath == "" {
		return res, fmt.Errorf("graph: %s would be created and there is no ops file to stamp their "+
			"ids into — a record is not on the layer, so the stamped id and its `ref:` are the only "+
			"handles on it; apply from a file",
			plural(plan.creates(), "record", "records"))
	}

	// Stamped before the first write, and deliberately: the run is about to
	// change the titles that half these ops are addressed by, and a file
	// stamped afterwards would be stamped only if everything went well.
	if err := stampOps(opts, plan, res); err != nil {
		return res, err
	}

	// One store for the whole run: an image named by two ops is fetched once,
	// uploaded once, and bound to one node only.
	pics := newPictureStore(opts.WorkspaceID)

	var writeErr error
	for _, a := range plan.Actions {
		resolvePicture(ctx, sim, pics, a, res)
		if !a.writes() {
			// Everything this op had to say was the picture, and the picture
			// could not be taken. Sending an empty update would report the
			// node as touched by a run that changed nothing on it.
			continue
		}
		if err := performAction(ctx, sim, a, opts, res); err != nil {
			res.Failed = append(res.Failed, &ActionFailure{Action: a, Err: err})
			if !opts.Partial {
				// Stopping leaves the layer half-written, which is why the
				// error says how far it got: there is nothing to roll back
				// to, and the next run will skip what already landed.
				writeErr = fmt.Errorf("graph: op #%d (%s) failed after %d applied: %w",
					a.OpIndex, a.Path, len(res.Applied), err)
				break
			}
			continue
		}
		res.Applied = append(res.Applied, a)
	}

	// Tallied before the export, and before any failure is reported: what
	// landed, landed, and the record of it must not depend on the rest of
	// the run going well.
	resultErr := recordResult(opts, res)

	// The export is refreshed after a half-written run too: what landed is
	// what the next run must see.
	exportErr := refreshExport(ctx, sim, layerID, idx, opts, res)

	switch {
	case writeErr != nil:
		return res, writeErr
	case len(res.Failed) > 0:
		return res, fmt.Errorf("graph: %s failed, %d applied",
			plural(len(res.Failed), "op", "ops"), len(res.Applied))
	case resultErr != nil:
		// Same reasoning as a failed export: the writes landed, and the
		// honest report says so and still fails.
		return res, resultErr
	case exportErr != nil:
		// The writes landed; saying so and still failing is the honest
		// report, because a stale snapshot is what the next run resolves
		// its addresses against.
		return res, exportErr
	}
	return res, nil
}

// stampOps records the resolved node ids in the ops file.
//
// Failing to write the file stops the run: the ops are about to be applied,
// and an apply whose renames land while the file still addresses the old
// titles is a file that cannot be replayed — which is the one thing the
// journal-free design depends on.
func stampOps(opts ApplyOptions, plan *Plan, res *ApplyResult) error {
	if opts.OpsPath == "" || len(plan.Resolved) == 0 {
		return nil
	}
	stamped, warnings, err := StampOps(opts.OpsPath, plan.Resolved)
	res.Stamped, res.StampWarnings = stamped, warnings
	if err != nil {
		return fmt.Errorf("%w — nothing written: the ops must carry their node ids before a "+
			"rename moves the paths they are addressed by", err)
	}
	return nil
}

// refreshExport rewrites the sidecars a write has just made wrong.
//
// It runs only when the index came from an export directory: that is the
// directory the next ops file will resolve against, and keeping it current is
// the whole reason to rewrite it. An ops file applied without an export beside
// it has nothing to keep in step.
func refreshExport(ctx context.Context, sim *simulator.Client, layerID string, idx *Index, opts ApplyOptions, res *ApplyResult) error {
	if opts.KeepExport || !idx.FromExport || !res.touchedTheLayer() {
		return nil
	}
	export, err := ExportLayer(ctx, sim, ExportOptions{
		LayerID: layerID, Dir: idx.Dir, Concurrency: opts.Concurrency,
	})
	if err != nil {
		return fmt.Errorf("graph: applied %d op(s), but the export in %s is now stale and could not be "+
			"rewritten — run the export again before the next ops file: %w", len(res.Applied), idx.Dir, err)
	}
	res.Export = export
	return nil
}

// performAction carries out one action.
//
// A create is stamped the moment it succeeds, one op at a time, rather than
// with the others before the run: until the uuid is in the file there is
// nothing but `ref:` pointing at an actor that is on no layer, and a run that
// dies after the create must not leave a file that would create it again.
// (`ref:` alone would in fact catch it — the plan reads by ref first — but the
// file is what a person reads, and an unrecorded create is invisible in it.)
func performAction(ctx context.Context, sim *simulator.Client, a *Action, opts ApplyOptions, res *ApplyResult) error {
	if !a.Create {
		return writeUpdate(ctx, sim, a)
	}

	id, err := createAction(ctx, sim, a)
	if id == "" {
		return err
	}
	a.ActorID = id
	if a.Create {
		// Only an actor this run made. The loser of a ref race writes into
		// somebody else's record, and who may see that one is theirs to say.
		shareCreated(ctx, sim, a, opts.GroupID, res)
	}

	stamped, warnings, stampErr := StampOps(opts.OpsPath, map[int]string{a.OpIndex: id})
	res.Stamped += stamped
	res.StampWarnings = append(res.StampWarnings, warnings...)
	if stampErr != nil && err == nil {
		err = fmt.Errorf("created %s as %s, but the id could not be written back into the ops "+
			"file — put it there by hand before the next run: %w", a.Path, id, stampErr)
	}
	return err
}

// shareCreated hands the group the record a run has just made. It fails
// softly for the same reason a picture does: the create is the point of the
// op and it has already landed, and a share nobody can see is worth a line in
// the report, not a run that stops with the record unstamped.
func shareCreated(ctx context.Context, sim *simulator.Client, a *Action, groupID int, res *ApplyResult) {
	if groupID <= 0 {
		return
	}
	if err := sim.ShareWithGroup(ctx, a.ActorID, groupID); err != nil {
		log.Printf("share actor %s (%s) with group %d: %v", a.ActorID, a.Path, groupID, err)
		res.ShareWarnings = append(res.ShareWarnings,
			fmt.Sprintf("%s (actor %s) is not shared with group %d: %v", a.Path, a.ActorID, groupID, err))
	}
}

// resolvePicture copies the action's image into the workspace's storage and
// hands the action the path it is stored under.
//
// A picture that cannot be taken is reported and dropped, and the action goes
// ahead without it. That is the one thing in a run that fails softly, and it
// is deliberate: the values an op writes came out of the page's text and are
// the point of the run, while the face is what makes the node recognisable.
// Losing the second must not cost the first — and an image is fetched from a
// third party, which refuses, rate-limits and moves things for reasons that
// have nothing to do with this layer.
func resolvePicture(ctx context.Context, sim *simulator.Client, pics *pictureStore, a *Action, res *ApplyResult) {
	if a.Picture == "" {
		return
	}
	stored, err := pics.path(ctx, sim, a)
	if err != nil {
		res.PictureWarnings = append(res.PictureWarnings,
			fmt.Sprintf("%s keeps no picture: %v", a.Path, err))
		a.Picture = ""
		return
	}
	a.StoredPicture = stored
}

// writeUpdate sends one node's changes.
func writeUpdate(ctx context.Context, sim *simulator.Client, a *Action) error {
	req := simulator.UpdateActorRequest{
		Data:        a.Data(),
		Title:       a.Rename,
		Description: a.Describe,
		Ref:         a.Ref,
		Picture:     a.StoredPicture,
	}
	if a.FillsHole {
		// The node stops being a placeholder the moment it carries data;
		// leaving the flag set would show a filled node as an empty slot.
		filled := false
		req.Hole = &filled
	}
	_, err := sim.PatchActor(ctx, a.FormID, a.ActorID, req)
	return err
}

// applyLayerID settles which layer a run writes to.
func applyLayerID(ops *OpsFile, opts ApplyOptions) (string, error) {
	switch {
	case ops == nil:
		return "", fmt.Errorf("graph: applying needs an ops file")
	case opts.LayerID != "" && ops.Layer != "" && opts.LayerID != ops.Layer:
		return "", fmt.Errorf("graph: ops file targets layer %s, asked to apply to %s", ops.Layer, opts.LayerID)
	case opts.LayerID != "":
		return opts.LayerID, nil
	case ops.Layer != "":
		return ops.Layer, nil
	}
	return "", fmt.Errorf("graph: no layer to apply to — set `layer:` in the ops file or pass one")
}
