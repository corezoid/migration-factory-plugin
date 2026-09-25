package graph

import (
	"context"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// writeRecordOps writes an ops file into a fresh directory and returns its
// path. A create needs a file: the uuid is stamped into it the moment the
// record exists, and an ops file held only in memory has nowhere to keep it.
func writeRecordOps(t *testing.T, ops string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), OpsFileName)
	if err := os.WriteFile(path, []byte("layer: "+testLayerID+"\n"+ops), 0o644); err != nil {
		t.Fatalf("write ops: %v", err)
	}
	return path
}

// A fact whose type is on the layer but whose node is taken gets a record of
// its own: created as an actor of that form, off the canvas.
func TestApplyOpsCreatesARecordNoRefFinds(t *testing.T) {
	f := newWriteFixture(t)

	opsPath := writeRecordOps(t, `
ops:
  - type: employee
    ref: "emp-koval-olena"
    rename: "Olena Koval"
    describe: Hired from the offer letter
    set:
      full_name: Olena Koval
      confidence: 0.8
`)

	res, err := ApplyOpsFile(context.Background(), f.sim, opsPath, ApplyOptions{})
	if err != nil {
		t.Fatalf("ApplyOpsFile: %v\n%s", err, res.Plan.Render())
	}
	if len(res.Applied) != 1 || len(f.creates) != 1 {
		t.Fatalf("applied %d, created %d: %s", len(res.Applied), len(f.creates), res.Plan.Render())
	}

	create := f.creates[0]
	if create.path != "/actors/actor/701" {
		t.Errorf("created under %q, want the form the type names", create.path)
	}
	if create.query != "" {
		t.Errorf("create carried the query %q — a record is deliberately not placed on a layer", create.query)
	}
	if got, _ := create.body["ref"].(string); got != "emp-koval-olena" {
		t.Errorf("ref = %q, want the business key — it is what a replay finds", got)
	}
	if got, _ := create.body["title"].(string); got != "Olena Koval" {
		t.Errorf("title = %q, want the rename", got)
	}
	data, _ := create.body["data"].(map[string]any)
	if data["full_name"] != "Olena Koval" || data["confidence"] != 0.8 {
		t.Errorf("data = %v, want both fields keyed by their field ids", data)
	}
	if len(f.writes) != 0 {
		t.Errorf("a create also sent %d update(s): %v", len(f.writes), f.writes)
	}

	// The uuid is the only handle on an actor that is on no layer, so it is
	// in the file before the run ends.
	raw, err := os.ReadFile(opsPath)
	if err != nil {
		t.Fatalf("read the stamped file: %v", err)
	}
	if res.Stamped != 1 || !strings.Contains(string(raw),
		"- type: employee\n    id: 00000001-0000-4000-8000-000000000000\n") {
		t.Fatalf("stamped %d, file:\n%s", res.Stamped, raw)
	}
	if !strings.Contains(res.Plan.Render(), "not on the layer") {
		t.Errorf("the plan does not say the record is off the layer:\n%s", res.Plan.Render())
	}
}

// The second run of the same document must not make a second record. The ref
// is what carries that, before and independently of the stamped id.
func TestApplyOpsFindsTheRecordByRefInsteadOfCreatingItTwice(t *testing.T) {
	f := newWriteFixture(t)

	const ops = `
ops:
  - type: employee
    ref: "emp-koval-olena"
    rename: "Olena Koval"
    set:
      full_name: Olena Koval
`
	first := writeRecordOps(t, ops)
	if _, err := ApplyOpsFile(context.Background(), f.sim, first, ApplyOptions{}); err != nil {
		t.Fatalf("first run: %v", err)
	}

	// A file derived again from the same document: same ref, no stamp.
	second := writeRecordOps(t, ops)
	res, err := ApplyOpsFile(context.Background(), f.sim, second, ApplyOptions{})
	if err != nil {
		t.Fatalf("second run: %v\n%s", err, res.Plan.Render())
	}
	if len(f.creates) != 1 {
		t.Errorf("the same document created %d records", len(f.creates))
	}
	if len(res.Applied) != 0 || len(res.Plan.Satisfied) != 1 {
		t.Errorf("second run applied %d, satisfied %d: %s",
			len(res.Applied), len(res.Plan.Satisfied), res.Plan.Render())
	}
}

