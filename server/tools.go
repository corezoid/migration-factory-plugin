package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"migration-factory-plugin-mcp/internal/firecrawl"
	"migration-factory-plugin-mcp/internal/graph"
	"migration-factory-plugin-mcp/internal/ledger"
)

const (
	exportTimeout = 5 * time.Minute
	applyTimeout  = 10 * time.Minute
	// readPageTimeout bounds one page. The provider renders it in seconds and
	// gives up on its own well before this; the deadline is here so a hung
	// connection cannot hold the whole run — the server answers one request at
	// a time.
	readPageTimeout = 3 * time.Minute
	// A statement can hold thousands of rows and each is its own POST.
	postStatementTimeout = 30 * time.Minute
)

func toolDefs() []any {
	return []any{
		map[string]any{
			"name": "export_graph",
			"description": "Export a Digital Twin layer into the three files the dto-fill skill reads: " +
				"graph.values.yaml (the tree with every stored value), graph.ids.json (path -> uuid) and " +
				"types.schema.yaml (the field dictionary). They land in `dir`, which defaults to the current " +
				"directory — no per-layer subdirectory. Run this before planning an ops file, and note that " +
				"apply_graph refreshes the same three files after a successful write.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"layer": map[string]any{
						"type":        "string",
						"description": "Layer UUID to export.",
					},
					"dir": map[string]any{
						"type":        "string",
						"description": "Directory the three files are written into, created if missing. Defaults to the current directory.",
					},
				},
				"required": []string{"layer"},
			},
		},
		map[string]any{
			"name": "apply_graph",
			"description": "Plan a graph.ops.yaml against the live layer and, when write is true, apply it. " +
				"A write returns the same full diff a plan does, so one call with write: true is the normal " +
				"way to apply — planning first and applying second only costs a round trip. Omitting write " +
				"plans and writes nothing, for when the caller genuinely wants to look before touching the " +
				"layer. The type dictionary is read from the export sitting next to the ops file, so " +
				"keep graph.ops.yaml in the same directory as graph.ids.json. Every op is an overwrite, so an " +
				"unchanged file replays as 'already applied'. A successful write re-exports the layer into that " +
				"same directory.\n\n" +
				"A write also keeps result.json beside the ops file: how many placeholder holes were filled, " +
				"actors updated and records created, with the uuid of each. It is cumulative and deduplicated " +
				"by uuid, so applying the same file again does not count the same node twice — the numbers are " +
				"the totals for the document, not for the last call.\n\n" +
				"Applying also stamps each op with the uuid of the node it resolved to, writing it back into " +
				"the ops file before anything is sent. From then on the op is addressed by that id and its " +
				"`at:` path is ignored, so the file still replays after a `rename:` has moved the paths.\n\n" +
				"An op can also carry `picture:` — the absolute address of an image on the source, " +
				"written only when the source ties that image to this subject (the alt text, the " +
				"caption, the JSON-LD key). The image is COPIED: it is fetched, checked and uploaded into the " +
				"workspace's storage, because an actor's picture is a path there and never a URL. A " +
				"node that already carries a picture keeps it. An image that cannot be taken — " +
				"refused, too small to be a picture of anything, or already bound to another node in " +
				"this run — is reported and the rest of the op still applies.\n\n" +
				"An op that carries `type:` (a slug from types.schema.yaml) and `ref:` (a business key) " +
				"owns a record instead of addressing a node: the ref is looked up first, and the record is " +
				"created when there is none. It is created as an actor of its form and is NOT placed on the " +
				"layer, so it does not appear in the refreshed graph.values.yaml — the stamped id and the " +
				"ref are the handles on it. It is shared to the session's group when the server was given " +
				"one; a share that failed is reported and the record stands. This is the only thing " +
				"apply_graph does that another ops file cannot undo.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"ops": map[string]any{
						"type":        "string",
						"description": "Path to the ops file to plan or apply.",
					},
					"write": map[string]any{
						"type": "boolean",
						"description": "false (default) plans and writes nothing. true applies the ops and " +
							"returns the plan alongside the result — pass it whenever the ask was to get " +
							"the source into the graph, rather than to preview what would land there.",
					},
					"layer": map[string]any{
						"type":        "string",
						"description": "Layer UUID. Optional: the ops file's own `layer:` key is used, and they must agree.",
					},
					"partial": map[string]any{
						"type":        "boolean",
						"description": "Apply the ops that validate even when others do not. Off by default — half an import is worse than none.",
					},
					"keep_export": map[string]any{
						"type":        "boolean",
						"description": "Leave the export beside the ops file untouched after a write, instead of refreshing it.",
					},
				},
				"required": []string{"ops"},
			},
		},
		map[string]any{
			"name": "find_records",
			"description": "Answer, for each value, whether a record of this type already carries that " +
				"identity — the check to run BEFORE writing a record, every time. A layer shows the " +
				"nodes somebody placed on it, usually one empty placeholder per type per branch; the " +
				"form behind the type holds every record, including the ones earlier runs created off " +
				"the canvas and the ones a person typed into Simulator. Skipping this check is how the " +
				"same counterparty ends up in the register three times under three different refs.\n\n" +
				"Pass the identity of each subject about to be written — several in one call, one per " +
				"record. By default a value is probed against every field the type marks as an identity " +
				"key, then against the actor's title, and the first probe that matches wins. When the " +
				"source identifies its subjects by something the type does not mark — an account number, " +
				"a document number, an email — name those fields in `fields` after reading the type in " +
				"types.schema.yaml. The title probe is what catches a record whose identity field was " +
				"never filled, which is most of what a source like that leaves behind. It matches the " +
				"whole title, case and spacing aside; a record whose title merely contains the value is " +
				"listed as similar, and similar is not found.\n\n" +
				"FOUND means write to that record — with `type:` plus ITS `ref:`, the one reported here, " +
				"not one you derived. A found record with an empty ref was made by hand and no ref " +
				"lookup reaches it: put its id in the op's `id:`. NOT FOUND is the only answer that " +
				"licenses a create. A value whose probe failed is unknown, not absent — do not create " +
				"on it.\n\n" +
				"The slug is resolved against types.schema.yaml in `dir`, so export the layer first.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"type": map[string]any{
						"type":        "string",
						"description": "Type slug to look in — the name in [square brackets] in graph.values.yaml.",
					},
					"values": map[string]any{
						"type":        "array",
						"items":       map[string]any{"type": "string"},
						"description": "The identities to check, one per record about to be written.",
					},
					"fields": map[string]any{
						"type":  "array",
						"items": map[string]any{"type": "string"},
						"description": "Fields to probe, in order, instead of the type's identity keys — the " +
							"names as they appear in types.schema.yaml, plus \"title\" for the actor's own " +
							"title. Naming any field drops the title probe unless \"title\" is among them, so " +
							"a check can be narrowed to exactly the keys that identify a record here.",
					},
					"dir": map[string]any{
						"type":        "string",
						"description": "Directory holding the export the slug is resolved against. Defaults to the current directory.",
					},
				},
				"required": []string{"type", "values"},
			},
		},
		map[string]any{
			"name": "read_page",
			"description": "Read one web page and return it as Markdown — the same rendering a website " +
				"source arrives in, so a page fetched here and a page handed over as a file read alike. " +
				"One address, one page: nothing is followed and no site is crawled, so reading a site " +
				"means calling this once per page you decided was worth reading.\n\n" +
				"Use it when the source IS a URL, and when a website source's front page (`source_scope " +
				"site`) points at something the layer wants and lacks — an \"about\", \"contacts\", " +
				"\"team\" or \"services\" page for a company twin. Do not walk a site for its own sake, " +
				"and say in `gaps` which pages you read and which you left.\n\n" +
				"The answer also carries an inventory of the pictures on the page — each with the alt " +
				"text or caption the page gives it — and the page's own og:image, which on a front " +
				"page is almost always the logo, stated as data rather than inferred from a banner. " +
				"That text is what ties a photograph to the person, product or office it shows, and " +
				"it is the only thing that licenses putting the image on a node. It is on by default " +
				"because a picture nobody was shown is one no node ever gets; images: false turns it " +
				"off for a read that is only about what the page says.\n\n" +
				"It returns the page's main content: navigation, ads and cookie banners are dropped, so a " +
				"page that is mostly chrome comes back thin, and a page behind a login or a bot wall comes " +
				"back refused rather than half-read. The refusal says whether reading it again is worth " +
				"anything. A page that renders to no text at all is an error too, not an empty answer — " +
				"there is nothing in it to route, and nothing should be written from it.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"url": map[string]any{
						"type": "string",
						"description": "The page to read: a full http or https address, no #fragment. " +
							"A site root reads as its front page, nothing deeper.",
					},
					"images": map[string]any{
						"type": "boolean",
						"description": "Whether to list the pictures on the page. On by default: a " +
							"picture nobody was told about is one no node ever gets, and the list is " +
							"cheap next to the page itself. Pass false for a read that is only ever " +
							"about what the page says.",
					},
				},
				"required": []string{"url"},
			},
		},
		map[string]any{
			"name": "post_statement",
			"description": "Record a parsed bank statement on an actor as transactions — the step after " +
				"a statement has been turned into JSONL and checked against the totals it prints about " +
				"itself.\n\n" +
				"Every row is posted onto the (account-name, currency) pair it belongs to. A pair on an " +
				"actor is two accounts with their own ids, one debit and one credit, and a transaction " +
				"carries no direction of its own — the side is the id it lands on. So `debit_sum` goes " +
				"to the debit side and `credit_sum` to the credit side, the card's credit-minus-debit " +
				"total is the net movement, and both turnovers stay readable separately, matching the " +
				"two columns the statement itself prints.\n\n" +
				"A file may hold several currencies; each gets its own pair, created on first sight and " +
				"reused for the rest of the run. Creating a pair is also what grants access to it — " +
				"attaching the account alone does not, and a transaction without that access is refused " +
				"403 — so this is done for every currency whether or not the pair looks new. Each pair " +
				"is then shared with the session's group when the server was given one, because the " +
				"bootstrap grants the caller and nobody else — without that share the rows land where " +
				"only this run can see them; a share that failed is reported and the rows stand.\n\n" +
				"Each transaction is dated by the row it came from, not by the moment of the import: " +
				"the statement's `transaction_date` (with `transaction_time` where the statement prints " +
				"one) is sent as the transaction's original date. The rows' clock carries no zone, so it " +
				"is read as UTC unless `timezone` names the bank's. A row whose date is missing or is " +
				"not `yyyy-mm-dd` stops the run — the alternative is a transaction confidently stamped " +
				"with today, which afterwards is indistinguishable from one that really happened today.\n\n" +
				"Idempotent by construction: each transaction's ref is derived from the row it came " +
				"from, so re-running the same file posts nothing twice and a run interrupted halfway " +
				"can simply be run again. Rows already present are reported as duplicates, not as " +
				"failures.\n\n" +
				"Run it with `dry_run` first on anything unfamiliar: that resolves the pairs and totals " +
				"the file per currency without posting, which is the cheapest way to see that the " +
				"turnovers match the statement before any of it is written.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"account_id": map[string]any{
						"type": "string",
						"description": "The account-name category to record under, BY NAME — e.g. " +
							"\"Bank Statement\". Created if the workspace does not have it. It is a name " +
							"and not an id because the pair route resolves names, and the workspace's " +
							"name register has no lookup by id.",
					},
					"actor_id": map[string]any{
						"type":        "string",
						"description": "Actor UUID the accounts hang on.",
					},
					"path": map[string]any{
						"type": "string",
						"description": "Path to the .jsonl, one transaction per line, with " +
							"transaction_date, debit_sum, credit_sum, currency and description.",
					},
					"ref_prefix": map[string]any{
						"type": "string",
						"description": "Namespaces the idempotency refs (default \"stmt\"). Change it only " +
							"to post the same statement a second time on purpose — the same file under " +
							"the same prefix is refused as a duplicate, which is the point.",
					},
					"timezone": map[string]any{
						"type": "string",
						"description": "IANA zone the rows' wall clock is read in, e.g. " +
							"\"Europe/Kyiv\". Defaults to UTC, which invents nothing but is up to " +
							"a few hours off the times the statement prints; pass the issuing " +
							"bank's zone when it is known.",
					},
					"dry_run": map[string]any{
						"type":        "boolean",
						"description": "Resolve the pairs and total the file per currency, posting nothing.",
					},
				},
				"required": []string{"account_id", "actor_id", "path"},
			},
		},
	}
}

