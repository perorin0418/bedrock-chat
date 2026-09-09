package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// copySelfToFixedLocation is the direct equivalent of the .ps1
// predecessor's Copy-SelfToFixedLocation: copies this running .exe to
// a fixed per-user location so the Task Scheduler entry keeps working
// even if the admin-distributed folder this exe was originally run
// from gets deleted, moved, or was on removable/network media.
// Returns the path the Task Scheduler action should point at (the
// copy on success, or the original executable path as a best-effort
// fallback if copying fails for any reason).
//
// Also self-updates an already-installed copy: called on every run
// (registerSelfAsScheduledTask no longer skips this just because the
// scheduled task already exists -- see its comments), so an admin who
// rebuilds and redistributes a newer .exe gets it picked up the next
// time a member double-clicks the new one, or (once installed) the
// next scheduled re-run, without any manual uninstall/reinstall step.
// The installed copy is only overwritten when its file size differs
// from this running exe's -- a cheap, dependency-free staleness check
// (no need to hash the whole binary) that catches the common real
// case (a rebuilt .exe almost never happens to land on the exact same
// byte count) while avoiding a redundant disk write when they already
// match, e.g. every routine scheduled re-run once installed.
func copySelfToFixedLocation() string {
	selfPath, err := os.Executable()
	if err != nil {
		warnf("could not determine this program's own path (%v). The scheduled task will not be self-healing if this folder moves.", err)
		return ""
	}
	// Resolve symlinks (os.Executable can return a symlink path on some
	// setups) so the "already running from the fixed location" check
	// below compares real paths.
	if resolved, err := filepath.EvalSymlinks(selfPath); err == nil {
		selfPath = resolved
	}

	home, err := os.UserHomeDir()
	if err != nil {
		warnf("could not determine home directory (%v). The scheduled task will point at this exe's current path instead.", err)
		return selfPath
	}
	installDir := filepath.Join(home, ".claude", "claude_teams_member_agent")
	installedPath := filepath.Join(installDir, "claude_teams_member_agent.exe")

	if selfPath == installedPath {
		// Already running from the fixed location (e.g. a Task
		// Scheduler re-run) -- nothing to do; there's no separate
		// "source" copy to compare against.
		return selfPath
	}

	if needsUpdate, err := installedCopyNeedsUpdate(selfPath, installedPath); err != nil {
		warnf("could not check whether the installed copy at %s is up to date (%v). Leaving it as-is.", installedPath, err)
		return installedPath
	} else if !needsUpdate {
		// Installed copy already matches this exe's size -- skip the
		// redundant write. Common case for every routine scheduled
		// re-run once installed.
		return installedPath
	}

	if err := os.MkdirAll(installDir, 0o755); err != nil {
		warnf("could not create %s (%v). The scheduled task will point at this exe's current path instead; if this folder is later moved or deleted, re-run the exe from wherever it ends up.", installDir, err)
		return selfPath
	}
	if err := copyFile(selfPath, installedPath); err != nil {
		warnf("could not copy this exe to a fixed location (%v). The scheduled task will point at this exe's current path instead; if this folder is later moved or deleted, re-run the exe from wherever it ends up.", err)
		return selfPath
	}

	infof("Copied this exe to %s so the scheduled task keeps working even if this original folder is later moved or deleted.", installDir)
	return installedPath
}

// installedCopyNeedsUpdate reports whether installedPath is missing
// or its file size differs from selfPath's, i.e. whether
// copySelfToFixedLocation should (re)copy over it. Returns (true, nil)
// if installedPath does not exist yet (first-ever install, not an
// update). Comparing sizes rather than full content is a deliberate,
// cheap approximation: this only ever runs against builds of this
// same program (never arbitrary user files), so a byte-for-byte-equal
// but differently-sized-never case isn't a concern in practice, and a
// full-content hash would mean reading every byte of the exe on every
// single scheduled run just to confirm the overwhelmingly common
// "nothing changed" case.
func installedCopyNeedsUpdate(selfPath, installedPath string) (bool, error) {
	installedInfo, err := os.Stat(installedPath)
	if err != nil {
		if os.IsNotExist(err) {
			return true, nil
		}
		return false, err
	}
	selfInfo, err := os.Stat(selfPath)
	if err != nil {
		return false, err
	}
	return installedInfo.Size() != selfInfo.Size(), nil
}

func copyFile(src, dst string) error {
	data, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	return os.WriteFile(dst, data, 0o755)
}