// An existing record is an ordinary update: the values that differ are
// written, by the actor's own form.
func TestApplyOpsUpdatesTheRecordTheRefFinds(t *testing.T) {
	f := newWriteFixture(t)

	if _, err := ApplyOpsFile(context.Background(), f.sim, writeRecordOps(t, `
ops:
  - type: employee
    ref: "emp-koval-olena"
    rename: "Olena Koval"
    set:
      full_name: Olena Koval
`), ApplyOptions{}); err != nil {
		t.Fatalf("first run: %v", err)
	}
	f.reset()

	res, err := ApplyOpsFile(context.Background(), f.sim, writeRecordOps(t, `
ops:
  - type: employee
    ref: "emp-koval-olena"
    set:
      full_name: Olena Koval
      confidence: 0.6
`), ApplyOptions{})
	if err != nil {
		t.Fatalf("update run: %v\n%s", err, res.Plan.Render())
	}
	if len(f.creates) != 0 {
		t.Fatalf("created %d records for a ref that exists", len(f.creates))
	}
	if len(f.writes) != 1 {
		t.Fatalf("sent %d updates, want one", len(f.writes))
	}
	if f.writes[0].path != "/actors/actor/701/00000001-0000-4000-8000-000000000000" {
		t.Errorf("updated %q, want the record the ref found", f.writes[0].path)
	}
	data, _ := f.writes[0].body["data"].(map[string]any)
	if _, resent := data["full_name"]; resent {
		t.Errorf("the update resent a value the record already holds: %v", data)
	}
	if data["confidence"] != 0.6 {
		t.Errorf("data = %v, want the changed field", data)
	}
}

// Losing a race on the ref is not a second record: the loser writes into the
// actor the winner made.
func TestCreateFallsBackToTheRecordThatWonTheRef(t *testing.T) {
	f := newWriteFixture(t)

	// Somebody else created the record between the plan and the write.
	f.states["00000009-0000-4000-8000-000000000000"] = &actorState{
		title: "Olena Koval", formID: 701, data: `{"full_name":"Olena Koval"}`,
	}
	f.refs["701/emp-koval-olena"] = "00000009-0000-4000-8000-000000000000"
	f.plannedRefMiss = "701/emp-koval-olena"

	res, err := ApplyOpsFile(context.Background(), f.sim, writeRecordOps(t, `
ops:
  - type: employee
    ref: "emp-koval-olena"
    rename: "Olena Koval"
    set:
      full_name: Olena Koval
      confidence: 0.5
`), ApplyOptions{})
	if err != nil {
		t.Fatalf("apply: %v\n%s", err, res.Plan.Render())
	}
	if len(f.writes) != 1 {
		t.Fatalf("sent %d updates, want the write into the winner: %v", len(f.writes), f.writes)
	}
	if !strings.HasSuffix(f.writes[0].path, "00000009-0000-4000-8000-000000000000") {
		t.Errorf("wrote to %q, want the actor that won the ref", f.writes[0].path)
	}
	data, _ := f.writes[0].body["data"].(map[string]any)
	if data["confidence"] != 0.5 {
		t.Errorf("data = %v, want the fields the op carried", data)
	}

	// The record the run wrote into existed before it: an update in the
	// tally, not a create — the winner's uuid under "created" would credit
	// this run with an actor somebody else made.
	if res.Applied[0].Create {
		t.Errorf("the action still reads as a create after writing into the winner")
	}
	if got := res.Result; got.ActorsCreated != 0 || got.ActorsUpdated != 1 ||
		got.Updated[0] != "00000009-0000-4000-8000-000000000000" {
		t.Errorf("tally = %+v, want the winner's uuid under updated and nothing under created", got)
	}
	if res.Stamped != 1 {
		t.Errorf("stamped %d ops, want the winner's uuid stamped into the file all the same", res.Stamped)
	}
}

