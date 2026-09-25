package simulator

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

// This file holds the entity endpoints the graph tools call: read an actor by
// id or by external ref, list the actors of a form, create one, write one
// back, and read the form behind it. The connector this was extracted from
// carries the rest of the CRUD surface — delete, cross-form search, form
// editing — and none of it is reachable from the tools, so none of it is here.
//
// The listing is the odd one out and the newest: it reads a form's records
// rather than a layer's nodes, which is the only way to answer whether an
// actor already exists when nothing put it on a graph.

// CreateActor calls POST /actors/actor/{formId} and returns the new actor.
//
// req.Data is keyed by the form's field ids, the same keys PatchActor writes.
// The actor is created as a record of its form and is not placed on any
// layer — see CreateActorRequest for why there is no contextLayerId here.
//
// In a form-tree (UAT) workspace formID must be the ROOT form: creating under
// a form that has a parent fails with 400 "Form <id> is not UAT".
func (c *Client) CreateActor(ctx context.Context, formID int, req CreateActorRequest) (*Actor, error) {
	if formID <= 0 {
		return nil, fmt.Errorf("simulator: CreateActor needs a form id")
	}
	if req.Data == nil {
		// The backend requires the key; an actor with no field values is
		// legitimate, so send an empty object rather than refusing.
		req.Data = map[string]any{}
	}
	var out itemEnvelope[Actor]
	err := c.call(ctx, request{
		method: http.MethodPost,
		path:   "/actors/actor/" + strconv.Itoa(formID),
		body:   req,
	}, &out)
	if err != nil {
		return nil, err
	}
	return &out.Data, nil
}

// GetActorByRef calls GET /actors/ref/{formId}/{ref} — the lookup by external
// business key instead of UUID.
//
// A missing actor comes back as a 404, which IsNotFound tells apart from a
// real failure: that distinction is what lets a create op ask "does this
// record exist yet" without a journal.
func (c *Client) GetActorByRef(ctx context.Context, formID int, ref, filter string) (*Actor, error) {
	if formID <= 0 || ref == "" {
		return nil, fmt.Errorf("simulator: GetActorByRef needs a form id and a ref")
	}
	var out itemEnvelope[Actor]
	if err := c.get(ctx, "/actors/ref/"+strconv.Itoa(formID)+"/"+seg(ref), filterQuery(filter), &out); err != nil {
		return nil, err
	}
	return &out.Data, nil
}

// GetActor calls GET /actors/{actorId}.
//
// filter is the server-side projection: a comma-separated field list, empty
// for everything. Read one actor with ActorSummaryFilter unless the form
// schema is genuinely wanted — an unfiltered read returns the whole template
// twice over.
func (c *Client) GetActor(ctx context.Context, actorID, filter string) (*Actor, error) {
	if err := validateActorID(actorID); err != nil {
		return nil, err
	}
	var out itemEnvelope[Actor]
	if err := c.get(ctx, "/actors/"+seg(actorID), filterQuery(filter), &out); err != nil {
		return nil, err
	}
	return &out.Data, nil
}

// PatchActor calls PUT /actors/actor/{formId}/{actorId} with
// replaceEmpty=false: the keys named in req.Data are written and every other
// stored value is left alone. This is the write path behind apply_graph —
// an ops file names the fields it touches and must not blank the rest.
func (c *Client) PatchActor(ctx context.Context, formID int, actorID string, req UpdateActorRequest) (*Actor, error) {
	if formID <= 0 {
		return nil, fmt.Errorf("simulator: PatchActor needs the actor's form id")
	}
	if err := validateActorID(actorID); err != nil {
		return nil, err
	}
	var out itemEnvelope[Actor]
	err := c.call(ctx, request{
		method: http.MethodPut,
		path:   "/actors/actor/" + strconv.Itoa(formID) + "/" + seg(actorID),
		query:  url.Values{"replaceEmpty": {"false"}},
		body:   req,
	}, &out)
	if err != nil {
		return nil, err
	}
	return &out.Data, nil
}

