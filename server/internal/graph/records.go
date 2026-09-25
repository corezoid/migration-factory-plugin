package graph

import (
	"context"
	"fmt"
	"sort"
	"strings"

	"migration-factory-plugin-mcp/internal/simulator"
)

// This file answers one question, per value, before anything is created: is
// there already a record of this type carrying this identity?
//
// It exists because the canvas cannot answer it. A layer holds the nodes
// somebody placed on it — typically one empty placeholder per type per branch
// — while the form behind the type holds every record: the ones earlier runs
// created off the canvas, the ones other documents brought in, the ones a
// person typed into Simulator. Routing against the canvas alone is how the
// same counterparty ends up in the register three times under three refs.
//
// The answer is returned, not written to a file. An earlier version of this
// dumped a form's records into JSON for the caller to search, and the caller
// searched it wrong — silently, reading "no match" out of its own bug and
// then creating fifteen records without having checked any of them. A verdict
// the tool computes cannot be misread that way.

// probeTitle names the probe that is not a field of the form but the actor's
// own title. It is also the name a caller passes in Fields to ask for it
// explicitly, since every actor has one whatever its type is.
const probeTitle = "title"

// identityMarker is how a form says a field identifies the record. The field
// titles in these twins are self-documenting ("… — STRONG identity key — …"),
// and that phrase is the only machine-readable part of the convention.
const identityMarker = "identity key"

// maxProbeValues caps one call. It guards against a prompt pasting a whole
// ledger in, not against cost: one value is a handful of small queries.
const maxProbeValues = 64

// probePageLimit bounds one probe. A value matching more records than this is
// ambiguous long before the cap, and the point here is the verdict, not a
// listing.
const probePageLimit = 10

// FindRecordsOptions configure FindRecords.
type FindRecordsOptions struct {
	// Dir holds the export whose types.schema.yaml resolves the slug. Empty
	// means the current directory.
	Dir string
	// Type is the slug to look in — the name in [brackets] in
	// graph.values.yaml.
	Type string
	// Fields are the fields to probe, in order, and "title" asks for the
	// actor's own title. Empty probes every field the type marks as an
	// identity key, then the title.
	//
	// A type marks its keys and this does not second-guess them: which field
	// identifies a record is a property of the form, not of this code. Name
	// the fields here when a source identifies its subjects by something the
	// form does not mark — an account number, a document number — and read
	// types.schema.yaml to find out what the type actually has.
	Fields []string
	// Values are the identities to check, one per record about to be written.
	Values []string
	// Concurrency caps the parallel probes. Zero uses the package default.
	Concurrency int
}

// FoundRecord is one record a probe matched.
type FoundRecord struct {
	ID string
	// Ref is the business key an ops file addresses the record by. Empty
	// means the record was made by hand and no `ref:` lookup reaches it — an
	// op has to carry `id:` instead.
	Ref    string
	Title  string
	Fields map[string]any
}

// ValueCheck is the verdict for one value.
type ValueCheck struct {
	Value string
	// MatchedBy is the field the hit came from, or probeTitle for the title
	// fallback. Empty when nothing matched.
	MatchedBy string
	Records   []FoundRecord
	// Similar are records the title search returned that are not this value:
	// the gateway matches a substring, so "Alfa" brings back "Alfa-Bank JSC"
	// and "Alfa Insurance". They are shown so the writer can see a near-miss,
	// and they are not a find — FOUND means "write here", and writing a new
	// subject's facts into a record that merely contains its name is the one
	// merge this probe exists to prevent.
	Similar []FoundRecord
	// Err is a probe that could not be run. Such a value is reported as
	// unknown rather than as absent: "no record" is the answer that leads
	// straight to a create.
	Err error
}

// Found reports whether the value matched anything.
func (c ValueCheck) Found() bool { return len(c.Records) > 0 }