// A record is an actor of its form and on no layer, so the share the layer
// carries never reaches it: the group the session works in is given the
// record the moment it exists — view and modify, this one actor only.
func TestApplyOpsSharesACreatedRecordWithTheGroup(t *testing.T) {
	f := newWriteFixture(t)

	res, err := ApplyOpsFile(context.Background(), f.sim, writeRecordOps(t, `
ops:
  - type: employee
    ref: "emp-koval-olena"
    set: {full_name: Olena Koval}
`), ApplyOptions{GroupID: 4242})
	if err != nil {
		t.Fatalf("ApplyOpsFile: %v\n%s", err, res.Plan.Render())
	}
	if len(f.shares) != 1 {
		t.Fatalf("shares = %v, want exactly one for the one record", f.shares)
	}

	s := f.shares[0]
	if s.path != "/access_rules/actor/00000001-0000-4000-8000-000000000000" {
		t.Errorf("shared %q, want the record that was just created", s.path)
	}
	// The platform cascades by default; a record has nothing under it to
	// share, and the flag has to say so rather than rely on that.
	if s.query != "recursive=false" {
		t.Errorf("query = %q, want recursive=false", s.query)
	}
	if len(s.rules) != 1 {
		t.Fatalf("rules = %v, want one grant", s.rules)
	}
	if s.rules[0]["action"] != "create" {
		t.Errorf("action = %v, want create", s.rules[0]["action"])
	}
	data, _ := s.rules[0]["data"].(map[string]any)
	if data["groupId"] != float64(4242) {
		t.Errorf("data = %v, want the group id", data)
	}
	privs, _ := data["privs"].(map[string]any)
	if privs["view"] != true || privs["modify"] != true || privs["remove"] != false {
		t.Errorf("privs = %v, want view and modify without remove", privs)
	}
	if len(res.ShareWarnings) != 0 {
		t.Errorf("share warnings = %v, want none for a share that landed", res.ShareWarnings)
	}
}

// The share is the one thing after a create that fails softly: the record is
// the substance and it is already there, so a gateway that refuses the grant
// costs a line in the report, not the op.
func TestApplyOpsKeepsTheRecordWhenTheShareFails(t *testing.T) {
	f := newWriteFixture(t)
	f.shareStatus = http.StatusInternalServerError

	res, err := ApplyOpsFile(context.Background(), f.sim, writeRecordOps(t, `
ops:
  - type: employee
    ref: "emp-koval-olena"
    set: {full_name: Olena Koval}
`), ApplyOptions{GroupID: 4242})
	if err != nil {
		t.Fatalf("a failed share failed the run: %v", err)
	}
	if len(res.Applied) != 1 || len(res.Failed) != 0 || res.Result.ActorsCreated != 1 {
		t.Fatalf("applied %d, failed %d, created %d: want the create to stand",
			len(res.Applied), len(res.Failed), res.Result.ActorsCreated)
	}
	if len(f.shares) != 1 {
		t.Errorf("shares = %d, want one attempt and no retry", len(f.shares))
	}
	if len(res.ShareWarnings) != 1 {
		t.Fatalf("share warnings = %v, want the one record that is not shared", res.ShareWarnings)
	}
	for _, want := range []string{"00000001-0000-4000-8000-000000000000", "4242", "500"} {
		if !strings.Contains(res.ShareWarnings[0], want) {
			t.Errorf("warning %q does not name %s", res.ShareWarnings[0], want)
		}
	}
	if res.Stamped != 1 {
		t.Errorf("stamped %d, want the id in the file regardless of the share", res.Stamped)
	}
}

// No group, no share — and an update is never one: a node on the layer is
// covered by the layer's own share, and the record that won a ref race belongs
// to whoever made it.
func TestApplyOpsSharesOnlyWhatItCreated(t *testing.T) {
	f := newWriteFixture(t)

	if _, err := ApplyOpsFile(context.Background(), f.sim, writeRecordOps(t, `
ops:
  - type: employee
    ref: "emp-koval-olena"
    set: {full_name: Olena Koval}
`), ApplyOptions{}); err != nil {
		t.Fatalf("create without a group: %v", err)
	}
	if len(f.shares) != 0 {
		t.Errorf("shares = %v, want none when no group is configured", f.shares)
	}

	f.apply(t, `
ops:
  - at: "Employee #1@aaaa"
    set: {role_job_title: CEO}
`, ApplyOptions{GroupID: 4242})
	if len(f.shares) != 0 {
		t.Errorf("shares = %v, want none for an update", f.shares)
	}

	f.states["00000009-0000-4000-8000-000000000000"] = &actorState{
		title: "Petro Bondar", formID: 701, data: `{"full_name":"Petro Bondar"}`,
	}
	f.refs["701/emp-bondar-petro"] = "00000009-0000-4000-8000-000000000000"
	f.plannedRefMiss = "701/emp-bondar-petro"
	if _, err := ApplyOpsFile(context.Background(), f.sim, writeRecordOps(t, `
ops:
  - type: employee
    ref: "emp-bondar-petro"
    set: {confidence: 0.5}
`), ApplyOptions{GroupID: 4242}); err != nil {
		t.Fatalf("lost race: %v", err)
	}
	if len(f.shares) != 0 {
		t.Errorf("shares = %v, want none for a record somebody else made", f.shares)
	}
}

