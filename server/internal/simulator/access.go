package simulator

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
)

// ShareWithGroup calls POST /access_rules/actor/{actorId} and grants a group
// view and modify — not remove — on that one actor.
//
// recursive is sent as false explicitly: the platform's default is true, and
// a grant that cascades is the right thing for a graph and the wrong thing
// for a record, whose children — if it ever has any — nobody here asked to
// share. The rule is the shape the platform actually takes: the grantee and
// its privileges sit under "data", not beside "action".
func (c *Client) ShareWithGroup(ctx context.Context, actorID string, groupID int) error {
	if err := validateActorID(actorID); err != nil {
		return err
	}
	rule := map[string]any{
		"action": "create",
		"data": map[string]any{
			"groupId": groupID,
			"privs":   map[string]bool{"view": true, "modify": true, "remove": false},
		},
	}
	return c.call(ctx, request{
		method: http.MethodPost,
		path:   "/access_rules/actor/" + seg(actorID),
		query:  url.Values{"recursive": {"false"}},
		body:   []any{rule},
	}, nil)
}

// ShareAccountPairWithGroup calls POST /access_rules/account/{nameId}_{currencyId}
// and grants a group view and modify — not remove — on that one pair.
//
// Access to money is enforced on the PAIR and never on the account row:
// bootstrapping the pair seeds access for the caller alone, and attaching the
// account to an actor seeds nothing at all. So without this the run's own key is
// the only identity that can see what it posted — every transaction lands, and
// the people the statement was imported for open the card and find no accounts
// on it. That failure is invisible from the posting side, which is why the share
// belongs next to the bootstrap rather than in anybody's runbook.
//
// The object is addressed by the pair's own id, `<nameId>_<currencyId>`; there
// is no route that takes the two apart. recursive is false for the same reason
// it is on an actor: this pair is what was asked for, not whatever the platform
// counts as beneath it.
func (c *Client) ShareAccountPairWithGroup(ctx context.Context, nameID string, currencyID, groupID int) error {
	if nameID == "" {
		return fmt.Errorf("simulator: no account-name id to share the pair of")
	}
	if currencyID <= 0 {
		return fmt.Errorf("simulator: no currency id to share the pair of")
	}
	if groupID <= 0 {
		return fmt.Errorf("simulator: no group to share the pair with")
	}
	rule := map[string]any{
		"action": "create",
		"data": map[string]any{
			"groupId": groupID,
			"privs":   map[string]bool{"view": true, "modify": true, "remove": false},
		},
	}
	return c.call(ctx, request{
		method: http.MethodPost,
		path:   "/access_rules/account/" + seg(nameID) + "_" + strconv.Itoa(currencyID),
		query:  url.Values{"recursive": {"false"}},
		body:   []any{rule},
	}, nil)
}