// registerSelfAsScheduledTask is the direct equivalent of the .ps1
// predecessor's Register-SelfAsScheduledTask: re-runs this exe on a
// recurring interval via Windows Task Scheduler, without
// -registration-secret (already consumed) so the secret doesn't sit
// indefinitely in a Task Scheduler action's saved arguments.
// Idempotent for the schtasks entry itself: skips /Create if the task
// already exists, so calling this on every run is safe and
// self-healing if a member accidentally deletes the task. The
// self-update copy (copySelfToFixedLocation) always runs first,
// regardless of whether the task already exists -- see that
// function's comments: an admin who redistributes a rebuilt .exe
// needs the installed copy refreshed on the very next run (manual or
// scheduled) even though the task itself needs no changes.
func (a *appContext) registerSelfAsScheduledTask() {
	exePath := copySelfToFixedLocation()
	if exePath == "" {
		warnf("could not determine a path to register for the scheduled task; skipping Task Scheduler registration for this run.")
		return
	}

	checkCmd := exec.Command("schtasks", "/Query", "/TN", a.taskName)
	if err := checkCmd.Run(); err == nil {
		// Task already exists (schtasks /Query exits 0). The self-update
		// copy above still ran, but nothing else to do.
		return
	}

	infof("Registering Task Scheduler entry '%s' (every %d minute(s))...", a.taskName, a.taskIntervalMinutes)

	// Only re-pass flags that differ from this exe's own baked-in/
	// computed defaults (see BUILD.md: -api-endpoint and
	// -registration-secret are normally baked in via -ldflags, so the
	// scheduled re-run picks them up automatically without needing
	// them on the command line at all).
	//
	// This matters structurally, not just cosmetically: schtasks.exe's
	// /TR option has a hard 261-character limit (undocumented in
	// `schtasks /Create /?` but enforced -- confirmed empirically:
	// "Value for '/TR' option cannot be more than 261 character(s)").
	// The exe path alone
	// (%USERPROFILE%\.claude\claude_teams_member_agent\
	// claude_teams_member_agent.exe) plus a full set of quoted flags
	// (particularly -api-endpoint's execute-api URL) blows past that
	// easily. Passing only the non-default subset keeps the common
	// case (a normally-built, non-overridden distribution) at just the
	// exe path with zero extra flags. For the remaining case -- a long
	// -api-endpoint or -config-path/-credentials-path override that
	// alone would still overflow /TR -- see the environment-variable
	// fallback in setScheduledTaskEnvironment below instead of growing
	// the command line further.
	//
	// display-name is deliberately never re-passed here at all: it is
	// only ever consulted inside registerThisMachine (first run / a
	// disabled-token re-register), never on a normal scheduled run.
	//
	// -unattended is always passed, unconditionally: this scheduled
	// re-run has no one at the keyboard to press Enter, so it must
	// never block on the "press Enter to close" prompt main.go shows a
	// manual double-click run (see waitForEnterIfInteractive).
	args := []string{"-unattended"}
	if a.taskIntervalMinutes != 60 {
		args = append(args, "-task-interval-minutes", strconv.Itoa(a.taskIntervalMinutes))
	}
	if a.taskName != defaultTaskName {
		args = append(args, "-task-name", a.taskName)
	}
	taskRun := buildCommandLine(exePath, args)

	// Anything that could make /TR overflow 261 characters (a
	// non-default -api-endpoint, or explicit -config-path/
	// -credentials-path overrides) is passed via a per-user persistent
	// environment variable instead of a command-line flag. main.go
	// reads these as fallback defaults when the corresponding flag is
	// omitted, so the scheduled re-run behaves identically without
	// growing /TR at all. setx persists across logons (unlike `set`),
	// which the scheduled run needs since it isn't a child process of
	// this one.
	envOverrides := map[string]string{}
	if a.apiEndpoint != strings.TrimRight(defaultAPIEndpoint, "/") {
		envOverrides["CLAUDE_TEAMS_AGENT_API_ENDPOINT"] = a.apiEndpoint
	}
	if a.configPathOverridden {
		envOverrides["CLAUDE_TEAMS_AGENT_CONFIG_PATH"] = a.configPath
	}
	if a.credentialsPathOverridden {
		envOverrides["CLAUDE_TEAMS_AGENT_CREDENTIALS_PATH"] = a.credentialsPath
	}
	for name, value := range envOverrides {
		setxCmd := exec.Command("setx", name, value)
		if err := setxCmd.Run(); err != nil {
			warnf("could not persist %s via setx (%v); the scheduled run may not pick up this override correctly.", name, err)
		}
	}

	// /SC MINUTE /MO <n>: repeats every n minutes indefinitely (no
	// end-date equivalent needed, unlike the .ps1 predecessor's
	// New-ScheduledTaskTrigger workaround for an unbounded
	// -RepetitionDuration).
	// /ST <HH:MM>: start time; using "now" so the very first recurrence
	// is imminent rather than waiting up to taskIntervalMinutes for the
	// first automatic run.
	// /RL LIMITED: run with the member's normal (non-elevated)
	// privileges -- matches the .ps1 predecessor's Interactive logon
	// type reasoning: no admin rights or stored password needed, and it
	// naturally only fires while the member is logged in (matching how
	// %USERPROFILE%\.claude\.credentials.json is per-user anyway).
	// /F: overwrite without prompting if a stale entry with the same
	// name somehow exists despite the /Query check above (e.g. a race
	// with another concurrent run).
	now := time.Now()
	startTime := now.Format("15:04")
	createCmd := exec.Command("schtasks", "/Create",
		"/TN", a.taskName,
		"/TR", taskRun,
		"/SC", "MINUTE",
		"/MO", strconv.Itoa(a.taskIntervalMinutes),
		"/ST", startTime,
		"/RL", "LIMITED",
		"/F",
	)
	createCmd.Stdout = os.Stdout
	createCmd.Stderr = os.Stderr
	if err := createCmd.Run(); err != nil {
		warnf("could not auto-register Task Scheduler entry (%v). Usage was still reported for this run; re-run this exe later, or set up the recurring task manually, to keep reporting going.", err)
		return
	}
	infof("Task Scheduler entry '%s' registered.", a.taskName)
	infof("")
}

// buildCommandLine quotes exePath and each argument for schtasks'
// /TR, which takes one command-line string (schtasks.exe has no
// separate argv-array parameter, unlike CreateProcess or PowerShell's
// Register-ScheduledTaskAction).
func buildCommandLine(exePath string, args []string) string {
	quoted := quoteArg(exePath)
	for _, a := range args {
		quoted += " " + quoteArg(a)
	}
	return quoted
}

func quoteArg(s string) string {
	return `"` + s + `"`
}