// strict mode is the promise never to create — a ref that finds nothing is an
// error rather than a new record.
func TestPlanOpsStrictModeRefusesToCreate(t *testing.T) {
	f := newWriteFixture(t)

	plan := f.plan(t, `
mode: strict
ops:
  - type: employee
    ref: "emp-koval-olena"
    set: {full_name: Olena Koval}
`)
	if got := plan.errorFor(1); !strings.Contains(got, "never creates") {
		t.Errorf("error = %q, want strict mode to refuse", got)
	}
	if len(f.creates) != 0 {
		t.Errorf("strict mode created %d records", len(f.creates))
	}
}

func TestPlanOpsRejectsMalformedRecordOps(t *testing.T) {
	f := newWriteFixture(t)

	plan := f.plan(t, `
ops:
  - type: employee
    set: {full_name: X}
  - type: no_such_type
    ref: "x-1"
    set: {full_name: X}
  - at: "Employee #1@aaaa"
    type: employee
    ref: "x-2"
    set: {full_name: X}
  - type: employee
    ref: "x-3"
    set: {ful_name: X}
  - type: employee
    ref: "x-4"
    set: {full_name: X}
  - type: employee
    ref: "x-4"
    set: {full_name: Y}
`)

	for n, want := range map[int]string{
		1: "`type:` needs a `ref:`",
		2: `no type "no_such_type"`,
		3: "both `at:` and `type:`",
		4: `did you mean "full_name"`,
		6: "op #5 already claims ref",
	} {
		if got := plan.errorFor(n); !strings.Contains(got, want) {
			t.Errorf("op #%d error = %q, want it to mention %q", n, got, want)
		}
	}
	// Op #5 is the only well-formed one, and it is a create.
	if len(plan.Actions) != 1 || !plan.Actions[0].Create {
		t.Errorf("planned %d actions: %s", len(plan.Actions), plan.Render())
	}
}

// A record is on no layer, so making one leaves the export beside the ops file
// exactly as correct as it was — and a refresh re-reads every node and every
// form of the layer for nothing.
func TestApplyOpsKeepsTheExportWhenOnlyRecordsWereCreated(t *testing.T) {
	f := newWriteFixture(t)
	dir := t.TempDir()

	write := func(name, body string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o600); err != nil {
			t.Fatalf("write %s: %v", name, err)
		}
	}
	write(TypesFileName, `
types:
  employee:
    formId: 701
    fields:
      full_name: {id: full_name, type: string}
`)
	write(IDsFileName, `{"layer":"`+testLayerID+`","sep":" > ","paths":{
		"ACME": "`+fixtureCompany+`"}}`)
	write(OpsFileName, `
layer: `+testLayerID+`
ops:
  - type: employee
    ref: "emp-koval-olena"
    rename: "Olena Koval"
    set: {full_name: Olena Koval}
`)

	res, err := ApplyOpsFile(context.Background(), f.sim, filepath.Join(dir, OpsFileName), ApplyOptions{})
	if err != nil {
		t.Fatalf("ApplyOpsFile: %v", err)
	}
	if len(res.Applied) != 1 {
		t.Fatalf("applied %d: %s", len(res.Applied), res.Plan.Render())
	}
	if res.Export != nil {
		t.Errorf("the export was rewritten for a record that is on no layer")
	}
	if f.layerReads != 0 {
		t.Errorf("read the layer %d times — the sidecars answer every address here", f.layerReads)
	}
}

// There is nowhere to record the uuid of an off-canvas actor when the ops
// were never a file, so the run refuses before it makes one.
func TestApplyOpsRefusesToCreateWithoutAnOpsFile(t *testing.T) {
	f := newWriteFixture(t)

	res := f.apply(t, `
ops:
  - type: employee
    ref: "emp-koval-olena"
    set: {full_name: Olena Koval}
`, ApplyOptions{})

	if len(f.creates) != 0 {
		t.Errorf("created %d records with no file to stamp", len(f.creates))
	}
	if len(res.Applied) != 0 {
		t.Errorf("applied %d actions", len(res.Applied))
	}
}