// FindResult reports one call.
type FindResult struct {
	Type      string
	FormID    int
	FormTitle string
	// Probes are the names tried, in order, as a caller would pass them in
	// Fields.
	Probes []string
	Checks []ValueCheck
	// Warnings are what the call worked around: a type marking no identity
	// key, a workspace it could not resolve.
	Warnings []string
}

// Tally splits the checks the way a writer reads them.
func (r *FindResult) Tally() (found, missing, failed int) {
	for _, c := range r.Checks {
		switch {
		case c.Err != nil:
			failed++
		case c.Found():
			found++
		default:
			missing++
		}
	}
	return found, missing, failed
}

// FindRecords checks each value against the records of one type's form.
//
// A value is probed field by field in the order the fields were given — the
// ones the caller named, or the ones the type marks as identity keys — and
// the first probe that matches ends that value's search. The gateway's field
// query is exact and case-sensitive, so a value shaped like an identifier is
// also tried uppercased and stripped of spaces: that is how an account number
// copied out of a document differs from the same number stored.
//
// The title probe is a case-insensitive substring match and it runs last. It
// is what catches a record whose identity field was never filled, which is
// common enough to be the default: a source that identifies its subjects by
// something the form does not mark as a key leaves every record it created
// findable by name and by nothing else.
func FindRecords(ctx context.Context, sim *simulator.Client, opts FindRecordsOptions) (*FindResult, error) {
	if sim == nil {
		return nil, fmt.Errorf("graph: FindRecords needs a simulator client")
	}
	slug := strings.TrimSpace(opts.Type)
	if slug == "" {
		return nil, fmt.Errorf("graph: FindRecords needs a type slug")
	}

	values, err := cleanProbeValues(opts.Values)
	if err != nil {
		return nil, err
	}

	types, found, err := LoadTypesSchema(opts.Dir)
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, fmt.Errorf("no %s in %s — export the layer first: the slug is resolved against "+
			"the export, so a check and an ops file name the same types", TypesFileName, dirOrCwd(opts.Dir))
	}
	t, ok := types.BySlug(slug)
	if !ok {
		return nil, fmt.Errorf("no type %q in %s — the slug is the name in [square brackets] in "+
			"%s; this export has %s", slug, TypesFileName, ValuesFileName, strings.Join(types.Slugs(), ", "))
	}

	fields, withTitle, warnings, err := probeFields(t, opts.Fields)
	if err != nil {
		return nil, err
	}

	workspace, warning := formWorkspace(ctx, sim, t)
	if warning != "" {
		warnings = append(warnings, warning)
	}

	res := &FindResult{
		Type:      t.Slug,
		FormID:    t.FormID,
		FormTitle: t.FormTitle,
		Probes:    fieldNames(fields),
		Checks:    make([]ValueCheck, len(values)),
		Warnings:  warnings,
	}
	if withTitle {
		res.Probes = append(res.Probes, probeTitle)
	}

	parallel(len(values), opts.Concurrency, func(i int) {
		res.Checks[i] = probeValue(ctx, sim, t, fields, workspace, values[i], withTitle)
	})
	return res, nil
}

// cleanProbeValues trims and deduplicates the values, and refuses an empty or
// oversized list.
func cleanProbeValues(raw []string) ([]string, error) {
	out := make([]string, 0, len(raw))
	seen := make(map[string]bool, len(raw))
	for _, v := range raw {
		v = strings.TrimSpace(v)
		if v == "" || seen[v] {
			continue
		}
		seen[v] = true
		out = append(out, v)
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("graph: nothing to check — pass `values` with the identity of each " +
			"record about to be written: a registration number, an IBAN, a name")
	}
	if len(out) > maxProbeValues {
		return nil, fmt.Errorf("graph: %d values in one call, the limit is %d — check the subjects of "+
			"one document, not a whole ledger", len(out), maxProbeValues)
	}
	return out, nil
}