type callParams struct {
	Name      string          `json:"name"`
	Arguments json.RawMessage `json:"arguments"`
}

func callTool(raw json.RawMessage) (any, *rpcError) {
	var p callParams
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, &rpcError{Code: -32602, Message: "bad params: " + err.Error()}
	}

	var (
		text string
		err  error
	)
	switch p.Name {
	case "export_graph":
		text, err = runExport(p.Arguments)
	case "apply_graph":
		text, err = runApply(p.Arguments)
	case "find_records":
		text, err = runFindRecords(p.Arguments)
	case "read_page":
		text, err = runReadPage(p.Arguments)
	case "post_statement":
		text, err = runPostStatement(p.Arguments)
	default:
		return nil, &rpcError{Code: -32602, Message: "unknown tool: " + p.Name}
	}

	// A tool that failed reports through the result, not a protocol error:
	// the model is supposed to read the message and correct itself.
	if err != nil {
		return toolResult(err.Error(), true), nil
	}
	return toolResult(text, false), nil
}

// toolResult builds a CallToolResult. structuredContent is deliberately absent
// rather than null: the field is optional, but when present it must be an
// object, and a literal null fails the client's schema validation on every
// call — success and failure alike.
func toolResult(text string, isError bool) any {
	return map[string]any{
		"content": []any{map[string]any{"type": "text", "text": text}},
		"isError": isError,
	}
}

