package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// These tests drive resolveClaudeCLI through its injected
// claudeCLIProbe so the Windows-only install layouts that caused the
// original `exec: "claude": executable file not found in %PATH%`
// failure can be exercised regardless of the host this runs on.

func fakeProbe(goos string, home string, env map[string]string, present map[string]bool) claudeCLIProbe {
	return claudeCLIProbe{
		GOOS: goos,
		LookPath: func(string) (string, error) {
			return "", os.ErrNotExist
		},
		IsExecutableFile: func(path string) bool { return present[path] },
		HomeDir:          func() (string, error) { return home, nil },
		Getenv:           func(k string) string { return env[k] },
	}
}

// The reported failure mode: native installer put the CLI in
// %USERPROFILE%\.local\bin but that setx'd PATH change never reached
// this process, so LookPath fails and we must still find it.
func TestResolveFindsNativeInstallWhenNotOnPath(t *testing.T) {
	home := `C:\Users\member`
	want := filepath.Join(home, ".local", "bin", "claude.exe")
	p := fakeProbe("windows", home, nil, map[string]bool{want: true})

	cli, err := resolveClaudeCLI("", p)
	if err != nil {
		t.Fatalf("resolveClaudeCLI: %v", err)
	}
	if cli.Path != want {
		t.Fatalf("want %q, got %q", want, cli.Path)
	}
	if len(cli.Prefix) != 0 {
		t.Fatalf("an .exe needs no cmd.exe host, got prefix %v", cli.Prefix)
	}
	if !strings.Contains(cli.Source, "known install location") {
		t.Fatalf("source should say it came from a fallback probe, got %q", cli.Source)
	}
}

// npm -g installs expose the CLI as claude.cmd under %APPDATA%\npm,
// which CreateProcess cannot launch directly -- it has to go through
// cmd.exe /c.
func TestResolveRoutesNpmCmdShimThroughCmdExe(t *testing.T) {
	home := `C:\Users\member`
	appdata := `C:\Users\member\AppData\Roaming`
	want := filepath.Join(appdata, "npm", "claude.cmd")
	p := fakeProbe("windows", home, map[string]string{"APPDATA": appdata}, map[string]bool{want: true})

	cli, err := resolveClaudeCLI("", p)
	if err != nil {
		t.Fatalf("resolveClaudeCLI: %v", err)
	}
	if len(cli.Prefix) != 2 || cli.Prefix[0] != "/c" || cli.Prefix[1] != want {
		t.Fatalf("want cmd.exe /c %q, got path=%q prefix=%v", want, cli.Path, cli.Prefix)
	}
	cmd := cli.newCommand("setup-token")
	last := cmd.Args[len(cmd.Args)-1]
	if last != "setup-token" {
		t.Fatalf("caller args must come after the /c shim path, got %v", cmd.Args)
	}
}

// PATH stays authoritative when it works, so a healthy install keeps
// exactly the pre-change behavior.
func TestResolvePrefersPathWhenAvailable(t *testing.T) {
	p := fakeProbe("windows", `C:\Users\member`, nil, map[string]bool{
		filepath.Join(`C:\Users\member`, ".local", "bin", "claude.exe"): true,
	})
	p.LookPath = func(string) (string, error) { return `C:\tools\claude.exe`, nil }

	cli, err := resolveClaudeCLI("", p)
	if err != nil {
		t.Fatalf("resolveClaudeCLI: %v", err)
	}
	if cli.Path != `C:\tools\claude.exe` || cli.Source != "found on PATH" {
		t.Fatalf("PATH hit should win, got %+v", cli)
	}
}

// An explicit -claude-path wins over everything else.
func TestResolveExplicitPathWins(t *testing.T) {
	explicit := `D:\custom\claude.exe`
	p := fakeProbe("windows", `C:\Users\member`, nil, map[string]bool{explicit: true})
	p.LookPath = func(string) (string, error) { return `C:\tools\claude.exe`, nil }

	cli, err := resolveClaudeCLI(explicit, p)
	if err != nil {
		t.Fatalf("resolveClaudeCLI: %v", err)
	}
	if cli.Path != explicit {
		t.Fatalf("want explicit %q, got %q", explicit, cli.Path)
	}
}

// A bad explicit path must fail loudly instead of silently falling
// back to some other install the member didn't ask for.
func TestResolveExplicitPathMissingIsAnError(t *testing.T) {
	p := fakeProbe("windows", `C:\Users\member`, nil, map[string]bool{
		`C:\tools\claude.exe`: true,
	})
	p.LookPath = func(string) (string, error) { return `C:\tools\claude.exe`, nil }

	if _, err := resolveClaudeCLI(`D:\typo\claude.exe`, p); err == nil {
		t.Fatal("expected an error for a nonexistent explicit path, got none")
	}
}

// The extensionless `claude` shipped next to a Windows native install
// is a POSIX shell script; CreateProcess cannot run it, so it must not
// be selected.
func TestResolveSkipsExtensionlessScriptOnWindows(t *testing.T) {
	home := `C:\Users\member`
	p := fakeProbe("windows", home, nil, map[string]bool{
		filepath.Join(home, ".local", "bin", "claude"): true,
	})
	if _, err := resolveClaudeCLI("", p); err == nil {
		t.Fatal("extensionless launcher must not be treated as runnable on Windows")
	}
}

// When nothing is found the member needs to know where we looked and
// how to override it, since this is the most likely first-run failure.
func TestNotFoundErrorIsActionable(t *testing.T) {
	home := `C:\Users\member`
	p := fakeProbe("windows", home, map[string]string{"APPDATA": `C:\Users\member\AppData\Roaming`}, nil)

	_, err := resolveClaudeCLI("", p)
	if err == nil {
		t.Fatal("expected a not-found error")
	}
	// Separators are normalized before comparison: filepath.Join uses
	// the *host* separator, so these paths render with '/' when the
	// test runs on Linux even though the real Windows binary emits
	// backslashes. What matters is that each location is listed.
	msg := strings.ReplaceAll(err.Error(), "/", `\`)
	for _, want := range []string{`%PATH%`, `.local\bin`, `AppData\Roaming\npm`, "-claude-path", claudeCLIPathEnv, "where claude"} {
		if !strings.Contains(msg, want) {
			t.Fatalf("error message missing %q:\n%s", want, msg)
		}
	}
}

// Non-Windows hosts (used by developers running this agent's code
// locally) resolve a plain `claude` and never involve cmd.exe.
func TestResolveOnNonWindowsUsesPlainName(t *testing.T) {
	home := "/home/member"
	want := filepath.Join(home, ".local", "bin", "claude")
	p := fakeProbe("linux", home, nil, map[string]bool{want: true})

	cli, err := resolveClaudeCLI("", p)
	if err != nil {
		t.Fatalf("resolveClaudeCLI: %v", err)
	}
	if cli.Path != want || len(cli.Prefix) != 0 {
		t.Fatalf("want bare %q, got %+v", want, cli)
	}
}

// defaultClaudeCLIProbe must reject directories: a `claude` folder on
// PATH-adjacent locations should never be launched.
func TestDefaultProbeRejectsDirectories(t *testing.T) {
	dir := t.TempDir()
	p := defaultClaudeCLIProbe()
	if p.IsExecutableFile(dir) {
		t.Fatal("a directory must not be considered a runnable file")
	}
	f := filepath.Join(dir, "claude.exe")
	if err := os.WriteFile(f, []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	if !p.IsExecutableFile(f) {
		t.Fatalf("expected %s to be runnable", f)
	}
}
