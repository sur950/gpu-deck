// Package rulesschema validates gpudeck_rule params against the rule contract
// (libs/contracts/rules/*.schema.json, copied into schemas/ by tools/gen.sh and embedded).
package rulesschema

import (
	"embed"
	"encoding/json"
	"fmt"
	"io/fs"
	"path"
	"sort"
	"strings"
	"sync"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

//go:embed schemas/*.schema.json
var files embed.FS

const baseID = "https://sur950.github.io/gpu-deck/schemas/rules/v1/"

var (
	once     sync.Once
	compiled map[string]*jsonschema.Schema
	loadErr  error
)

// Types returns the rule types that have a params schema, sorted.
func Types() []string {
	load()
	out := make([]string, 0, len(compiled))
	for t := range compiled {
		out = append(out, t)
	}
	sort.Strings(out)
	return out
}

func load() {
	once.Do(func() {
		c := jsonschema.NewCompiler()
		entries, err := fs.ReadDir(files, "schemas")
		if err != nil {
			loadErr = err
			return
		}
		for _, e := range entries {
			raw, err := files.ReadFile(path.Join("schemas", e.Name()))
			if err != nil {
				loadErr = err
				return
			}
			doc, err := jsonschema.UnmarshalJSON(strings.NewReader(string(raw)))
			if err != nil {
				loadErr = fmt.Errorf("%s: %w", e.Name(), err)
				return
			}
			if err := c.AddResource(baseID+e.Name(), doc); err != nil {
				loadErr = fmt.Errorf("%s: %w", e.Name(), err)
				return
			}
		}
		compiled = map[string]*jsonschema.Schema{}
		for _, e := range entries {
			name := e.Name()
			if name == "rule.schema.json" || name == "layout.schema.json" || name == "target-selector.schema.json" {
				continue
			}
			s, err := c.Compile(baseID + name)
			if err != nil {
				loadErr = fmt.Errorf("compile %s: %w", name, err)
				return
			}
			compiled[strings.TrimSuffix(name, ".schema.json")] = s
		}
	})
}

// ValidateParams checks a params document (JSON text) against the schema for ruleType.
func ValidateParams(ruleType string, paramsJSON string) error {
	load()
	if loadErr != nil {
		return loadErr
	}
	s, ok := compiled[ruleType]
	if !ok {
		return fmt.Errorf("unknown rule type %q (known: %s)", ruleType, strings.Join(Types(), ", "))
	}
	var doc any
	if err := json.Unmarshal([]byte(paramsJSON), &doc); err != nil {
		return fmt.Errorf("params is not valid JSON: %w", err)
	}
	if err := s.Validate(doc); err != nil {
		return fmt.Errorf("params do not match the %s schema: %w", ruleType, err)
	}
	return nil
}
