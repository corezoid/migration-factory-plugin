package graph

import (
	"context"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"sync"
	"unicode"

	"migration-factory-plugin-mcp/internal/simulator"
)

// The type vocabulary used in types.schema.yaml and, through it, by whatever
// validates a write. Field values are scalars plus two parameterised forms:
// enum[a, b, c] and ref(<type slug>).
const (
	TypeString = "string" // single-line text
	TypeText   = "text"   // multi-line text
	TypeInt    = "int"
	TypeNumber = "number" // fractional: amounts, coordinates, confidences
	TypeBool   = "bool"
	TypeDate   = "date"
)

// TypeSet is the layer's type dictionary: one entry per form used on the
// layer, keyed by a slug the model addresses instead of the opaque formId.
type TypeSet struct {
	// Types are ordered by formId, the order the context legend renders.
	Types  []*Type
	byForm map[int]*Type
	bySlug map[string]*Type
}

// Type is one form as the graph files see it.
type Type struct {
	Slug string
	// FormID is the Simulator form behind the slug — the id every API call
	// needs and the only stable identity here; slugs are derived from titles
	// and change when a form is renamed.
	FormID int
	// FormTitle is the form's own name, kept so a renamed form is traceable
	// back from a generated slug.
	FormTitle   string
	Description string
	Fields      []*FieldSpec
}

// FieldSpec is one field of a form.
type FieldSpec struct {
	// Name is the field's slug — the key a write addresses.
	Name string
	// ID is the Simulator field id an actor's data is keyed by. Usually the
	// same as Name; an "item_<digits>" id makes them differ.
	ID       string
	Type     string
	Title    string
	Writable bool
}

// Type returns the entry for a form id.
func (ts *TypeSet) Type(formID int) (*Type, bool) {
	t, ok := ts.byForm[formID]
	return t, ok
}

// BySlug returns the entry a slug names — the direction a write takes when it
// names its own type instead of addressing a node that already has one.
func (ts *TypeSet) BySlug(slug string) (*Type, bool) {
	t, ok := ts.bySlug[slug]
	return t, ok
}

// Slugs lists every type name in the set, ordered, for an error that has to
// say what the writer could have meant.
func (ts *TypeSet) Slugs() []string {
	out := make([]string, 0, len(ts.Types))
	for _, t := range ts.Types {
		out = append(out, t.Slug)
	}
	sort.Strings(out)
	return out
}

// indexSlugs builds the slug lookup. Both constructors end here: slugs are
// assigned late in ResolveTypes and read straight off the file in
// ParseTypesSchema, and neither can fill the map as it goes.
func (ts *TypeSet) indexSlugs() {
	ts.bySlug = make(map[string]*Type, len(ts.Types))
	for _, t := range ts.Types {
		ts.bySlug[t.Slug] = t
	}
}

// Slug returns the slug of a form id, falling back to "form_<id>" for a form
// that is not in the set.
func (ts *TypeSet) Slug(formID int) string {
	if t, ok := ts.byForm[formID]; ok {
		return t.Slug
	}
	return "form_" + strconv.Itoa(formID)
}

// Field returns a form's field by its Simulator id.
func (t *Type) Field(id string) (*FieldSpec, bool) {
	for _, f := range t.Fields {
		if f.ID == id {
			return f, true
		}
	}
	return nil, false
}

