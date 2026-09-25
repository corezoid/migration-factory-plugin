package graph

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// writeOpsIn writes an ops file into dir under the given name and returns its
// path. The tally lives beside the ops file, so tests that replay a document
// keep every run in one directory.
func writeOpsIn(t *testing.T, dir, name, ops string) string {
	t.Helper()
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, []byte("layer: "+testLayerID+"\n"+ops), 0o644); err != nil {
		t.Fatalf("write %s: %v", name, err)
	}
	return path
}

// readResult decodes the tally, failing the test when there is none.
func readResult(t *testing.T, dir string) *RunResult {
	t.Helper()
	res := &RunResult{}
	data, err := os.ReadFile(filepath.Join(dir, ResultFileName))
	if err != nil {
		t.Fatalf("read %s: %v", ResultFileName, err)
	}
	if err := json.Unmarshal(data, res); err != nil {
		t.Fatalf("%s is not JSON: %v", ResultFileName, err)
	}
	return res
}

// A write leaves a tally beside the ops file, with each applied node in
// exactly one of the three lists.
func TestApplyOpsRecordsWhatItWrote(t *testing.T) {
	f := newWriteFixture(t)
	f.states[fixtureDocs] = &actorState{title: "Docs", formID: 702, hole: true, data: `{}`}

	dir := t.TempDir()
	opsPath := writeOpsIn(t, dir, OpsFileName, `
ops:
  - at: "ACME"
    set: {name: ACME Holding}
  - at: "ACME > Docs"
    rename: Documents
  - type: employee
    ref: "emp-koval-olena"
    set: {full_name: Olena Koval}
`)

	res, err := ApplyOpsFile(context.Background(), f.sim, opsPath, ApplyOptions{})
	if err != nil {
		t.Fatalf("ApplyOpsFile: %v\n%s", err, res.Plan.Render())
	}
	if len(res.Applied) != 3 {
		t.Fatalf("applied %d, want 3:\n%s", len(res.Applied), res.Plan.Render())
	}

	got := readResult(t, dir)
	if got.HolesFilled != 1 || got.ActorsUpdated != 1 || got.ActorsCreated != 1 {
		t.Errorf("counts = %d holes, %d updated, %d created; want one of each",
			got.HolesFilled, got.ActorsUpdated, got.ActorsCreated)
	}
	if len(got.Holes) != 1 || got.Holes[0] != fixtureDocs {
		t.Errorf("holes = %v, want [%s]", got.Holes, fixtureDocs)
	}
	if len(got.Updated) != 1 || got.Updated[0] != fixtureCompany {
		t.Errorf("updated = %v, want [%s]", got.Updated, fixtureCompany)
	}
	created := res.Applied[2].ActorID
	if len(got.Created) != 1 || got.Created[0] != created {
		t.Errorf("created = %v, want [%s] — the uuid the record was minted as", got.Created, created)
	}
	if res.ResultAdded != 3 {
		t.Errorf("ResultAdded = %d, want 3", res.ResultAdded)
	}
}

// The tally is read by its Russian keys, so they are part of the contract and
// not an implementation detail of the struct.
func TestResultFileKeys(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ResultFileName)
	if err := WriteResult(path, &RunResult{Holes: []string{"a"}}); err != nil {
		t.Fatalf("WriteResult: %v", err)
	}

	raw := map[string]any{}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read: %v", err)
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		t.Fatalf("not JSON: %v", err)
	}
	for _, key := range []string{
		"количество заполненных дырок", "количество обновленных акторов", "количество созданных акторов",
		"заполненные дырки", "обновленные акторы", "созданные акторы",
	} {
		if _, ok := raw[key]; !ok {
			t.Errorf("%s is missing from %s", key, string(data))
		}
	}
	// An empty run reads as an empty list, not as null.
	if list, ok := raw["созданные акторы"].([]any); !ok || len(list) != 0 {
		t.Errorf("созданные акторы = %v, want []", raw["созданные акторы"])
	}
}

