package provider

// Registry-style Markdown documentation generated from the provider schemas and the example
// files (examples/). `go run ./tools/docgen` writes docs/; TestDocsUpToDate fails when docs/
// is stale.

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/hashicorp/terraform-plugin-framework/datasource"
	dschema "github.com/hashicorp/terraform-plugin-framework/datasource/schema"
	"github.com/hashicorp/terraform-plugin-framework/provider"
	pschema "github.com/hashicorp/terraform-plugin-framework/provider/schema"
	"github.com/hashicorp/terraform-plugin-framework/resource"
	rschema "github.com/hashicorp/terraform-plugin-framework/resource/schema"
)

// GenerateDocs returns docs/ file contents keyed by relative path.
func GenerateDocs(examplesDir string) (map[string]string, error) {
	ctx := context.Background()
	p := New("docs")().(*gpudeckProvider)
	out := map[string]string{}

	var ps provider.SchemaResponse
	p.Schema(ctx, provider.SchemaRequest{}, &ps)
	var b strings.Builder
	b.WriteString("---\npage_title: \"gpudeck provider\"\n---\n\n# gpudeck provider\n\n")
	b.WriteString(ps.Schema.Description + "\n\n")
	b.WriteString("Authentication: a session token (`token` / `GPUDECK_TOKEN`, e.g. from `gpudeck login`) " +
		"or `username` + `password`, which the provider exchanges for a session token at start-up. " +
		"Requests use `Authorization: Bearer`, so no CSRF token is needed.\n\n")
	if err := example(&b, filepath.Join(examplesDir, "provider", "provider.tf")); err != nil {
		return nil, err
	}
	b.WriteString("## Schema\n\n### Optional\n\n")
	names := sortedKeys(ps.Schema.Attributes)
	for _, n := range names {
		a := ps.Schema.Attributes[n]
		fmt.Fprintf(&b, "- `%s` (%s%s) %s\n", n, providerAttrType(a), sensitive(a.IsSensitive()), a.GetMarkdownDescription())
	}
	b.WriteString("\n## Resources\n\n")
	for _, f := range p.Resources(ctx) {
		r := f()
		var md resource.MetadataResponse
		r.Metadata(ctx, resource.MetadataRequest{ProviderTypeName: "gpudeck"}, &md)
		fmt.Fprintf(&b, "- [`%s`](resources/%s.md)\n", md.TypeName, strings.TrimPrefix(md.TypeName, "gpudeck_"))
	}
	b.WriteString("\n## Data sources\n\n")
	for _, f := range p.DataSources(ctx) {
		d := f()
		var md datasource.MetadataResponse
		d.Metadata(ctx, datasource.MetadataRequest{ProviderTypeName: "gpudeck"}, &md)
		fmt.Fprintf(&b, "- [`%s`](data-sources/%s.md)\n", md.TypeName, strings.TrimPrefix(md.TypeName, "gpudeck_"))
	}
	out["index.md"] = b.String()

	for _, f := range p.Resources(ctx) {
		r := f()
		var md resource.MetadataResponse
		r.Metadata(ctx, resource.MetadataRequest{ProviderTypeName: "gpudeck"}, &md)
		var sr resource.SchemaResponse
		r.Schema(ctx, resource.SchemaRequest{}, &sr)
		doc, err := resourceDoc(md.TypeName, sr.Schema, filepath.Join(examplesDir, "resources", md.TypeName))
		if err != nil {
			return nil, err
		}
		out["resources/"+strings.TrimPrefix(md.TypeName, "gpudeck_")+".md"] = doc
	}
	for _, f := range p.DataSources(ctx) {
		d := f()
		var md datasource.MetadataResponse
		d.Metadata(ctx, datasource.MetadataRequest{ProviderTypeName: "gpudeck"}, &md)
		var sr datasource.SchemaResponse
		d.Schema(ctx, datasource.SchemaRequest{}, &sr)
		doc, err := dataSourceDoc(md.TypeName, sr.Schema, filepath.Join(examplesDir, "data-sources", md.TypeName))
		if err != nil {
			return nil, err
		}
		out["data-sources/"+strings.TrimPrefix(md.TypeName, "gpudeck_")+".md"] = doc
	}
	return out, nil
}