// ResolveTypes builds the type dictionary for the actors of a layer: one
// Simulator form read per distinct formId, run concurrently, turned into
// slugs and field specs.
//
// The whole dictionary is derived — nothing here is hand-maintained, so an
// export always describes the forms as they are now. Field ids come from the
// form API, which is what keeps the schema from being a stub: a write against
// a made-up field name is refused instead of vanishing into the API.
func ResolveTypes(ctx context.Context, sim *simulator.Client, actors []Actor, concurrency int) (*TypeSet, []string, error) {
	if sim == nil {
		return nil, nil, fmt.Errorf("graph: ResolveTypes needs a simulator client")
	}

	// Distinct forms, with the title the layer read already served as the
	// fallback name for a form that cannot be read.
	titles := map[int]string{}
	var formIDs []int
	for _, a := range actors {
		if a.FormID == 0 {
			continue
		}
		if _, seen := titles[a.FormID]; !seen {
			formIDs = append(formIDs, a.FormID)
		}
		if titles[a.FormID] == "" {
			titles[a.FormID] = a.FormName
		}
	}
	sort.Ints(formIDs)

	forms := make([]*simulator.Form, len(formIDs))
	errs := make([]error, len(formIDs))
	parallel(len(formIDs), concurrency, func(i int) {
		forms[i], errs[i] = sim.GetForm(ctx, formIDs[i], simulator.FormWithFieldsFilter)
	})

	var warnings []string
	ts := &TypeSet{byForm: make(map[int]*Type, len(formIDs))}
	for i, formID := range formIDs {
		t := &Type{FormID: formID, FormTitle: titles[formID]}
		if form := forms[i]; errs[i] != nil {
			// A form the caller cannot read still has nodes on the layer;
			// keep it in the dictionary, named, with no fields — a write
			// against it fails loudly rather than being silently dropped.
			warnings = append(warnings, fmt.Sprintf("form %d (%s): %v — type has no field schema",
				formID, titles[formID], errs[i]))
		} else {
			t.FormTitle = firstNonEmpty(form.Title, t.FormTitle)
			t.Description = strings.TrimSpace(form.Description)
			for _, f := range form.Fields() {
				if f.ID == "" {
					continue
				}
				t.Fields = append(t.Fields, &FieldSpec{
					Name:     fieldSlug(f),
					ID:       f.ID,
					Type:     fieldType(f),
					Title:    strings.TrimSpace(f.Title),
					Writable: f.Visibility != "disabled",
				})
			}
		}
		dedupeFieldNames(t.Fields)
		ts.Types = append(ts.Types, t)
		ts.byForm[formID] = t
	}

	assignSlugs(ts.Types)
	ts.indexSlugs()
	resolveRefs(ts)
	return ts, warnings, nil
}

// assignSlugs names every type from its form title, stripping the prefix the
// whole workspace shares ("DEV_DTO_HRS_EMPLOYEE" -> "hrs_employee") so the
// slugs read as types rather than as installation names.
func assignSlugs(types []*Type) {
	prefix := commonTitlePrefix(types)
	taken := make(map[string]bool, len(types))
	for _, t := range types {
		title := t.FormTitle
		// Strip the shared prefix only when something is left to name the
		// type by; a form actually called "DEV_DTO_" keeps its full title.
		if trimmed := strings.TrimPrefix(title, prefix); trimmed != "" {
			title = trimmed
		}
		base := slugify(title)
		if base == "" {
			base = "form_" + strconv.Itoa(t.FormID)
		}
		slug := base
		if taken[slug] {
			// Two forms named the same: the id is the only thing that
			// separates them, and it keeps the slug stable across exports.
			slug = base + "_" + strconv.Itoa(t.FormID)
		}
		taken[slug] = true
		t.Slug = slug
	}
}

// commonTitlePrefix is the longest "_"-delimited prefix that most of the form
// titles share — "DEV_DTO_" across a workspace whose forms are named
// DEV_DTO_HRS_EMPLOYEE, DEV_DTO_DOCUMENT and so on.
//
// It is a majority rather than a universal prefix on purpose: one platform
// form on the layer ("Graphs") is enough to make the prefix shared by all
// titles empty, and then every slug carries the installation name it should
// have dropped.
func commonTitlePrefix(types []*Type) string {
	if len(types) < 2 {
		return ""
	}

	counts := map[string]int{}
	for _, t := range types {
		for i, r := range t.FormTitle {
			if r == '_' {
				counts[t.FormTitle[:i+1]]++
			}
		}
	}

	// Three fifths of the forms, and never fewer than two: a prefix two forms
	// out of fifty happen to share is a coincidence, not a namespace.
	threshold := max(2, len(types)*3/5)
	best := ""
	for prefix, n := range counts {
		if n < threshold {
			continue
		}
		if len(prefix) > len(best) || (len(prefix) == len(best) && prefix < best) {
			best = prefix
		}
	}
	return best
}

// fieldSlug is the name a write addresses a field by: the field id when it is
// readable, and the title turned into a slug when the id is an opaque
// "item_<digits>" key.
func fieldSlug(f simulator.Field) string {
	// A readable id is already the best name there is, and keeping it
	// verbatim means the schema slug and the Simulator key are the same
	// string ("inheritForms" rather than a lower-cased "inheritforms").
	if !strings.HasPrefix(f.ID, "item_") && isIdentifier(f.ID) {
		return f.ID
	}
	if s := slugify(firstWords(f.Title, 4)); s != "" {
		return s
	}
	return slugify(f.ID)
}

// isIdentifier reports whether an id can be used as a field name as it
// stands: letters, digits and "_", not starting with a digit.
func isIdentifier(s string) bool {
	if s == "" || unicode.IsDigit(rune(s[0])) {
		return false
	}
	for _, r := range s {
		if !unicode.IsLetter(r) && !unicode.IsDigit(r) && r != '_' {
			return false
		}
	}
	return true
}

