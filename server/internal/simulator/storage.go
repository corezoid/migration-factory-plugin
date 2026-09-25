package simulator

import (
	"bytes"
	"context"
	"fmt"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"net/url"
	"strconv"
)

// This file holds the one storage route the graph tools need: putting bytes
// into a workspace's storage so an actor can carry them.
//
// It exists for pictures. An actor's `picture` is a path in that storage and
// not an address on the web, which is the whole reason a picture found on a
// site has to be copied here first: the twin keeps its own copy, and it
// outlives the redesign that moves the original.

// UploadStatusClean is the only status the scanner clears a file with. Any
// other value means the file was rejected and must not be handed on.
const UploadStatusClean = "clean"

// Upload is a stored file. FileName is its storage path — the string an
// actor's `picture` carries, and the one thing a caller normally wants back.
type Upload struct {
	ID       int64  `json:"id"`
	AccID    string `json:"accId"`
	Title    string `json:"title"`
	Type     string `json:"type"`
	Size     int64  `json:"size"`
	FileName string `json:"fileName"`
	Status   string `json:"status"`
}

// Clean reports whether the scanner cleared the file for use.
func (u Upload) Clean() bool { return u.Status == "" || u.Status == UploadStatusClean }

// UploadFile stores bytes in a workspace's storage and returns the record.
//
// It is multipart rather than the base64 route: only multipart preserves the
// file's real type and the extension of its name, and the graph UI renders a
// picture by both. contentType is what the store records — pass the real one,
// because "application/octet-stream" comes back as a download link instead of
// an image.
//
// ttl=0 mirrors what the UI sends. Without it the gateway sometimes stores
// the file with a lifetime, and a picture that expires is worse than one that
// was never set: the node looks right today and blank next month.
func (c *Client) UploadFile(ctx context.Context, accID, name, contentType string, data []byte) (*Upload, error) {
	switch {
	case accID == "":
		return nil, fmt.Errorf("simulator: UploadFile needs the workspace to store the file in")
	case name == "":
		return nil, fmt.Errorf("simulator: UploadFile needs a file name")
	case len(data) == 0:
		return nil, fmt.Errorf("simulator: UploadFile got no bytes")
	}

	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	hdr := textproto.MIMEHeader{}
	hdr.Set("Content-Disposition", fmt.Sprintf(`form-data; name="file"; filename=%q`, name))
	if contentType != "" {
		hdr.Set("Content-Type", contentType)
	}
	part, err := mw.CreatePart(hdr)
	if err != nil {
		return nil, fmt.Errorf("simulator: build upload part: %w", err)
	}
	if _, err := part.Write(data); err != nil {
		return nil, fmt.Errorf("simulator: write upload part: %w", err)
	}
	if err := mw.Close(); err != nil {
		return nil, fmt.Errorf("simulator: close upload body: %w", err)
	}

	var out itemEnvelope[Upload]
	if err := c.call(ctx, request{
		method:      http.MethodPost,
		path:        "/upload/" + seg(accID),
		query:       url.Values{"ttl": {strconv.Itoa(0)}},
		rawBody:     buf.Bytes(),
		contentType: mw.FormDataContentType(),
	}, &out); err != nil {
		return nil, err
	}
	if !out.Data.Clean() {
		return nil, fmt.Errorf("simulator: %s was stored as %q, not %q — the scanner rejected it",
			name, out.Data.Status, UploadStatusClean)
	}
	if out.Data.FileName == "" {
		return nil, fmt.Errorf("simulator: %s was uploaded but the store returned no path to it", name)
	}
	return &out.Data, nil
}