func resourceDoc(name string, s rschema.Schema, exDir string) (string, error) {
	var b strings.Builder
	fmt.Fprintf(&b, "---\npage_title: \"%s Resource\"\n---\n\n# %s (Resource)\n\n%s\n\n", name, name, s.MarkdownDescription)
	if err := example(&b, filepath.Join(exDir, "resource.tf")); err != nil {
		return "", err
	}
	b.WriteString("## Schema\n\n")
	var req, opt, ro []string
	for _, n := range sortedKeys(s.Attributes) {
		a := s.Attributes[n]
		line := fmt.Sprintf("- `%s` (%s%s) %s", n, resourceAttrType(a), sensitive(a.IsSensitive()), a.GetMarkdownDescription())
		if nested, ok := a.(rschema.ListNestedAttribute); ok {
			line += "\n" + nestedList(nested.NestedObject.Attributes)
		}
		switch {
		case a.IsRequired():
			req = append(req, line)
		case a.IsOptional():
			opt = append(opt, line)
		default:
			ro = append(ro, line)
		}
	}
	section(&b, "Required", req)
	section(&b, "Optional", opt)
	section(&b, "Read-Only", ro)
	if raw, err := os.ReadFile(filepath.Join(exDir, "import.sh")); err == nil {
		b.WriteString("## Import\n\n```shell\n" + string(raw) + "```\n\n" +
			"Attributes equal to their server default are left unset after import.\n")
	} else if !os.IsNotExist(err) {
		return "", err
	}
	return b.String(), nil
}

func nestedList(attrs map[string]rschema.Attribute) string {
	var lines []string
	for _, n := range sortedKeys(attrs) {
		a := attrs[n]
		req := "Optional"
		if a.IsRequired() {
			req = "Required"
		}
		lines = append(lines, fmt.Sprintf("  - `%s` (%s, %s) %s", n, resourceAttrType(a), req, a.GetMarkdownDescription()))
	}
	return strings.Join(lines, "\n")
}

func dataSourceDoc(name string, s dschema.Schema, exDir string) (string, error) {
	var b strings.Builder
	fmt.Fprintf(&b, "---\npage_title: \"%s Data Source\"\n---\n\n# %s (Data Source)\n\n%s\n\n", name, name, s.MarkdownDescription)
	if err := example(&b, filepath.Join(exDir, "data-source.tf")); err != nil {
		return "", err
	}
	b.WriteString("## Schema\n\n")
	var opt, ro []string
	for _, n := range sortedKeys(s.Attributes) {
		a := s.Attributes[n]
		line := fmt.Sprintf("- `%s` (%s) %s", n, dataAttrType(a), a.GetMarkdownDescription())
		if nested, ok := a.(dschema.ListNestedAttribute); ok {
			var sub []string
			for _, sn := range sortedKeys(nested.NestedObject.Attributes) {
				sa := nested.NestedObject.Attributes[sn]
				sub = append(sub, fmt.Sprintf("  - `%s` (%s) %s", sn, dataAttrType(sa), sa.GetMarkdownDescription()))
			}
			line += "\n" + strings.Join(sub, "\n")
		}
		if a.IsOptional() {
			opt = append(opt, line)
		} else {
			ro = append(ro, line)
		}
	}
	section(&b, "Optional", opt)
	section(&b, "Read-Only", ro)
	return b.String(), nil
}

func example(b *strings.Builder, file string) error {
	raw, err := os.ReadFile(file)
	if err != nil {
		return fmt.Errorf("example %s: %w", file, err)
	}
	b.WriteString("## Example Usage\n\n```terraform\n" + string(raw) + "```\n\n")
	return nil
}

func section(b *strings.Builder, title string, lines []string) {
	if len(lines) == 0 {
		return
	}
	b.WriteString("### " + title + "\n\n" + strings.Join(lines, "\n") + "\n\n")
}

func sensitive(s bool) string {
	if s {
		return ", Sensitive"
	}
	return ""
}

func typeName(t string) string {
	switch {
	case strings.Contains(t, "Normalized"):
		return "String, JSON"
	case strings.HasPrefix(t, "ListNested"):
		return "Attributes List"
	case strings.HasPrefix(t, "List"):
		return "List of String"
	case strings.HasPrefix(t, "Map"):
		return "Map of String"
	case strings.HasPrefix(t, "Int64"):
		return "Number"
	case strings.HasPrefix(t, "Float64"):
		return "Number"
	case strings.HasPrefix(t, "Bool"):
		return "Boolean"
	}
	return "String"
}

func resourceAttrType(a rschema.Attribute) string {
	if s, ok := a.(rschema.StringAttribute); ok && s.CustomType != nil {
		return typeName("Normalized")
	}
	return typeName(strings.TrimPrefix(fmt.Sprintf("%T", a), "schema."))
}

func dataAttrType(a dschema.Attribute) string {
	if s, ok := a.(dschema.StringAttribute); ok && s.CustomType != nil {
		return typeName("Normalized")
	}
	return typeName(strings.TrimPrefix(fmt.Sprintf("%T", a), "schema."))
}

func providerAttrType(a pschema.Attribute) string {
	return typeName(strings.TrimPrefix(fmt.Sprintf("%T", a), "schema."))
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