// The model applies the same file more than once — after a partial run, after
// an edit, or just to be sure. Each node is counted the first time it is
// written and never again, so the totals stay the document's rather than the
// last call's.
func TestApplyOpsDoesNotCountTheSameNodeTwice(t *testing.T) {
	f := newWriteFixture(t)

	dir := t.TempDir()
	ops := `
ops:
  - at: "ACME"
    set: {name: ACME Holding}
`
	first := writeOpsIn(t, dir, OpsFileName, ops)
	if _, err := ApplyOpsFile(context.Background(), f.sim, first, ApplyOptions{}); err != nil {
		t.Fatalf("first apply: %v", err)
	}

	// The fixture does not fold a write back into what it serves, so the
	// second run writes the node again — which is exactly the case the tally
	// has to survive.
	second := writeOpsIn(t, dir, "graph.ops.again.yaml", ops)
	res, err := ApplyOpsFile(context.Background(), f.sim, second, ApplyOptions{})
	if err != nil {
		t.Fatalf("second apply: %v", err)
	}
	if len(res.Applied) != 1 {
		t.Fatalf("the second run applied %d, want it to write again", len(res.Applied))
	}
	if res.ResultAdded != 0 {
		t.Errorf("ResultAdded = %d, want 0 — the node was already in the tally", res.ResultAdded)
	}

	got := readResult(t, dir)
	if got.ActorsUpdated != 1 || len(got.Updated) != 1 {
		t.Errorf("updated = %d %v after two runs, want the node counted once", got.ActorsUpdated, got.Updated)
	}
}

// A hole stops being a hole the moment it is filled. Writing to it again is
// an update of a real node, but the tally keeps it where it was first
// counted — otherwise the same node would show up as both a filled hole and
// an updated actor.
func TestApplyOpsKeepsAFilledHoleOutOfTheUpdates(t *testing.T) {
	f := newWriteFixture(t)
	f.states[fixtureDocs] = &actorState{title: "Docs", formID: 702, hole: true, data: `{}`}

	dir := t.TempDir()
	first := writeOpsIn(t, dir, OpsFileName, `
ops:
  - at: "ACME > Docs"
    rename: Documents
`)
	if _, err := ApplyOpsFile(context.Background(), f.sim, first, ApplyOptions{}); err != nil {
		t.Fatalf("first apply: %v", err)
	}

	// The node as it reads after the fill: on the canvas, no longer a slot.
	f.states[fixtureDocs].hole = false
	f.renameFixtureNode(fixtureDocs, "Documents")
	second := writeOpsIn(t, dir, "graph.ops.again.yaml", `
ops:
  - at: "ACME > Documents"
    describe: The paperwork
`)
	if _, err := ApplyOpsFile(context.Background(), f.sim, second, ApplyOptions{}); err != nil {
		t.Fatalf("second apply: %v", err)
	}

	got := readResult(t, dir)
	if got.HolesFilled != 1 || got.ActorsUpdated != 0 {
		t.Errorf("counts = %d holes, %d updated (%v); want the node counted once, as a filled hole",
			got.HolesFilled, got.ActorsUpdated, got.Updated)
	}
}

// A dry run writes nothing at all, the tally included.
func TestApplyOpsDryRunLeavesNoResultFile(t *testing.T) {
	f := newWriteFixture(t)

	dir := t.TempDir()
	opsPath := writeOpsIn(t, dir, OpsFileName, `
ops:
  - at: "ACME"
    set: {name: ACME Holding}
`)
	if _, err := ApplyOpsFile(context.Background(), f.sim, opsPath, ApplyOptions{DryRun: true}); err != nil {
		t.Fatalf("ApplyOpsFile: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, ResultFileName)); !os.IsNotExist(err) {
		t.Errorf("a dry run left %s behind (%v)", ResultFileName, err)
	}
}

// A hand-edited tally is still readable: the lists are the record and the
// counts follow them.
func TestLoadResultRecountsFromTheLists(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, ResultFileName)
	if err := os.WriteFile(path, []byte(`{
		"количество заполненных дырок": 99,
		"заполненные дырки": ["a", "b"]
	}`), 0o600); err != nil {
		t.Fatalf("write: %v", err)
	}

	got, err := LoadResult(path)
	if err != nil {
		t.Fatalf("LoadResult: %v", err)
	}
	if got.HolesFilled != 2 {
		t.Errorf("HolesFilled = %d, want the length of the list", got.HolesFilled)
	}
}