// GetForm calls GET /forms/{formId} — the field dictionary a node's data is
// keyed by. Pass FormWithFieldsFilter to keep the sections.
func (c *Client) GetForm(ctx context.Context, formID int, filter string) (*Form, error) {
	if formID <= 0 {
		return nil, fmt.Errorf("simulator: GetForm needs a form id")
	}
	var out itemEnvelope[Form]
	if err := c.get(ctx, "/forms/"+strconv.Itoa(formID), filterQuery(filter), &out); err != nil {
		return nil, err
	}
	return &out.Data, nil
}

// GetActorSummary reads an actor with ActorSummaryFilter — everything about
// the actor itself and none of its form schema.
func (c *Client) GetActorSummary(ctx context.Context, actorID string) (*Actor, error) {
	return c.GetActor(ctx, actorID, ActorSummaryFilter)
}

var actorUUIDRe = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)

// validateActorID rejects a malformed or shortened actor id before the
// request goes out. The backend answers 403 Access Denied for a non-UUID id,
// which reads as a permissions problem and is not one.
func validateActorID(actorID string) error {
	if actorID == "" {
		return fmt.Errorf("simulator: actor id is required")
	}
	if !actorUUIDRe.MatchString(actorID) {
		return fmt.Errorf("simulator: actor id %q is not a full UUID (8-4-4-4-12) — "+
			"the backend would answer a misleading 403 for it", actorID)
	}
	return nil
}

// filterQuery builds the projection query shared by the read routes.
func filterQuery(filter string) url.Values {
	if filter == "" {
		return nil
	}
	return url.Values{"filter": {filter}}
}

// actorPageLimit is the page size the actor listing walks with. The backend
// caps a page at 200 and silently clamps anything larger, so asking for more
// in one request buys nothing.
const actorPageLimit = 200

// ActorListFilter is the projection for a listing: what identifies an actor
// and what it holds, and none of its form schema. An unfiltered listing
// returns the form template once per row.
const ActorListFilter = "id,title,ref,status,data,formId,updatedAt"

// ListActorsOptions tunes GET /actors_filters/{formId}.
type ListActorsOptions struct {
	// WorkspaceID is the accId to list in. Unlike the routes this client's
	// other calls use, the listing is not scoped by the API key alone — with
	// no accId the gateway answers for no workspace, so a caller that wants
	// rows passes one.
	WorkspaceID string
	// Filter is the server-side field projection; pass ActorListFilter unless
	// whole actors are genuinely wanted.
	Filter string
	// Search is a full-text match on the actor title.
	Search string
	// Query is a data-field filter expression, e.g. "registration_id=37185315".
	Query string
	// Limit is the page size (capped at actorPageLimit). The probes read one
	// page in the backend's order and never page on: a hit is a hit.
	Limit int
}

// ActorList is one page of a form's actors.
type ActorList struct {
	Items []Actor
}

// ListActors calls GET /actors_filters/{formId} — the actors of one form,
// filtered, ordered and paged.
//
// This is the records side of a form, as opposed to the layer side: it
// answers with every actor of the form, whether or not anything placed it on
// a graph. A record created by an ops file lives here and nowhere else, which
// is why "does this counterparty already exist" is a question only this route
// can answer.
func (c *Client) ListActors(ctx context.Context, formID int, opts ListActorsOptions) (*ActorList, error) {
	if formID <= 0 {
		return nil, fmt.Errorf("simulator: ListActors needs a form id")
	}

	query := url.Values{}
	setQuery(query, "filter", opts.Filter)
	setQuery(query, "search", opts.Search)
	setQuery(query, "q", opts.Query)
	setQuery(query, "accId", opts.WorkspaceID)
	if opts.Limit > 0 {
		query.Set("limit", strconv.Itoa(min(opts.Limit, actorPageLimit)))
	}

	var out listEnvelope[Actor]
	if err := c.get(ctx, "/actors_filters/"+strconv.Itoa(formID), query, &out); err != nil {
		return nil, err
	}
	return &ActorList{Items: out.Data}, nil
}

// setQuery sets a query parameter unless the value is blank.
func setQuery(q url.Values, key, value string) {
	if v := strings.TrimSpace(value); v != "" {
		q.Set(key, v)
	}
}