// probeFields settles what a check probes, in order, and whether the title is
// among it.
//
// Named fields are taken as given — the caller has read the type and knows
// which of its fields a register would hold this subject by. With none named,
// the fields the type itself marks as identity keys are the only defensible
// default: no field name is special to this code, and a guess about which one
// is unique would be a guess about somebody else's ontology.
func probeFields(t *Type, named []string) ([]*FieldSpec, bool, []string, error) {
	if len(named) > 0 {
		var (
			fields    []*FieldSpec
			warnings  []string
			withTitle bool
		)
		for _, name := range named {
			name = strings.TrimSpace(name)
			switch {
			case name == "":
				continue
			case name == probeTitle:
				// "title" is the actor's own title, as the tool promises —
				// even when the type happens to carry a field of that name,
				// which is then reachable only by the type's other fields.
				if fieldByName(t, probeTitle) != nil {
					warnings = append(warnings, fmt.Sprintf("%q names the actor title; type %q also has a field called %q, which this probe does not read", probeTitle, t.Slug, probeTitle))
				}
				withTitle = true
				continue
			}
			f := fieldByName(t, name)
			if f == nil {
				return nil, false, nil, fmt.Errorf("type %q has no field %q — its fields are %s "+
					"(or %q for the actor's own title)", t.Slug, name,
					strings.Join(fieldNames(t.Fields), ", "), probeTitle)
			}
			if containsField(fields, f) {
				continue
			}
			if !isIdentityField(f) {
				warnings = append(warnings, fmt.Sprintf("field %q is not marked an identity key in %q, "+
					"so two different records may legitimately carry the same value", f.Name, t.Slug))
			}
			fields = append(fields, f)
		}
		if len(fields) == 0 && !withTitle {
			return nil, false, nil, fmt.Errorf("no field left to probe on %q — `fields` named nothing "+
				"the type has", t.Slug)
		}
		return fields, withTitle, warnings, nil
	}

	var fields []*FieldSpec
	for _, f := range t.Fields {
		if isIdentityField(f) {
			fields = append(fields, f)
		}
	}
	var warnings []string
	if len(fields) == 0 {
		warnings = append(warnings, fmt.Sprintf("type %q marks no field as an identity key, so this "+
			"checked the title alone — name the fields that identify a record of this type in "+
			"`fields`, reading %s for what it has", t.Slug, TypesFileName))
	}
	return fields, true, warnings, nil
}

func isIdentityField(f *FieldSpec) bool {
	return strings.Contains(strings.ToLower(f.Title), identityMarker)
}

func containsField(fields []*FieldSpec, want *FieldSpec) bool {
	for _, f := range fields {
		if f.ID == want.ID {
			return true
		}
	}
	return false
}

func fieldByName(t *Type, name string) *FieldSpec {
	for _, f := range t.Fields {
		if f.Name == name {
			return f
		}
	}
	return nil
}

func fieldNames(fields []*FieldSpec) []string {
	names := make([]string, 0, len(fields))
	for _, f := range fields {
		names = append(names, f.Name)
	}
	return names
}

// probeValue runs one value's probes in order and stops at the first hit.
func probeValue(ctx context.Context, sim *simulator.Client, t *Type, fields []*FieldSpec,
	workspace, value string, withTitle bool) ValueCheck {

	check := ValueCheck{Value: value}

	for _, f := range fields {
		for _, candidate := range queryCandidates(value) {
			actors, err := sim.ListActors(ctx, t.FormID, simulator.ListActorsOptions{
				WorkspaceID: workspace,
				Filter:      simulator.ActorListFilter,
				Query:       f.ID + "=" + candidate,
				Limit:       probePageLimit,
			})
			if err != nil {
				check.Err = fmt.Errorf("probe %s=%s: %w", f.Name, candidate, err)
				return check
			}
			if len(actors.Items) > 0 {
				check.MatchedBy = f.Name
				check.Records = buildRecords(actors.Items, t, fields)
				return check
			}
		}
	}

	if !withTitle {
		return check
	}
	actors, err := sim.ListActors(ctx, t.FormID, simulator.ListActorsOptions{
		WorkspaceID: workspace,
		Filter:      simulator.ActorListFilter,
		Search:      value,
		Limit:       probePageLimit,
	})
	if err != nil {
		check.Err = fmt.Errorf("probe title~%s: %w", value, err)
		return check
	}
	var exact, similar []simulator.Actor
	for _, a := range actors.Items {
		if sameTitle(a.Title, value) {
			exact = append(exact, a)
		} else {
			similar = append(similar, a)
		}
	}
	if len(exact) > 0 {
		check.MatchedBy = probeTitle
		check.Records = buildRecords(exact, t, fields)
	}
	check.Similar = buildRecords(similar, t, fields)
	return check
}