func runExport(raw json.RawMessage) (string, error) {
	var args struct {
		Layer string `json:"layer"`
		Dir   string `json:"dir"`
	}
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &args); err != nil {
			return "", fmt.Errorf("bad arguments: %w", err)
		}
	}

	layerID := strings.TrimSpace(args.Layer)
	if layerID == "" {
		return "", errors.New("no layer to export: pass `layer` with the layer UUID")
	}

	cfg, err := loadConfig()
	if err != nil {
		return "", err
	}

	dir, err := resolve(firstNonEmpty(args.Dir, "."))
	if err != nil {
		return "", err
	}

	ctx, cancel := context.WithTimeout(context.Background(), exportTimeout)
	defer cancel()

	start := time.Now()
	res, err := graph.ExportLayer(ctx, cfg.client(), graph.ExportOptions{LayerID: layerID, Dir: dir})
	if err != nil {
		return "", err
	}

	var b strings.Builder
	fmt.Fprintf(&b, "exported layer %s into %s in %s\n", layerID, dir, time.Since(start).Round(time.Millisecond))
	fmt.Fprintf(&b, "  %d nodes, %d edges, %d types, %d nodes with values\n",
		res.Nodes, res.Edges, res.Types, res.NodesWithValues)
	for _, path := range []string{res.ValuesPath, res.IDsPath, res.TypesPath} {
		if info, statErr := os.Stat(path); statErr == nil {
			fmt.Fprintf(&b, "  %8d bytes  %s\n", info.Size(), filepath.Base(path))
		}
	}
	writeWarnings(&b, res.Warnings)
	return b.String(), nil
}