// dedupeFieldNames keeps field slugs unique within a type — two fields whose
// titles slugify the same would otherwise shadow each other.
func dedupeFieldNames(fields []*FieldSpec) {
	taken := make(map[string]bool, len(fields))
	for i, f := range fields {
		name := f.Name
		if name == "" {
			name = "field_" + strconv.Itoa(i)
		}
		for n := 2; taken[name]; n++ {
			name = f.Name + "_" + strconv.Itoa(n)
		}
		taken[name] = true
		f.Name = name
	}
}

// fieldType maps a Simulator field onto the graph type vocabulary. An unknown
// widget degrades to string: a write is then validated as text rather than
// refused, which is the right trade for a field nobody has modelled yet.
func fieldType(f simulator.Field) string {
	switch strings.ToLower(f.Class) {
	case "check", "checkbox", "switch", "toggle":
		return TypeBool
	case "calendar", "date", "datetime", "datepicker":
		return TypeDate
	case "select", "radio", "multiselect", "dropdown":
		if opts := optionValues(f); len(opts) > 0 {
			return "enum[" + strings.Join(opts, ", ") + "]"
		}
		return TypeString
	case "actor", "actorlink", "link", "reference":
		// Filled in by resolveRefs once every type is known.
		if id := extraFormID(f); id > 0 {
			return "ref:" + strconv.Itoa(id)
		}
		return TypeString
	}

	switch strings.ToLower(f.Type) {
	case "int", "integer":
		return TypeInt
	case "float", "double", "number", "decimal":
		return TypeNumber
	}
	if multiline(f) {
		return TypeText
	}
	return TypeString
}

// resolveRefs turns the "ref:<formId>" placeholders fieldType leaves into
// ref(<slug>) now that every slug is known. A reference to a form outside the
// layer has no slug to name, so it stays a plain string.
func resolveRefs(ts *TypeSet) {
	for _, t := range ts.Types {
		for _, f := range t.Fields {
			rest, ok := strings.CutPrefix(f.Type, "ref:")
			if !ok {
				continue
			}
			id, _ := strconv.Atoi(rest)
			if target, known := ts.Type(id); known {
				f.Type = "ref(" + target.Slug + ")"
			} else {
				f.Type = TypeString
			}
		}
	}
}

// ---- small helpers ----

// slugify turns a title into a lower-case identifier: runs of anything that
// is not a letter or digit become a single "_".
func slugify(s string) string {
	var b strings.Builder
	lastUnderscore := true // also trims a leading "_"
	for _, r := range strings.TrimSpace(s) {
		switch {
		case unicode.IsLetter(r) || unicode.IsDigit(r):
			b.WriteRune(unicode.ToLower(r))
			lastUnderscore = false
		case !lastUnderscore:
			b.WriteByte('_')
			lastUnderscore = true
		}
	}
	return strings.Trim(b.String(), "_")
}

// firstWords keeps the first n words of a title — field titles here are often
// a whole sentence of documentation ("Full name — first and last name as
// written"), and only its head belongs in a slug.
func firstWords(s string, n int) string {
	fields := strings.Fields(s)
	if len(fields) > n {
		fields = fields[:n]
	}
	return strings.Join(fields, " ")
}

func optionValues(f simulator.Field) []string {
	var out []string
	for _, o := range f.Options {
		v := strings.TrimSpace(fmt.Sprint(o.Value))
		if v == "" {
			v = strings.TrimSpace(o.Title)
		}
		if v != "" && !strings.ContainsAny(v, ",]") {
			out = append(out, v)
		}
	}
	return out
}

func extraFormID(f simulator.Field) int {
	switch v := f.Extra["formId"].(type) {
	case float64:
		return int(v)
	case int:
		return v
	case string:
		id, _ := strconv.Atoi(v)
		return id
	}
	return 0
}

func multiline(f simulator.Field) bool {
	v, ok := f.Extra["multiline"].(bool)
	return ok && v
}

func firstNonEmpty(vs ...string) string {
	for _, v := range vs {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

// parallel runs fn(0..n-1) on at most limit goroutines. The layer needs one
// form read per type and one actor read per node; serially that is a minute
// of round trips on a 150-node layer.
func parallel(n, limit int, fn func(i int)) {
	if n == 0 {
		return
	}
	if limit <= 0 {
		limit = defaultConcurrency
	}
	if limit > n {
		limit = n
	}

	var wg sync.WaitGroup
	work := make(chan int)
	for range limit {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range work {
				fn(i)
			}
		}()
	}
	for i := range n {
		work <- i
	}
	close(work)
	wg.Wait()
}

// defaultConcurrency caps the parallel reads an export makes. Eight keeps a
// 150-node layer to a couple of seconds without hammering the gateway.
const defaultConcurrency = 8
