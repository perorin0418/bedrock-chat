package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

// Why this file exists: registerThisMachine used to shell out with a
// bare exec.Command("claude", "setup-token"), which on Windows only
// ever resolves through %PATH% x %PATHEXT%. That fails outright --
// `exec: "claude": executable file not found in %PATH%` -- in three
// perfectly normal member setups:
//
//  1. Native installer: the CLI lands in %USERPROFILE%\.local\bin, and
//     the installer's setx-based PATH edit only reaches *newly
//     created* processes. A console (or a schtasks-launched run)
//     started before/without that refresh never sees it.
//  2. npm -g install: the real entry points are claude.cmd/claude.ps1
//     under %APPDATA%\npm, a directory that nvm-windows switching (or
//     any environment that doesn't inherit the user PATH) routinely
//     leaves out.
//  3. Our own scheduled re-run: the .vbs -> schtasks path runs with a
//     thinner environment than an interactive logon.
//
// So instead of trusting %PATH% alone we do PATH first, then probe the
// handful of well-known install locations directly. This is only ever
// a *fallback*: a member with a working PATH keeps the exact previous
// behavior.

// claudeCLIPathEnv lets a member (or admin, via the scheduled task's
// environment) pin an explicit CLI location when their install lives
// somewhere none of the probes below know about. The -claude-path
// flag takes precedence over it.
const claudeCLIPathEnv = "CLAUDE_TEAMS_AGENT_CLAUDE_PATH"

// claudeCLI is a resolved, ready-to-run invocation of the Claude Code
// CLI. Path/Args are kept separate from the caller's own arguments so
// the .cmd/.bat "needs a cmd.exe host" case (see newCommand) stays an
// implementation detail of this file.
type claudeCLI struct {
	// Path is the executable to launch (either the CLI itself, or
	// cmd.exe when the CLI turned out to be a batch script).
	Path string
	// Prefix holds any arguments that must precede the CLI's own
	// arguments (e.g. "/c", "C:\...\claude.cmd").
	Prefix []string
	// Source describes how this was found, for operator-facing logs.
	Source string
}

// newCommand builds an *exec.Cmd running the CLI with args.
func (c claudeCLI) newCommand(args ...string) *exec.Cmd {
	full := append(append([]string{}, c.Prefix...), args...)
	return exec.Command(c.Path, full...)
}

// Display renders the resolved invocation for log lines.
func (c claudeCLI) Display() string {
	if len(c.Prefix) == 0 {
		return c.Path
	}
	return c.Path + " " + strings.Join(c.Prefix, " ")
}

// claudeCLIProbe is the OS/filesystem-dependent slice of resolution,
// injected so the resolver itself is testable on any platform (the
// real Windows-only install layouts can't be exercised on the Linux CI
// this repo builds on otherwise).
type claudeCLIProbe struct {
	// GOOS is the target platform ("windows", "linux", ...).
	GOOS string
	// LookPath resolves a bare command name through PATH (+PATHEXT on
	// Windows); os/exec.LookPath in production.
	LookPath func(string) (string, error)
	// IsExecutableFile reports whether path exists and is a plain file
	// we could launch.
	IsExecutableFile func(string) bool
	// HomeDir is the user's home directory; probing is skipped if it
	// returns an error.
	HomeDir func() (string, error)
	// Getenv resolves environment variables (APPDATA, LOCALAPPDATA,
	// ProgramFiles) used by the well-known-location probes.
	Getenv func(string) string
}

func defaultClaudeCLIProbe() claudeCLIProbe {
	return claudeCLIProbe{
		GOOS:     runtime.GOOS,
		LookPath: exec.LookPath,
		IsExecutableFile: func(path string) bool {
			info, err := os.Stat(path)
			if err != nil || info.IsDir() {
				return false
			}
			if runtime.GOOS == "windows" {
				return true
			}
			return info.Mode().Perm()&0o111 != 0
		},
		HomeDir: os.UserHomeDir,
		Getenv:  os.Getenv,
	}
}

// claudeExecutableNames lists the launchable file names to try inside
// each candidate directory, most-preferred first. On Windows the
// extensionless `claude` shipped alongside the native install is a
// POSIX shell script for Git Bash -- CreateProcess can't run it -- so
// it is deliberately absent here.
func claudeExecutableNames(goos string) []string {
	if goos == "windows" {
		return []string{"claude.exe", "claude.cmd", "claude.bat"}
	}
	return []string{"claude"}
}

