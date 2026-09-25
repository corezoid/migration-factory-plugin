package graph

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"migration-factory-plugin-mcp/internal/simulator"
)

// This file holds the second way an op addresses its subject: by type and
// business ref rather than by a path on the layer.
//
// It is what a document does with a fact that has nowhere to go — the layer
// carries one node of the right type and it already holds a different record.
// The record is created as an actor of its form and is NOT placed on the
// canvas: putting it there needs the edge to its parent as well, and that
// endpoint is not wired. So the new actor lives in the form's records, where
// `ref:` and the uuid stamped back into the ops file are the only handles on
// it — it will not appear in the next graph.values.yaml.

// recordPath is what the plan and the errors call a record addressed by ref.
// It is deliberately not a layer path: the actor is not on the layer, and a
// reader who goes looking for one on the canvas would not find it.
func recordPath(ref string) string { return "ref:" + ref }

// recordKey identifies a record within one ops file — a ref is unique per
// form, so two ops may carry the same ref only if their types differ.
func recordKey(formID int, ref string) string { return strconv.Itoa(formID) + "/" + ref }

// resolveRecordOp validates a `type:` op and returns the type it names.
func resolveRecordOp(idx *Index, op Op) (*Type, error) {
	if err := unsupportedVocabulary(op); err != nil {
		return nil, err
	}
	switch {
	case op.At != "":
		return nil, fmt.Errorf("op has both `at:` and `type:` — `at:` writes to a node on the " +
			"layer, `type:` owns a record of its own; pick one")
	case strings.TrimSpace(op.Ref) == "":
		return nil, fmt.Errorf("`type:` needs a `ref:`: the business key is what stops a replay of " +
			"this file — or of the same document — from creating the record a second time")
	case len(op.Set) == 0 && op.Rename == "" && op.Describe == "" && op.Picture == "":
		return nil, fmt.Errorf("op does nothing: no `set:`, `rename:`, `describe:` or `picture:`")
	}

	t, ok := idx.Types.BySlug(op.Type)
	if !ok {
		return nil, fmt.Errorf("no type %q in %s — the slug is the name in [square brackets] in "+
			"graph.values.yaml; the layer has %s", op.Type, idx.TypesFrom, strings.Join(idx.Types.Slugs(), ", "))
	}
	if len(t.Fields) == 0 {
		return nil, fmt.Errorf("type %q has no field schema in %s — re-export, or the write would "+
			"go against a field nobody can check", t.Slug, idx.TypesFrom)
	}
	return t, nil
}

// findRecord reads the record an op owns, or reports that it does not exist
// yet by returning a nil actor.
//
// A stamped op is read by its uuid: the record was created by an earlier run,
// and the uuid is the address that survives a `rename:` — which a ref would
// also survive, but the uuid is what every other op in the file is addressed
// by once applied.
func findRecord(ctx context.Context, sim *simulator.Client, t *Type, op Op) (*simulator.Actor, error) {
	if op.ID != "" {
		actor, err := sim.GetActor(ctx, op.ID, actorStateFilter)
		if simulator.IsNotFound(err) {
			// The stamped record is gone — deleted in Simulator since the
			// last run. Creating a replacement silently would resurrect a
			// record somebody deleted on purpose.
			return nil, fmt.Errorf("the record stamped as %s is gone from Simulator; drop the `id:` "+
				"from this op to create it again, or drop the op", op.ID)
		}
		return actor, err
	}

	actor, err := sim.GetActorByRef(ctx, t.FormID, op.Ref, actorStateFilter)
	switch {
	case simulator.IsNotFound(err):
		return nil, nil
	case err != nil:
		return nil, fmt.Errorf("read %s by ref: %w", recordPath(op.Ref), err)
	}
	return actor, nil
}

// planCreate builds the action for a record that does not exist yet.
//
// Every field is a write, because there is nothing stored to compare against —
// which is also why a create can never come back satisfied, and why it is the
// one action in a plan that is not an overwrite.
func planCreate(pc *planContext, op Op, n int, t *Type) (*Action, error) {
	action := &Action{
		OpIndex:  n,
		Path:     recordPath(op.Ref),
		FormID:   t.FormID,
		Type:     t.Slug,
		Create:   true,
		Ref:      op.Ref,
		Rename:   op.Rename,
		Describe: op.Describe,
		Picture:  op.Picture,
	}
	// Nothing is stored to leave alone, so a create's picture is never the
	// overwrite planOp guards against — only the address has to hold up.
	if action.Picture != "" {
		if err := validatePictureURL(action.Picture); err != nil {
			return nil, fmt.Errorf("%s: %w", action.Path, err)
		}
	}

	// An empty actor as the left-hand side: storageKey then returns the plain
	// field id, which is the right key for a record that carries no other
	// form's fields yet, and every value reads as a change.
	changes, err := planSet(pc, op, t, &simulator.Actor{}, action.Path)
	if err != nil {
		return nil, err
	}
	action.Changes = changes
	return action, nil
}

// createAction creates the record an action owns and returns its uuid.
//
// A ref is unique per form, so two runs racing on the same document collide
// here rather than silently ending up with two records: the loser reads the
// winner's actor back and writes its own fields into it.
func createAction(ctx context.Context, sim *simulator.Client, a *Action) (string, error) {
	actor, err := sim.CreateActor(ctx, a.FormID, simulator.CreateActorRequest{
		Data:        a.Data(),
		Title:       a.Rename,
		Description: a.Describe,
		Ref:         a.Ref,
		Picture:     a.StoredPicture,
	})
	if err == nil {
		return actor.ID, nil
	}
	if !simulator.IsConflict(err) && !simulator.IsBadRequest(err) {
		return "", err
	}

	existing, refErr := sim.GetActorByRef(ctx, a.FormID, a.Ref, actorStateFilter)
	if refErr != nil {
		// Not a lost race after all — report what the create said, since
		// that is the failure the writer has to fix.
		return "", err
	}

	// Write into the actor that won. Both of these come from the record
	// itself rather than from the type: the form to address it by is the leaf
	// form on a multiform actor, and the keys its values are stored under
	// were planned against an empty actor, which cannot know that shape.
	//
	// From here the action is an update of a record somebody else made, and
	// it is tallied and reported as one: the winner's uuid under "created"
	// would credit this run with an actor it did not bring into being.
	a.Create = false
	a.ActorID = existing.ID
	a.FormID = existing.ResolvedFormID()
	if strings.TrimSpace(existing.Picture) != "" {
		// The record that won the race already has a face. The rule for an
		// update is the rule here too: a picture is filled, never replaced.
		a.StoredPicture = ""
	}
	for i := range a.Changes {
		a.Changes[i].Key = storageKey(existing.Data, a.Changes[i].Key)
	}
	if patchErr := writeUpdate(ctx, sim, a); patchErr != nil {
		return existing.ID, fmt.Errorf("%s already existed and writing to it failed: %w", recordPath(a.Ref), patchErr)
	}
	return existing.ID, nil
}
