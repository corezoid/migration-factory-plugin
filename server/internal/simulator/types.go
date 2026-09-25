package simulator

import (
	"encoding/json"
)

// ---------- envelopes ----------

// The gateway wraps every payload: a single entity comes back as
// {"data": {...}}, a collection as {"data": [...]} plus an optional stats
// object when the caller asked for totals.

type itemEnvelope[T any] struct {
	Data T `json:"data"`
}

type listEnvelope[T any] struct {
	Data []T `json:"data"`
}

// UnmarshalJSON accepts the two shapes the gateway returns a collection in:
// a bare array under "data" (the form listings) and an object under "data"
// carrying the rows in "list" (the actor listings). Both land in Data; the
// totals and stats the gateway may add beside them have no reader here.
func (l *listEnvelope[T]) UnmarshalJSON(b []byte) error {
	var raw struct {
		Data json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(b, &raw); err != nil {
		return err
	}
	if len(raw.Data) == 0 || string(raw.Data) == "null" {
		return nil
	}
	if raw.Data[0] == '[' {
		return json.Unmarshal(raw.Data, &l.Data)
	}
	var inner struct {
		List []T `json:"list"`
	}
	if err := json.Unmarshal(raw.Data, &inner); err != nil {
		return err
	}
	l.Data = inner.List
	return nil
}

// ---------- actors ----------

// Actor is one graph node: an instance of a form, whose field values live in
// Data. Which fields arrive depends on the projection the read asked for — an
// unrequested field is simply zero here.
type Actor struct {
	ID          string         `json:"id"`
	AccID       string         `json:"accId,omitempty"`
	FormID      int            `json:"formId,omitempty"`
	FormTitle   string         `json:"formTitle,omitempty"`
	Title       string         `json:"title,omitempty"`
	Description string         `json:"description,omitempty"`
	Ref         string         `json:"ref,omitempty"`
	Color       string         `json:"color,omitempty"`
	Picture     string         `json:"picture,omitempty"`
	Data        map[string]any `json:"data,omitempty"`
	Hole        bool           `json:"hole,omitempty"`
}

// ResolvedFormID returns the form id to address this actor by — the leaf form
// its own values are keyed under on a multiform node, and the actor's own
// formId otherwise. It is the same rule LayerActor.ResolvedFormID applies, so
// a node read either way resolves to the same form.
func (a Actor) ResolvedFormID() int { return resolvedFormID(a.Data, a.FormID) }

// CreateActorRequest is the body of POST /actors/actor/{formId}.
//
// Data is keyed by the form's field ids ("item_<digits>" — not their titles),
// and each value's shape follows that field's class; read the form with
// GetForm first.
//
// There is deliberately no contextLayerId here, which the route takes as a
// query parameter to place the new actor on a layer: the graph tools create a
// record of its form and nothing else. Placing a node on the canvas needs the
// edge to its parent as well, and that endpoint is not wired — an actor on the
// canvas with no edge is invisible in the tree and shows up in the next export
// as a second root. Ref is the handle on an off-canvas actor instead.
type CreateActorRequest struct {
	Data        map[string]any `json:"data"`
	Title       string         `json:"title,omitempty"`
	Description string         `json:"description,omitempty"`
	// Ref is the external business key. It is unique per form, which is what
	// makes creating an actor idempotent: the same ops file replayed finds
	// the actor by ref instead of creating a second one.
	Ref string `json:"ref,omitempty"`
	// Picture is the actor's image, and it is a path in the workspace's
	// storage — what UploadFile returns — never an address on the web. It is
	// set at creation rather than in a second write so a record arrives with
	// its face already on it.
	Picture string `json:"picture,omitempty"`
}

// UpdateActorRequest is the body of PUT /actors/actor/{formId}/{actorId}.
// Only the fields you set change; a nil pointer or empty value is omitted.
//
// Data is merged key by key — the keys you include are written, the rest of
// the actor's data is left alone.
type UpdateActorRequest struct {
	Data        map[string]any `json:"data,omitempty"`
	Title       string         `json:"title,omitempty"`
	Description string         `json:"description,omitempty"`
	Ref         string         `json:"ref,omitempty"`
	// Picture is the actor's image, as a path in the workspace's storage —
	// what UploadFile returns, never an address on the web.
	Picture string `json:"picture,omitempty"`
	// Hole is cleared when a placeholder node receives its first data; a nil
	// pointer leaves the flag alone.
	Hole *bool `json:"hole,omitempty"`
}

// ActorSummaryFilter is a sane projection for reading one actor: everything a
// caller usually wants and nothing of the form schema. Without a filter the
// gateway returns the actor's whole form template twice plus the full access
// list — tens of thousands of tokens.
const ActorSummaryFilter = "id,title,description,status,data,formId,formTitle,ref,ownerId,createdAt,updatedAt"

// ---------- forms ----------

// Form is a form template (the product calls it an Account Template): the
// field structure actors of this form instantiate.
type Form struct {
	ID          int    `json:"id"`
	AccID       string `json:"accId,omitempty"`
	Title       string `json:"title,omitempty"`
	Description string `json:"description,omitempty"`
	Ref         string `json:"ref,omitempty"`
	Type        string `json:"type,omitempty"`
	Color       string `json:"color,omitempty"`
	Picture     string `json:"picture,omitempty"`
	// Sections is the field structure. A read returns it nested under the
	// entity's `form` object rather than at the top level, so UnmarshalJSON
	// lifts it here either way — which also means a `filter` that is to keep
	// the fields must list `form`, not `sections` (see FormWithFieldsFilter).
	Sections []Section `json:"sections,omitempty"`
}

// UnmarshalJSON decodes a form, lifting the sections out of the nested
// `form` object the read routes wrap them in when they are not top level.
func (f *Form) UnmarshalJSON(b []byte) error {
	type alias Form
	var a alias
	if err := json.Unmarshal(b, &a); err != nil {
		return err
	}
	*f = Form(a)
	if len(f.Sections) > 0 {
		return nil
	}
	var nested struct {
		Form struct {
			Sections []Section `json:"sections"`
		} `json:"form"`
	}
	if json.Unmarshal(b, &nested) == nil {
		f.Sections = nested.Form.Sections
	}
	return nil
}

// Section is one group of fields in a form. Only what the graph tools read is
// modelled: forms are never written back from here, so there is nothing to
// round-trip.
type Section struct {
	ID      string  `json:"id,omitempty"`
	Title   string  `json:"title,omitempty"`
	Content []Field `json:"content"`
}

// Field is one input in a section. ID is the stable "item_<digits>" key an
// actor's Data uses; Class picks the widget and therefore the value shape.
type Field struct {
	ID    string `json:"id,omitempty"`
	Key   string `json:"key,omitempty"`
	Class string `json:"class,omitempty"`
	// Type sub-types an edit field: text (default), password, email, phone,
	// int, float.
	Type  string `json:"type,omitempty"`
	Title string `json:"title,omitempty"`
	// Value is the default value; its shape follows Class.
	Value any `json:"value,omitempty"`
	// Options are the choices of a radio/select/multiSelect field.
	Options []FieldOption `json:"options,omitempty"`
	// Extra is class-specific config: optionsSource for a dynamic select,
	// calendar bounds, {multiline,rows} for an edit, reverseEdge for an
	// actor-reference field.
	Extra map[string]any `json:"extra,omitempty"`
	// Description is the helper text under the field — the authoritative
	// meaning of the field, worth reading before guessing from Title.
	Description string `json:"description,omitempty"`
	// Visibility is visible (default), disabled or hidden.
	Visibility string `json:"visibility,omitempty"`
	Color      string `json:"color,omitempty"`
}

// FieldOption is one choice of a radio, select or multiSelect field.
type FieldOption struct {
	Title string `json:"title,omitempty"`
	Value any    `json:"value,omitempty"`
	Color string `json:"color,omitempty"`
}

// FormWithFieldsFilter is the projection to use when the fields are wanted:
// the sections live under the top-level `form` key, so a filter naming
// `sections` silently drops them.
const FormWithFieldsFilter = "id,title,description,status,type,color,form"

// Fields flattens a form's sections into the field list, in form order — the
// dictionary an actor's Data is keyed by.
func (f *Form) Fields() []Field {
	var fields []Field
	for _, section := range f.Sections {
		fields = append(fields, section.Content...)
	}
	return fields
}