// claudeCandidateDirs returns the well-known install directories to
// probe after PATH resolution fails, most-likely-first.
func claudeCandidateDirs(p claudeCLIProbe) []string {
	var dirs []string
	add := func(parts ...string) {
		if parts[0] == "" {
			return
		}
		dirs = append(dirs, filepath.Join(parts...))
	}

	if home, err := p.HomeDir(); err == nil && home != "" {
		// Native installer (`claude install`, curl|bash installer).
		add(home, ".local", "bin")
		// Older/alternate native layout.
		add(home, ".claude", "local")
		add(home, ".claude", "bin")
		if p.GOOS != "windows" {
			// npm --prefix ~/.npm-global and nodenv/asdf shims.
			add(home, ".npm-global", "bin")
			add(home, ".local", "share", "npm", "bin")
		}
	}
	if p.GOOS == "windows" {
		// npm -g default location.
		add(p.Getenv("APPDATA"), "npm")
		// npm when a per-user Node install redirects the global prefix.
		add(p.Getenv("LOCALAPPDATA"), "npm")
		// Native installer's machine-wide variant.
		add(p.Getenv("LOCALAPPDATA"), "Programs", "claude")
		add(p.Getenv("ProgramFiles"), "nodejs")
	} else {
		dirs = append(dirs, "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin")
	}
	return dirs
}

// resolveClaudeCLI finds a runnable Claude Code CLI.
//
// Order: explicit override (flag/env) -> PATH -> well-known install
// directories. The explicit override is never silently ignored: if it
// is set but unusable we fail loudly rather than falling back, so a
// member who pinned a path doesn't end up running some other install
// without noticing.
func resolveClaudeCLI(explicit string, p claudeCLIProbe) (claudeCLI, error) {
	explicit = strings.TrimSpace(explicit)
	if explicit != "" {
		if !p.IsExecutableFile(explicit) {
			return claudeCLI{}, fmt.Errorf(
				"the Claude Code CLI path you specified (%s) does not exist or is not a runnable file. Check the path, or drop the override to let this program search %%PATH%% and the usual install locations",
				explicit,
			)
		}
		return wrapClaudeCLI(explicit, "explicitly specified", p.GOOS), nil
	}

	if path, err := p.LookPath("claude"); err == nil && strings.TrimSpace(path) != "" {
		return wrapClaudeCLI(path, "found on PATH", p.GOOS), nil
	}

	names := claudeExecutableNames(p.GOOS)
	for _, dir := range claudeCandidateDirs(p) {
		for _, name := range names {
			candidate := filepath.Join(dir, name)
			if p.IsExecutableFile(candidate) {
				return wrapClaudeCLI(candidate, "found at a known install location (not on PATH)", p.GOOS), nil
			}
		}
	}

	return claudeCLI{}, fmt.Errorf("%s", claudeCLINotFoundMessage(p))
}

// wrapClaudeCLI routes .cmd/.bat entry points (how `npm -g install`
// exposes the CLI on Windows) through cmd.exe, which is the only way
// CreateProcess can run a batch script.
func wrapClaudeCLI(path, source, goos string) claudeCLI {
	if goos == "windows" {
		switch strings.ToLower(filepath.Ext(path)) {
		case ".cmd", ".bat":
			comspec := os.Getenv("COMSPEC")
			if strings.TrimSpace(comspec) == "" {
				comspec = "cmd.exe"
			}
			return claudeCLI{Path: comspec, Prefix: []string{"/c", path}, Source: source}
		}
	}
	return claudeCLI{Path: path, Source: source}
}

// claudeCLINotFoundMessage keeps the "nothing worked" text in one
// place: it has to tell the member both what was tried and what to do,
// because this is the single most likely first-run failure.
func claudeCLINotFoundMessage(p claudeCLIProbe) string {
	var b strings.Builder
	b.WriteString("could not find the Claude Code CLI ('claude'). It is not on ")
	if p.GOOS == "windows" {
		b.WriteString("%PATH%")
	} else {
		b.WriteString("$PATH")
	}
	b.WriteString(", and it was not in any of the usual install locations:\n")
	for _, dir := range claudeCandidateDirs(p) {
		b.WriteString("  - " + dir + "\n")
	}
	b.WriteString("Fix one of the following and re-run this program:\n")
	b.WriteString("  - Install Claude Code, then open a NEW terminal window (a fresh install's PATH change does not reach already-open windows).\n")
	if p.GOOS == "windows" {
		b.WriteString("  - Check where it is installed with: where claude\n")
	} else {
		b.WriteString("  - Check where it is installed with: which claude\n")
	}
	b.WriteString("  - Or pass the full path directly: -claude-path \"C:\\path\\to\\claude.exe\" (or set " + claudeCLIPathEnv + ").")
	return b.String()
}
