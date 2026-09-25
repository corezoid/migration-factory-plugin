package simulator

import (
	"cmp"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

// ErrNoCredential means no API key is configured; it is raised before the
// request leaves the process.
var ErrNoCredential = errors.New("simulator: no api key set (use WithAPIKey)")

// Error is a non-2xx response from the gateway. The platform is not uniform
// about its error envelope, so Message is filled best-effort from the shapes it
// does use and Body always keeps the raw payload.
type Error struct {
	StatusCode int
	Message    string
	Code       string // the envelope's error/type discriminator, when it has one
	Body       string
}

func (e *Error) Error() string {
	var b strings.Builder
	fmt.Fprintf(&b, "simulator: http %d", e.StatusCode)
	if e.Code != "" {
		fmt.Fprintf(&b, " (%s)", e.Code)
	}
	switch {
	case e.Message != "":
		fmt.Fprintf(&b, ": %s", e.Message)
	case e.Body != "":
		fmt.Fprintf(&b, ": %s", truncate(e.Body, 512))
	}
	return b.String()
}

// parseError converts a non-2xx response into an *Error. It does not close the
// body — the caller does.
func parseError(resp *http.Response) *Error {
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	e := &Error{StatusCode: resp.StatusCode, Body: string(raw)}

	// The shapes seen in the wild: Fastify's {statusCode,error,message}, a
	// nested {error:{message,code}}, and a bare {message} / {description}.
	var envelope struct {
		Message     string          `json:"message"`
		Description string          `json:"description"`
		Error       json.RawMessage `json:"error"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		e.Message = strings.TrimSpace(string(raw))
		return e
	}

	e.Message = cmp.Or(envelope.Message, envelope.Description)

	if len(envelope.Error) > 0 {
		var s string
		if json.Unmarshal(envelope.Error, &s) == nil {
			e.Code = s
		} else {
			var nested struct {
				Message string `json:"message"`
				Code    string `json:"code"`
				Type    string `json:"type"`
			}
			if json.Unmarshal(envelope.Error, &nested) == nil {
				e.Message = cmp.Or(e.Message, nested.Message)
				e.Code = cmp.Or(nested.Code, nested.Type)
			}
		}
	}
	return e
}

// StatusCode returns the HTTP status behind err, or 0 if it is not an *Error.
func StatusCode(err error) int {
	var e *Error
	if errors.As(err, &e) {
		return e.StatusCode
	}
	return 0
}

// IsNotFound reports a 404 — no such actor or form.
func IsNotFound(err error) bool { return StatusCode(err) == http.StatusNotFound }

// IsBadRequest reports a 400 — a rejected payload, e.g. actor data keyed by
// field titles instead of item ids, or a create under a non-root UAT form.
func IsBadRequest(err error) bool { return StatusCode(err) == http.StatusBadRequest }

// IsConflict reports a 409 — e.g. an actor ref already taken on that form.
func IsConflict(err error) bool { return StatusCode(err) == http.StatusConflict }

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// IsDuplicateRef reports the refusal a transaction gets when its ref was posted
// before: the platform answers 400 "Not unique ref" rather than deduplicating
// silently, so a retried post is refused, not doubled — which is exactly the
// idempotency a caller relies on, and no reason to warn. The phrase is looked
// for wherever parseError may have put it: the envelope is not uniform, and a
// string-shaped {"error":…} lands in Code, not Message.
func IsDuplicateRef(err error) bool {
	var e *Error
	if !errors.As(err, &e) || e.StatusCode != http.StatusBadRequest {
		return false
	}
	const phrase = "Not unique ref"
	return strings.Contains(e.Message, phrase) ||
		strings.Contains(e.Code, phrase) ||
		strings.Contains(e.Body, phrase)
}