// runFindRecords answers, per value, whether a record of the type already
// carries that identity.
func runFindRecords(raw json.RawMessage) (string, error) {
	var args struct {
		Type   string   `json:"type"`
		Values []string `json:"values"`
		Fields []string `json:"fields"`
		Dir    string   `json:"dir"`
	}
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &args); err != nil {
			return "", fmt.Errorf("bad arguments: %w", err)
		}
	}
	if strings.TrimSpace(args.Type) == "" {
		return "", errors.New("no type to check: pass `type` with a slug from graph.values.yaml")
	}

	cfg, err := loadConfig()
	if err != nil {
		return "", err
	}
	dir, err := resolve(firstNonEmpty(args.Dir, "."))
	if err != nil {
		return "", err
	}

	ctx, cancel := context.WithTimeout(context.Background(), exportTimeout)
	defer cancel()

	res, err := graph.FindRecords(ctx, cfg.client(), graph.FindRecordsOptions{
		Dir:    dir,
		Type:   args.Type,
		Fields: args.Fields,
		Values: args.Values,
	})
	if err != nil {
		return "", err
	}
	return renderFindResult(res), nil
}

// renderSimilar names the near-misses of a title probe: a lead for the writer
// to check by eye, never an address to write to, so no ref is printed.
func renderSimilar(recs []graph.FoundRecord) string {
	titles := make([]string, 0, len(recs))
	for _, r := range recs {
		titles = append(titles, fmt.Sprintf("%q", r.Title))
	}
	return strings.Join(titles, ", ")
}

