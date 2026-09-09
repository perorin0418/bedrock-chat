package main

import (
	"encoding/binary"
	"encoding/xml"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
	"unicode/utf16"
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

// launcherVBScriptName is the fixed filename of the hidden-window
// launcher script written alongside the installed .exe (see
// writeLauncherVBScript). Kept as a named constant since both
// writeLauncherVBScript and registerSelfAsScheduledTask need the exact
// same path.
const launcherVBScriptName = "claude_teams_member_agent_launcher.vbs"

// writeLauncherVBScript (re)writes a small WScript.Shell-based .vbs
// next to installedExePath that launches it with WindowStyle 0
// (hidden) and waits for it to exit. This is what the Task Scheduler
// entry actually points at (via wscript.exe -- see
// registerSelfAsScheduledTask), instead of the .exe directly.
//
// Why this exists at all: schtasks has no window-visibility option of
// its own, and this .exe is deliberately built with the default
// console subsystem (not windowsgui) because a manual double-click run
// still needs a visible console for the first-run 'claude setup-token'
// browser-approval prompt and any error output (see main.go). A
// windowsgui build would hide the console for *every* run mode, not
// just the scheduled one, silently swallowing that interactive flow.
// A runtime GetConsoleWindow+ShowWindow(SW_HIDE) approach was tried
// and rejected too: the console is still momentarily created and shown
// by the OS before this program's own code gets a chance to hide it,
// so a brief flash is still visible on every scheduled run -- exactly
// what this feature exists to prevent. Routing the scheduled launch
// through WScript.Shell.Run's WindowStyle=0 instead avoids a console
// ever being created in the first place for that run, which is the
// only way to avoid the flash entirely.
//
// Regenerated on every run alongside the self-update copy in
// copySelfToFixedLocation (not just once at first install), so a
// rebuilt/redistributed .exe -- and any changed exeArgs, e.g. after a
// -task-interval-minutes/-task-name override -- also gets an
// up-to-date launcher without a separate migration step, and so a
// member who somehow lost/edited the .vbs gets a fresh, correct copy
// automatically on the next run.
//
// The .vbs is deliberately minimal (a single WScript.Shell.Run call,
// no other logic) precisely because it can't be code-signed the way
// the .exe can: keeping it to one auditable line limits what an admin
// or a security reviewer needs to trust in an unsigned script sitting
// next to a signed binary.
func writeLauncherVBScript(installDir, installedExePath string, exeArgs []string) (string, error) {
	vbsPath := filepath.Join(installDir, launcherVBScriptName)
	// WScript.Shell.Run(command, windowStyle, waitOnReturn):
	//   windowStyle 0  -- hidden, no window ever shown for this process.
	//   waitOnReturn true -- blocks until the launched .exe exits, so
	//   Task Scheduler's own "already running" duplicate-instance
	//   handling still applies to the .vbs's own lifetime the same way
	//   it applied to the .exe's when schtasks pointed at it directly.
	// The whole command string (exe path + args) is built with
	// buildCommandLine (same quoting buildCommandLine already uses for
	// schtasks' /TR) and then escaped once more for VBScript's string
	// literal syntax: wrapped in double quotes, with any embedded
	// double quote doubled (VBScript's own escaping convention).
	command := buildCommandLine(installedExePath, exeArgs)
	vbsQuotedCommand := `"` + strings.ReplaceAll(command, `"`, `""`) + `"`
	script := "Set shell = CreateObject(\"WScript.Shell\")\r\n" +
		"shell.Run " + vbsQuotedCommand + ", 0, True\r\n"
	if err := os.WriteFile(vbsPath, []byte(script), 0o644); err != nil {
		return "", err
	}
	return vbsPath, nil
}

// registerSelfAsScheduledTask is the direct equivalent of the .ps1
// predecessor's Register-SelfAsScheduledTask: re-runs this exe on a
// recurring interval via Windows Task Scheduler, without
// -registration-secret (already consumed) so the secret doesn't sit
// indefinitely in a Task Scheduler action's saved arguments.
//
// The Task Scheduler action itself never points at the .exe directly.
// It points at wscript.exe running a small generated launcher .vbs
// (writeLauncherVBScript) which in turn runs the .exe with
// WScript.Shell.Run's WindowStyle=0 -- see that function's comments
// for why (in short: avoids a console window ever being created for
// the scheduled run, which neither a plain schtasks /TR nor a
// runtime GetConsoleWindow+ShowWindow(SW_HIDE) call can do, since both
// still let the OS create/flash a console first).
//
// Members who registered before this wscript-launcher indirection was
// introduced already have a schtasks entry whose /TR points at the
// .exe directly -- merely checking "does the task exist" (schtasks
// /Query's exit code) and returning early would leave that stale,
// console-flashing /TR in place forever, since a member's Task
// Scheduler entry is never otherwise touched again once created. So
// this always inspects the existing entry's actual action (via
// schtasksActionNeedsUpdate, XML-based rather than parsing /Query's
// plain-text output, which is localized and not stable to parse) and
// falls through to /Create /F to replace it whenever it doesn't
// already invoke wscript.exe on our launcher .vbs -- covering both a
// pre-launcher /TR and (for good measure) a hand-edited or otherwise
// unexpected one.
func (a *appContext) registerSelfAsScheduledTask() {
	exePath := copySelfToFixedLocation()
	if exePath == "" {
		warnf("could not determine a path to register for the scheduled task; skipping Task Scheduler registration for this run.")
		return
	}

	// Only re-pass flags that differ from this exe's own baked-in/
	// computed defaults (see BUILD.md: -api-endpoint and
	// -registration-secret are normally baked in via -ldflags, so the
	// scheduled re-run picks them up automatically without needing
	// them on the command line at all).
	//
	// Historically this mattered structurally, not just cosmetically,
	// because of schtasks.exe's /TR 261-character limit (undocumented
	// in `schtasks /Create /?` but enforced -- confirmed empirically:
	// "Value for '/TR' option cannot be more than 261 character(s)").
	// Now that /TR points at a fixed, short wscript.exe command instead
	// (see below), that specific limit no longer applies to these
	// flags -- but keeping the non-default-only filter regardless
	// keeps the generated .vbs minimal and avoids re-deriving
	// resolvedAPIEndpoint-style defaulting logic a second time here.
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

	installDir := filepath.Dir(exePath)
	vbsPath, err := writeLauncherVBScript(installDir, exePath, args)
	if err != nil {
		warnf("could not write the hidden-window launcher script to %s (%v). Falling back to launching the .exe directly, which will briefly show a console window on each scheduled run.", installDir, err)
		vbsPath = ""
	}

	taskExists := false
	checkCmd := exec.Command("schtasks", "/Query", "/TN", a.taskName)
	if err := checkCmd.Run(); err == nil {
		taskExists = true
	}

	if taskExists {
		needsUpdate, err := schtasksActionNeedsUpdate(a.taskName)
		if err != nil {
			warnf("could not inspect the existing Task Scheduler entry '%s' to check whether it needs updating (%v). Leaving it as-is.", a.taskName, err)
			return
		}
		if !needsUpdate {
			// Already points at our current launcher (or, if vbsPath is
			// empty because writing it failed above, was already
			// invoking the .exe directly) -- the self-update copy and
			// launcher .vbs (re)write above still ran, but nothing else
			// to do.
			return
		}
		infof("Task Scheduler entry '%s' exists but points at an outdated action (e.g. a pre-hidden-window-launcher version); recreating it...", a.taskName)
	} else {
		infof("Registering Task Scheduler entry '%s' (every %d minute(s))...", a.taskName, a.taskIntervalMinutes)
	}

	// /TR: prefer routing through the generated .vbs launcher
	// (wscript.exe //B, hidden window, no window ever created for this
	// scheduled run -- see writeLauncherVBScript). //B suppresses
	// wscript's own error-dialog popups so a launcher/script failure
	// doesn't itself show a visible box; any such failure still
	// surfaces indirectly via the usage-reporting gap an admin would
	// notice on the bedrock-chat admin page. Falls back to invoking the
	// .exe directly (briefly showing a console, same as before this
	// feature) only if writing the .vbs failed above.
	var taskRun string
	if vbsPath != "" {
		taskRun = buildCommandLine("wscript.exe", []string{"//B", vbsPath})
	} else {
		taskRun = buildCommandLine(exePath, args)
	}

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

// schtasksTaskXML mirrors just the piece of `schtasks /Query /XML`'s
// output this program needs: the registered action's executable
// (<Command>). Task Scheduler always splits a /TR command line into a
// <Command> (the program) and a separate <Arguments> element, so the
// program actually being invoked -- wscript.exe (our hidden-window
// launcher) vs. the .exe directly (the pre-launcher behavior) -- is
// exactly this one field, regardless of what quoting or arguments
// happen to follow it. Element names in this XML are fixed by the
// Task Scheduler schema and not localized (unlike /Query's own
// plain-text column output), so this is stable to parse regardless of
// the member's Windows display language.
type schtasksTaskXML struct {
	Actions struct {
		Exec struct {
			Command string `xml:"Command"`
		} `xml:"Exec"`
	} `xml:"Actions"`
}

// schtasksActionNeedsUpdate reports whether the existing Task
// Scheduler entry named taskName should be recreated because it
// doesn't already invoke wscript.exe (our hidden-window .vbs launcher
// -- see writeLauncherVBScript). Used by registerSelfAsScheduledTask
// to upgrade members who registered before this wscript-launcher
// indirection existed -- without this check, merely confirming the
// task exists and returning early would leave their original
// exe-direct /TR (which flashes a console on every scheduled run) in
// place forever.
func schtasksActionNeedsUpdate(taskName string) (bool, error) {
	out, err := exec.Command("schtasks", "/Query", "/TN", taskName, "/XML").Output()
	if err != nil {
		return false, err
	}
	command, err := parseSchtasksExecCommand(out)
	if err != nil {
		return false, err
	}
	// Command is normally just "wscript.exe" once we've registered our
	// launcher (schtasks splits the launcher .vbs path itself into a
	// separate <Arguments> element, since it was a separate quoted
	// token in the original /TR string) -- checking for "wscript" here,
	// rather than requiring an exact match, is deliberately lenient
	// about a full vs. relative path to wscript.exe. Anything else
	// (most commonly the .exe's own path, from before this launcher
	// indirection existed) needs recreating.
	return !strings.Contains(strings.ToLower(command), "wscript"), nil
}

// parseSchtasksExecCommand extracts the registered action's <Command>
// from raw `schtasks /Query /TN <name> /XML` output, transcoding from
// UTF-16 first if needed (see decodeUTF16IfNeeded). Split out from
// schtasksActionNeedsUpdate as a pure function (no exec.Command
// dependency) so it -- and the UTF-16 transcoding it relies on -- can
// be unit tested directly against a captured real XML sample, without
// needing an actual Windows schtasks.exe to shell out to.
func parseSchtasksExecCommand(rawXML []byte) (string, error) {
	// schtasks /Query /XML emits UTF-16LE with a BOM on stock Windows;
	// encoding/xml's Decoder only understands UTF-8/US-ASCII directly,
	// so a UTF-16 document would otherwise fail to parse with an
	// "invalid character entity" or similar low-level error. Detect
	// and transcode it first; a document that's already UTF-8 (e.g.
	// under some non-default schtasks/locale combination) passes
	// through untouched.
	rawXML = decodeUTF16IfNeeded(rawXML)

	// The transcoded bytes are valid UTF-8 now, but the XML
	// declaration itself still literally says
	// encoding="UTF-16" (schtasks writes that regardless of transport
	// encoding) -- encoding/xml checks that declared name and refuses
	// to proceed without a registered CharsetReader for anything other
	// than UTF-8/US-ASCII, even though the bytes it's actually looking
	// at are already UTF-8 at this point. Rewriting just the declared
	// name to "UTF-8" (only when it says UTF-16; a document with no
	// such declaration, or already declaring UTF-8, is left alone)
	// avoids needing a CharsetReader/external dependency at all.
	rawXML = rewriteUTF16XMLDeclaration(rawXML)

	var parsed schtasksTaskXML
	if err := xml.Unmarshal(rawXML, &parsed); err != nil {
		return "", err
	}
	return strings.TrimSpace(parsed.Actions.Exec.Command), nil
}

// rewriteUTF16XMLDeclaration replaces a `UTF-16`/`utf-16` encoding
// name in an XML declaration's encoding="..." attribute with `UTF-8`,
// so encoding/xml (which understands the bytes are actually UTF-8
// after decodeUTF16IfNeeded's transcoding, but only trusts its own
// declared-encoding check, not the actual byte content) accepts the
// document without needing a CharsetReader. Only touches the encoding
// attribute inside the leading `<?xml ... ?>` declaration -- never
// anywhere else in the document -- and only when it actually names
// UTF-16, so a document already declaring UTF-8 (or no declaration at
// all) passes through unchanged.
func rewriteUTF16XMLDeclaration(b []byte) []byte {
	const maxDeclScan = 256 // XML declarations are always short and at the very start of the document
	scanLen := len(b)
	if scanLen > maxDeclScan {
		scanLen = maxDeclScan
	}
	declEnd := strings.Index(string(b[:scanLen]), "?>")
	if declEnd == -1 {
		return b
	}
	decl := string(b[:declEnd])
	lowerDecl := strings.ToLower(decl)
	idx := strings.Index(lowerDecl, "utf-16")
	if idx == -1 {
		return b
	}
	fixedDecl := decl[:idx] + "UTF-8" + decl[idx+len("utf-16"):]
	return append([]byte(fixedDecl), b[declEnd:]...)
}

// decodeUTF16IfNeeded transcodes a UTF-16 (LE or BE) byte slice with a
// byte-order-mark to UTF-8, which is what encoding/xml requires. Bytes
// with no recognized BOM are returned unchanged (assumed already
// UTF-8/ASCII, e.g. schtasks output under some environments).
func decodeUTF16IfNeeded(b []byte) []byte {
	var order binary.ByteOrder
	switch {
	case len(b) >= 2 && b[0] == 0xFF && b[1] == 0xFE:
		order = binary.LittleEndian
		b = b[2:]
	case len(b) >= 2 && b[0] == 0xFE && b[1] == 0xFF:
		order = binary.BigEndian
		b = b[2:]
	default:
		return b
	}
	if len(b)%2 != 0 {
		b = b[:len(b)-1]
	}
	u16s := make([]uint16, len(b)/2)
	for i := range u16s {
		u16s[i] = order.Uint16(b[i*2 : i*2+2])
	}
	return []byte(string(utf16.Decode(u16s)))
}