// A record made by hand in Simulator has no ref, so nothing a ref lookup does
// can find it — and creating "the one that is missing" would put a second copy
// of it in the form. `find_records` reports such a record by uuid, and an op
// carrying that uuid in `id:` updates it instead. This is the one `id:` a
// writer puts in an ops file by hand, so it has to work.
func TestApplyOpsUpdatesARefLessRecordAddressedByID(t *testing.T) {
	f := newWriteFixture(t)

	// The register holds it; the ref index does not.
	const found = "00000042-0000-4000-8000-000000000000"
	f.states[found] = &actorState{
		title: "Olena Koval", formID: 701, data: `{"full_name":"Olena Koval"}`,
	}

	res, err := ApplyOpsFile(context.Background(), f.sim, writeRecordOps(t, `
ops:
  - type: employee
    ref: "emp-koval-olena"
    id: `+found+`
    set:
      confidence: 0.8
`), ApplyOptions{})
	if err != nil {
		t.Fatalf("apply: %v\n%s", err, res.Plan.Render())
	}
	if len(f.creates) != 0 {
		t.Fatalf("created %d record(s), want the existing one updated: %v", len(f.creates), f.creates)
	}
	if len(f.writes) != 1 || !strings.HasSuffix(f.writes[0].path, found) {
		t.Fatalf("writes = %v, want one into %s", f.writes, found)
	}
	if data, _ := f.writes[0].body["data"].(map[string]any); data["confidence"] != 0.8 {
		t.Errorf("data = %v, want the op's fields", data)
	}
	if res.Result.ActorsCreated != 0 || res.Result.ActorsUpdated != 1 {
		t.Errorf("tally = %+v, want one update and no create", res.Result)
	}
}

// The `id:` a writer puts in by hand is good for one file. The op's `ref:` is
// written into the record at the same time, so the next file — derived again
// from the document, with no id in it — finds the record by ref instead of
// making a second one.
func TestApplyOpsGivesARefLessRecordItsRef(t *testing.T) {
	f := newWriteFixture(t)

	const found = "00000042-0000-4000-8000-000000000000"
	f.states[found] = &actorState{
		title: "Olena Koval", formID: 701, data: `{"full_name":"Olena Koval"}`,
	}

	res, err := ApplyOpsFile(context.Background(), f.sim, writeRecordOps(t, `
ops:
  - type: employee
    ref: "emp-koval-olena"
    id: `+found+`
    set:
      confidence: 0.8
`), ApplyOptions{})
	if err != nil {
		t.Fatalf("first file: %v\n%s", err, res.Plan.Render())
	}
	if len(f.writes) != 1 {
		t.Fatalf("writes = %v, want one into %s", f.writes, found)
	}
	if got, _ := f.writes[0].body["ref"].(string); got != "emp-koval-olena" {
		t.Errorf("the update carried ref %q, want the op's — it is the only handle the next file has", got)
	}
	if !strings.Contains(res.Plan.Render(), `ref: — -> "emp-koval-olena"`) {
		t.Errorf("the plan does not show the ref being set:\n%s", res.Plan.Render())
	}
	f.reset()

	// The same document, read again: same ref, no id.
	res, err = ApplyOpsFile(context.Background(), f.sim, writeRecordOps(t, `
ops:
  - type: employee
    ref: "emp-koval-olena"
    set:
      confidence: 0.9
`), ApplyOptions{})
	if err != nil {
		t.Fatalf("second file: %v\n%s", err, res.Plan.Render())
	}
	if len(f.creates) != 0 {
		t.Fatalf("the second file created %d record(s) for a ref the first one wrote: %v", len(f.creates), f.creates)
	}
	if len(f.writes) != 1 || !strings.HasSuffix(f.writes[0].path, found) {
		t.Fatalf("writes = %v, want one into the record the ref now finds", f.writes)
	}
	if _, resent := f.writes[0].body["ref"]; resent {
		t.Errorf("the ref was written again into a record that already carries it: %v", f.writes[0].body)
	}
}

// A record that already answers to its ref is left alone when nothing else
// changes — the ref is not a reason to write on every replay.
func TestPlanOpsDoesNotRewriteARefTheRecordHas(t *testing.T) {
	f := newWriteFixture(t)

	first := writeRecordOps(t, `
ops:
  - type: employee
    ref: "emp-koval-olena"
    set: {full_name: Olena Koval}
`)
	if _, err := ApplyOpsFile(context.Background(), f.sim, first, ApplyOptions{}); err != nil {
		t.Fatalf("create: %v", err)
	}

	// The file as it reads after the run: stamped, and nothing to change.
	res, err := ApplyOpsFile(context.Background(), f.sim, first, ApplyOptions{})
	if err != nil {
		t.Fatalf("replay: %v\n%s", err, res.Plan.Render())
	}
	if len(res.Applied) != 0 || len(res.Plan.Satisfied) != 1 {
		t.Errorf("replay applied %d, satisfied %d:\n%s", len(res.Applied), len(res.Plan.Satisfied), res.Plan.Render())
	}
}