// renderFindResult writes the verdict per value, then the one line that says
// what to do with it.
func renderFindResult(res *graph.FindResult) string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s (form %d) — probed %s\n\n", res.Type, res.FormID, strings.Join(res.Probes, ", "))

	for _, c := range res.Checks {
		switch {
		case c.Err != nil:
			fmt.Fprintf(&b, "  %-44s UNKNOWN — %v\n", c.Value, c.Err)
		case !c.Found():
			fmt.Fprintf(&b, "  %-44s not found\n", c.Value)
			if len(c.Similar) > 0 {
				fmt.Fprintf(&b, "      %-38s similar titles, not this record: %s\n", "", renderSimilar(c.Similar))
			}
		default:
			fmt.Fprintf(&b, "  %-44s FOUND by %s\n", c.Value, c.MatchedBy)
			for _, r := range c.Records {
				addr := "ref: " + r.Ref
				if r.Ref == "" {
					addr = "NO REF — address it by id:"
				}
				fmt.Fprintf(&b, "      %-38s %s  [%s]\n", r.Title, addr, r.ID)
				if len(r.Fields) > 0 {
					fmt.Fprintf(&b, "      %-38s %s\n", "", renderFields(r.Fields))
				}
			}
		}
	}

	found, missing, failed := res.Tally()
	fmt.Fprintf(&b, "\n%d found, %d not found", found, missing)
	if failed > 0 {
		fmt.Fprintf(&b, ", %d could not be checked", failed)
	}
	b.WriteString("\n")
	if found > 0 {
		b.WriteString("Write to a found record with `type:` plus the ref reported above — not a ref of " +
			"your own, or you create a second copy of it.\n")
	}
	if missing > 0 {
		b.WriteString("Only the not-found values license a create.\n")
	}
	if failed > 0 {
		b.WriteString("A value that could not be checked is unknown, not absent: leave it unwritten " +
			"and say so, or check it again.\n")
	}
	writeWarnings(&b, res.Warnings)
	return b.String()
}

// renderFields prints a matched record's identity values in a stable order.
func renderFields(fields map[string]any) string {
	keys := make([]string, 0, len(fields))
	for k := range fields {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, fmt.Sprintf("%s=%v", k, fields[k]))
	}
	return strings.Join(parts, "  ")
}