// sameTitle is the title match the probe accepts: the whole title, case and
// spacing aside. Anything looser is the gateway's substring search, which is
// a lead, not an identity.
func sameTitle(title, value string) bool {
	return strings.EqualFold(strings.Join(strings.Fields(title), " "), strings.Join(strings.Fields(value), " "))
}

// queryCandidates is the value as written, plus its uppercase spaceless form
// when that is a plausible account or registration number. The field query is
// exact and case-sensitive, and an IBAN is the value most often copied out of
// a document with spaces in it and stored without them.
//
// The digit test is what keeps a legal name out of this: stripping the spaces
// from "PASTEX COM SRL" produces a string no register has ever held, and
// probing for it is a request that can only come back empty.
func queryCandidates(value string) []string {
	normalized := strings.ToUpper(strings.ReplaceAll(value, " ", ""))
	if normalized == value || !looksLikeIdentifier(normalized) {
		return []string{value}
	}
	return []string{value, normalized}
}

// looksLikeIdentifier reports whether a value could be an account or
// registration number: letters and digits only, and at least one digit.
func looksLikeIdentifier(value string) bool {
	digit := false
	for _, r := range value {
		switch {
		case r >= '0' && r <= '9':
			digit = true
		case (r >= 'A' && r <= 'Z') || (r >= 'a' && r <= 'z'):
		default:
			return false
		}
	}
	return digit
}

// buildRecords trims the matched actors to what a writer needs: how to address
// the record, and enough identity to tell it is the right one.
//
// The columns are the type's identity keys plus whatever was probed, so a hit
// always shows the value it was found by — including when that field is one
// the caller named and the type does not mark.
func buildRecords(actors []simulator.Actor, t *Type, probed []*FieldSpec) []FoundRecord {
	columns := make([]*FieldSpec, 0, len(probed)+2)
	for _, f := range t.Fields {
		if isIdentityField(f) {
			columns = append(columns, f)
		}
	}
	for _, f := range probed {
		if !containsField(columns, f) {
			columns = append(columns, f)
		}
	}

	out := make([]FoundRecord, 0, len(actors))
	for _, a := range actors {
		rec := FoundRecord{ID: a.ID, Ref: a.Ref, Title: strings.TrimSpace(a.Title)}
		// cleanValues is what the export reads values through, so a multiform
		// actor's "__form__<id>:<field>" keys resolve the same way here.
		values := cleanValues(a.Data)
		for _, f := range columns {
			if v, ok := values[f.ID]; ok {
				if rec.Fields == nil {
					rec.Fields = make(map[string]any, len(columns))
				}
				rec.Fields[f.Name] = v
			}
		}
		out = append(out, rec)
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Title < out[j].Title })
	return out
}

// formWorkspace reads the accId the form lives in, which the listing route
// wants explicitly. A failure is not fatal — the probes still go out, and an
// empty answer is a clearer symptom than a refusal to try.
func formWorkspace(ctx context.Context, sim *simulator.Client, t *Type) (string, string) {
	form, err := sim.GetForm(ctx, t.FormID, "id,accId,title")
	if err != nil {
		return "", fmt.Sprintf("form %d: %v — probing without an explicit workspace, "+
			"which the gateway may answer empty", t.FormID, err)
	}
	if form.AccID == "" {
		return "", fmt.Sprintf("form %d served no accId — probing without an explicit workspace, "+
			"which the gateway may answer empty", t.FormID)
	}
	return form.AccID, ""
}
