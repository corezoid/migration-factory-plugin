package graph

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// ResultFileName is the tally an apply keeps beside the ops file.
const ResultFileName = "result.json"

// RunResult is the running total of what applying this ops file has done to
// the graph: how many placeholder holes were filled, how many existing actors
// were updated, how many records were created, and which ones.
//
// The keys are the Russian phrases the report is read by; the field order is
// the order they are written in.
type RunResult struct {
	HolesFilled   int `json:"количество заполненных дырок"`
	ActorsUpdated int `json:"количество обновленных акторов"`
	ActorsCreated int `json:"количество созданных акторов"`

	// The id lists the counts are derived from — never assigned to, only
	// unioned into, which is what makes a second apply of the same file add
	// nothing instead of doubling the tally.
	Holes   []string `json:"заполненные дырки"`
	Updated []string `json:"обновленные акторы"`
	Created []string `json:"созданные акторы"`
}

// LoadResult reads the tally a previous run left. A missing file is an empty
// tally, not an error: the first apply of an ops file is the one that creates
// it.
func LoadResult(path string) (*RunResult, error) {
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return &RunResult{}, nil
	}
	if err != nil {
		return nil, fmt.Errorf("graph: read %s: %w", path, err)
	}
	res := &RunResult{}
	if err := json.Unmarshal(data, res); err != nil {
		return nil, fmt.Errorf("graph: %s is not readable as a result file — move it aside or "+
			"delete it to start the tally over: %w", path, err)
	}
	res.recount()
	return res, nil
}

// Add folds one run's applied actions into the tally and reports how many ids
// were new.
//
// The three lists are disjoint and an id joins the first one it qualifies
// for, in the same precedence the plan prints an action with: created beats
// hole-filled beats updated. That is what keeps a node counted once across
// runs — a hole filled today is a plain node tomorrow, and writing to it
// again must not make it both a filled hole and an updated actor.
func (r *RunResult) Add(applied []*Action) int {
	seen := r.index()
	added := 0
	for _, a := range applied {
		id := a.ActorID
		if id == "" || seen[id] {
			// A create that failed before the API answered has no id, and
			// there is nothing to record about it.
			continue
		}
		seen[id] = true
		added++
		switch {
		case a.Create:
			r.Created = append(r.Created, id)
		case a.FillsHole:
			r.Holes = append(r.Holes, id)
		default:
			r.Updated = append(r.Updated, id)
		}
	}
	r.recount()
	return added
}

// index is every id the tally already holds, whichever list it sits in.
func (r *RunResult) index() map[string]bool {
	seen := make(map[string]bool, len(r.Holes)+len(r.Updated)+len(r.Created))
	for _, list := range [][]string{r.Created, r.Holes, r.Updated} {
		for _, id := range list {
			seen[id] = true
		}
	}
	return seen
}

// recount makes the counts follow the lists, including for a file edited by
// hand: the lists are the record, the numbers are a convenience.
func (r *RunResult) recount() {
	r.HolesFilled = len(r.Holes)
	r.ActorsUpdated = len(r.Updated)
	r.ActorsCreated = len(r.Created)
	for _, list := range []*[]string{&r.Holes, &r.Updated, &r.Created} {
		if *list == nil {
			// An absent list would marshal as null; an empty run should read
			// as an empty list.
			*list = []string{}
		}
	}
}

// WriteResult saves the tally.
func WriteResult(path string, res *RunResult) error {
	res.recount()
	data, err := json.MarshalIndent(res, "", "    ")
	if err != nil {
		return fmt.Errorf("graph: render %s: %w", path, err)
	}
	return writeFile(path, append(data, '\n'))
}

// resultPath settles where the tally for a run lives: beside the ops file,
// which is also where the export it was written against sits. Ops handed in
// memory with no directory to anchor to get no tally.
func resultPath(opts ApplyOptions) string {
	switch {
	case opts.OpsPath != "":
		return filepath.Join(filepath.Dir(opts.OpsPath), ResultFileName)
	case opts.FromDir != "":
		return filepath.Join(opts.FromDir, ResultFileName)
	}
	return ""
}

// recordResult updates the tally beside the ops file with what this run
// applied.
//
// It runs after the writes and reports its own failure separately: the graph
// has already changed, and a tally that could not be saved must not read as a
// run that did not happen.
func recordResult(opts ApplyOptions, res *ApplyResult) error {
	path := resultPath(opts)
	if path == "" || len(res.Applied) == 0 {
		return nil
	}
	tally, err := LoadResult(path)
	if err != nil {
		return err
	}
	res.ResultAdded = tally.Add(res.Applied)
	if err := WriteResult(path, tally); err != nil {
		return err
	}
	res.Result, res.ResultPath = tally, path
	return nil
}