// runReadPage renders one web page to Markdown and hands the whole of it
// back. Nothing is trimmed or summarised on the way out: the caller was told
// to read a source whole, and a tool that quietly cut a page in half would
// make it wrong about what the page said.
func runReadPage(raw json.RawMessage) (string, error) {
	var args struct {
		URL string `json:"url"`
		// A pointer, so an absent `images` can be told from an explicit
		// false: absent means yes. A caller that never names the flag is the
		// usual case, and defaulting it off would hide every picture on the
		// page from the one reader that could have used it.
		Images *bool `json:"images"`
	}
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &args); err != nil {
			return "", fmt.Errorf("bad arguments: %w", err)
		}
	}
	pageURL := strings.TrimSpace(args.URL)
	if pageURL == "" {
		return "", errors.New("no page to read: pass `url` with the address of the page")
	}

	cfg, err := loadFirecrawlConfig()
	if err != nil {
		return "", err
	}

	ctx, cancel := context.WithTimeout(context.Background(), readPageTimeout)
	defer cancel()

	withImages := args.Images == nil || *args.Images

	start := time.Now()
	read := cfg.client().Scrape
	if withImages {
		read = cfg.client().ScrapeWithImages
	}
	page, err := read(ctx, pageURL)
	if err != nil {
		return "", err
	}
	text := strings.TrimSpace(page.Markdown)
	if text == "" {
		// Not a page that says nothing — a page we got nothing of. Reported
		// as a failure so the two cannot be confused: the second licenses
		// reading it another way, the first would license writing down that
		// the site is silent on the subject.
		return "", fmt.Errorf("%s was read but rendered to no text — nothing in it to route, and that is "+
			"a page this reader could not see, not a page with nothing on it", pageURL)
	}

	var b strings.Builder
	fmt.Fprintf(&b, "read %s — %d bytes of markdown in %s\n",
		pageURL, len(text), time.Since(start).Round(time.Millisecond))
	if title := strings.TrimSpace(page.Metadata.Title); title != "" {
		fmt.Fprintf(&b, "title: %s\n", title)
	}
	if addr := page.Address(); addr != "" && addr != pageURL {
		// A redirect means the address that answered is not the one that was
		// asked for, and which page this text belongs to is exactly what a
		// `source:` line in the ops file claims.
		fmt.Fprintf(&b, "redirected to: %s\n", addr)
	}
	if img := strings.TrimSpace(page.Metadata.OGImage); img != "" {
		fmt.Fprintf(&b, "og:image (the page's own picture of itself, usually the logo): %s\n", img)
	}
	if icon := strings.TrimSpace(page.Metadata.Favicon); icon != "" && withImages {
		// Named for what it is good for: a company's logo is often inline SVG
		// in the markup, which no scan of <img> tags can see, and then this
		// is the only raster picture of the site's owner anywhere on it.
		fmt.Fprintf(&b, "favicon (the site's own icon — its owner's logo when nothing else on "+
			"the page is one): %s\n", icon)
	}
	writeImages(&b, withImages, page.Images)
	b.WriteString("\n")
	b.WriteString(text)
	b.WriteString("\n")
	return b.String(), nil
}

// writeImages renders the picture inventory: the address, and what the page
// says the picture is.
//
// An empty list is printed as such rather than left out. "The page carries no
// pictures" and "nobody asked for them" are different answers, and only one
// of them is a finding about the site.
func writeImages(b *strings.Builder, asked bool, images []firecrawl.Image) {
	if !asked {
		return
	}
	if len(images) == 0 {
		b.WriteString("pictures on this page: none\n")
		return
	}
	fmt.Fprintf(b, "pictures on this page — %d, with what the page says each one is. Only the "+
		"text beside an address ties that image to a subject; an image with nothing said about "+
		"it identifies nobody:\n", len(images))
	shown := 0
	for _, img := range images {
		if img.URL == "" || strings.HasPrefix(img.URL, "data:") {
			// An inline image has no address to copy it from, and nothing
			// here can put one on a node.
			continue
		}
		if shown == maxImagesListed {
			// Said rather than done quietly: a gallery page is a page whose
			// pictures were not all offered, and a reader who does not know
			// that reads the list as the whole of them.
			fmt.Fprintf(b, "  … and %d more, not listed — this page is a gallery; read it for its "+
				"text and take a picture from a page that is about one subject\n", len(images)-shown)
			break
		}
		b.WriteString("  " + img.URL)
		if alt := strings.TrimSpace(img.Alt); alt != "" {
			b.WriteString("   — " + oneLine(alt))
		}
		b.WriteString("\n")
		shown++
	}
}

// maxImagesListed caps the inventory. Sixty is more pictures than any page
// about a subject has, and past it the list is a catalogue rather than an
// answer to "what does this page show".
const maxImagesListed = 60

// oneLine collapses whitespace so a caption cannot break the line grammar of
// the inventory.
func oneLine(s string) string { return strings.Join(strings.Fields(s), " ") }

