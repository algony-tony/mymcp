package setup

import (
	"os/exec"
	"strings"
)

// System is the seam for every external command. apply.go, preflight.go,
// prompt.go and doctor.go must go through it; nothing else may exec.
type System interface {
	Run(name string, args ...string) (string, error)
	LookPath(file string) (string, error)
}

type realSystem struct{}

// RealSystem returns the production System.
func RealSystem() System { return realSystem{} }

func (realSystem) Run(name string, args ...string) (string, error) {
	out, err := exec.Command(name, args...).CombinedOutput()
	return strings.TrimRight(string(out), "\n"), err
}

func (realSystem) LookPath(file string) (string, error) { return exec.LookPath(file) }
