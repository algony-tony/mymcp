package setup

import (
	_ "embed"
	"fmt"
	"strings"
	"text/template"
)

//go:embed templates/mymcp.service.in
var unitTemplate string

type unitFields struct {
	ServiceUser, ConfigDir, EnvPath, ExecPath string
}

// RenderUnit renders the main systemd unit. The recorder unit is NOT rendered
// here: src/mymcp/recorder/templates/mymcp-recorder.service.in stays the single
// source of truth for it, and apply.go shells out to
// `mymcp-recorder --install-unit` instead (see the design spec).
func RenderUnit(p *Plan) string {
	t := template.Must(template.New("unit").Parse(unitTemplate))
	var sb strings.Builder
	err := t.Execute(&sb, unitFields{
		ServiceUser: p.ServiceUser,
		ConfigDir:   p.ConfigDir,
		EnvPath:     p.EnvPath(),
		ExecPath:    p.ExecPath,
	})
	if err != nil {
		panic(fmt.Sprintf("bad embedded unit template: %v", err))
	}
	return sb.String()
}