func runApply(raw json.RawMessage) (string, error) {
	var args struct {
		Ops        string `json:"ops"`
		Write      bool   `json:"write"`
		Layer      string `json:"layer"`
		Partial    bool   `json:"partial"`
		KeepExport bool   `json:"keep_export"`
	}
	if err := json.Unmarshal(raw, &args); err != nil {
		return "", fmt.Errorf("bad arguments: %w", err)
	}
	if strings.TrimSpace(args.Ops) == "" {
		return "", errors.New("no ops file: pass `ops` with the path to a graph.ops.yaml")
	}
	opsPath, err := resolve(args.Ops)
	if err != nil {
		return "", err
	}
	if _, err := os.Stat(opsPath); err != nil {
		return "", fmt.Errorf("ops file %s: %w", opsPath, err)
	}

	cfg, err := loadConfig()
	if err != nil {
		return "", err
	}

	ctx, cancel := context.WithTimeout(context.Background(), applyTimeout)
	defer cancel()

	start := time.Now()
	res, applyErr := graph.ApplyOpsFile(ctx, cfg.client(), opsPath, graph.ApplyOptions{
		LayerID:     args.Layer,
		DryRun:      !args.Write,
		Partial:     args.Partial,
		KeepExport:  args.KeepExport,
		WorkspaceID: cfg.WorkspaceID,
		GroupID:     cfg.GroupID,
	})

	var b strings.Builder
	fmt.Fprintf(&b, "%s %s\n\n", map[bool]string{true: "applying", false: "planning"}[args.Write], opsPath)
	if res != nil && res.Plan != nil {
		b.WriteString(res.Plan.Render())
		b.WriteString("\n")
	}
	if applyErr != nil {
		// The plan is the useful half even when the run failed, so it is
		// already in the buffer — the error goes after it, not instead.
		return "", fmt.Errorf("%s\n%w", b.String(), applyErr)
	}

	if !args.Write {
		b.WriteString("\ndry run only — nothing written; call again with write: true to apply\n")
		return b.String(), nil
	}
	fmt.Fprintf(&b, "\napplied %d action(s) in %s\n", len(res.Applied), time.Since(start).Round(time.Millisecond))
	for _, a := range res.Applied {
		if a.Create {
			// The one line a reader must not miss: a created record is not on
			// the layer, and this uuid is what finds it in Simulator.
			fmt.Fprintf(&b, "created %s [%s] as actor %s — a record of form %d, not a node on the layer\n",
				a.Path, a.Type, a.ActorID, a.FormID)
		}
	}
	if r := res.Result; r != nil {
		fmt.Fprintf(&b, "%s: %d hole(s) filled, %d actor(s) updated, %d record(s) created in total "+
			"across every run of this ops file (%d new this run)\n",
			filepath.Base(res.ResultPath), r.HolesFilled, r.ActorsUpdated, r.ActorsCreated, res.ResultAdded)
	}
	// Printed after the tally and before the stamps: a picture that was not
	// taken is the one part of a successful run that did less than the ops
	// file asked for, and nothing else in the output says so.
	writeWarnings(&b, res.PictureWarnings)
	writeWarnings(&b, res.ShareWarnings)
	if res.Stamped > 0 {
		fmt.Fprintf(&b, "stamped %d op(s) with their node id — the file replays by id from now on, "+
			"through any rename\n", res.Stamped)
	}
	writeWarnings(&b, res.StampWarnings)
	if e := res.Export; e != nil {
		fmt.Fprintf(&b, "export refreshed: %d nodes, %d types, %d with values → %s\n",
			e.Nodes, e.Types, e.NodesWithValues, filepath.Dir(e.ValuesPath))
		writeWarnings(&b, e.Warnings)
	}
	return b.String(), nil
}

// resolve turns a path from a tool call into an absolute one.
//
// Relative paths are relative to the directory the MCP client started the
// server in — the user's project, normally, which is what makes `dir: "."`
// mean anything. The launcher runs the server through `go run -C`, which
// leaves the process sitting in the module directory instead, so it passes
// the real one in MIGRATION_FACTORY_PLUGIN_CWD. Running the compiled binary directly sets no
// such variable and falls back to the process's own working directory, which
// is then already the right one.
func resolve(path string) (string, error) {
	path = strings.TrimSpace(path)
	if filepath.IsAbs(path) {
		return filepath.Clean(path), nil
	}
	if base := env(envCWD); base != "" {
		return filepath.Join(base, path), nil
	}
	return filepath.Abs(path)
}

