package setup

import (
	"fmt"
	"strings"
)

// fakeSystem records every exec and answers from canned tables. It is the
// single stub boundary for the whole package: nothing else in setup/ execs.
type fakeSystem struct {
	Calls    []string          // "systemctl daemon-reload"
	Outputs  map[string]string // exact command line -> stdout
	Errors   map[string]error  // exact command line -> error
	Paths    map[string]string // LookPath name -> resolved path
	LookErrs map[string]error
}

func newFakeSystem() *fakeSystem {
	return &fakeSystem{
		Outputs:  map[string]string{},
		Errors:   map[string]error{},
		Paths:    map[string]string{},
		LookErrs: map[string]error{},
	}
}

func (f *fakeSystem) Run(name string, args ...string) (string, error) {
	line := strings.TrimSpace(name + " " + strings.Join(args, " "))
	f.Calls = append(f.Calls, line)
	if err, ok := f.Errors[line]; ok {
		return f.Outputs[line], err
	}
	return f.Outputs[line], nil
}

func (f *fakeSystem) LookPath(file string) (string, error) {
	if err, ok := f.LookErrs[file]; ok {
		return "", err
	}
	if p, ok := f.Paths[file]; ok {
		return p, nil
	}
	return "", fmt.Errorf("exec: %q: executable file not found in $PATH", file)
}

func (f *fakeSystem) ran(line string) bool {
	for _, c := range f.Calls {
		if c == line {
			return true
		}
	}
	return false
}