// firstNonEmpty returns the first value that is not blank.
func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if s := strings.TrimSpace(v); s != "" {
			return s
		}
	}
	return ""
}

func writeWarnings(b *strings.Builder, warnings []string) {
	for _, w := range warnings {
		fmt.Fprintf(b, "warning: %s\n", w)
	}
}

// postStatementArgs is one post_statement call.
type postStatementArgs struct {
	AccountID string `json:"account_id"`
	ActorID   string `json:"actor_id"`
	Path      string `json:"path"`
	RefPrefix string `json:"ref_prefix"`
	Timezone  string `json:"timezone"`
	DryRun    bool   `json:"dry_run"`
}

// runPostStatement records a parsed statement on an actor.
func runPostStatement(raw json.RawMessage) (string, error) {
	var args postStatementArgs
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &args); err != nil {
			return "", fmt.Errorf("bad arguments: %w", err)
		}
	}
	if strings.TrimSpace(args.AccountID) == "" {
		return "", errors.New("no account name: pass `account_id` with the account-name category to record under, e.g. \"Bank Statement\"")
	}
	if strings.TrimSpace(args.ActorID) == "" {
		return "", errors.New("no actor: pass `actor_id` with the UUID of the actor the accounts belong to")
	}
	if strings.TrimSpace(args.Path) == "" {
		return "", errors.New("no statement: pass `path` with the .jsonl a statement parser produced")
	}
	path, err := resolve(args.Path)
	if err != nil {
		return "", err
	}
	cfg, err := loadConfig()
	if err != nil {
		return "", err
	}
	if strings.TrimSpace(cfg.WorkspaceID) == "" {
		return "", errors.New("no workspace: an account pair is workspace-level, so set SIM_WORKSPACE_ID in the MCP server's env")
	}

	ctx, cancel := context.WithTimeout(context.Background(), postStatementTimeout)
	defer cancel()

	start := time.Now()
	res, err := ledger.Post(ctx, cfg.client(), ledger.Options{
		AccountName: args.AccountID,
		ActorID:     args.ActorID,
		Path:        path,
		WorkspaceID: cfg.WorkspaceID,
		GroupID:     cfg.GroupID,
		RefPrefix:   args.RefPrefix,
		Timezone:    args.Timezone,
		DryRun:      args.DryRun,
	})
	if err != nil {
		return "", err
	}
	return renderPostStatement(res, args, start), nil
}

func renderPostStatement(res *ledger.Result, args postStatementArgs, start time.Time) string {
	var b strings.Builder
	what := "posted"
	if args.DryRun {
		what = "would post"
	}
	fmt.Fprintf(&b, "%s: %d rows, %s %d transactions", args.AccountID, res.Records, what, res.Posted)
	if res.Duplicate > 0 {
		fmt.Fprintf(&b, ", %d already there", res.Duplicate)
	}
	if res.Skipped > 0 {
		fmt.Fprintf(&b, ", %d failed", res.Skipped)
	}
	fmt.Fprintf(&b, " (%s)\n", time.Since(start).Round(time.Millisecond))

	for _, c := range res.Currencies {
		fmt.Fprintf(&b, "\n%s  %d rows\n", c.Currency, c.Rows)
		fmt.Fprintf(&b, "  debit  %14.2f   -> %s\n", c.Debit, c.DebitID)
		fmt.Fprintf(&b, "  credit %14.2f   -> %s\n", c.Credit, c.CreditID)
		fmt.Fprintf(&b, "  net    %14.2f   (credit - debit, what the card shows)\n", c.Credit-c.Debit)
	}
	if len(res.Currencies) > 1 {
		b.WriteString("\nMore than one currency: each has its own pair, and only the\n" +
			"per-currency turnovers above are comparable with the statement.\n")
	}
	if args.DryRun {
		b.WriteString("\nNothing was written. Check these turnovers against the totals the\n" +
			"statement prints about itself, then run again without dry_run.\n")
	}
	writeWarnings(&b, res.Warnings)
	return b.String()
}
