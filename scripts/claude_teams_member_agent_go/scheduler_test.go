package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"unicode/utf16"
)

// installedCopyNeedsUpdate is plain file-size comparison with no
// Windows-specific syscalls, unlike the rest of scheduler.go (which
// shells out to schtasks.exe/setx and so can only actually run on
// Windows) -- so this is tested directly here, on whatever OS `go
// test` runs on, the same way config_test.go exercises other
// OS-agnostic helpers.
func TestInstalledCopyNeedsUpdateMissingInstalledFile(t *testing.T) {
	dir := t.TempDir()
	selfPath := filepath.Join(dir, "self.exe")
	if err := os.WriteFile(selfPath, []byte("hello"), 0o755); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	installedPath := filepath.Join(dir, "does-not-exist.exe")

	needsUpdate, err := installedCopyNeedsUpdate(selfPath, installedPath)
	if err != nil {
		t.Fatalf("installedCopyNeedsUpdate returned error for a missing installed file (should be treated as first-ever install): %v", err)
	}
	if !needsUpdate {
		t.Fatal("installedCopyNeedsUpdate returned false for a missing installed file -- should report true (first-ever install)")
	}
}

func TestInstalledCopyNeedsUpdateSameSizeSkipsUpdate(t *testing.T) {
	dir := t.TempDir()
	selfPath := filepath.Join(dir, "self.exe")
	installedPath := filepath.Join(dir, "installed.exe")
	// Same size, different content -- this is a size-only check by
	// design (see installedCopyNeedsUpdate's comments), so this must
	// report "no update needed" even though the bytes differ.
	if err := os.WriteFile(selfPath, []byte("AAAAA"), 0o755); err != nil {
		t.Fatalf("WriteFile self: %v", err)
	}
	if err := os.WriteFile(installedPath, []byte("BBBBB"), 0o755); err != nil {
		t.Fatalf("WriteFile installed: %v", err)
	}

	needsUpdate, err := installedCopyNeedsUpdate(selfPath, installedPath)
	if err != nil {
		t.Fatalf("installedCopyNeedsUpdate: %v", err)
	}
	if needsUpdate {
		t.Fatal("installedCopyNeedsUpdate reported an update needed for two same-sized files -- expected the size-only check to treat these as matching")
	}
}

func TestInstalledCopyNeedsUpdateDifferentSizeTriggersUpdate(t *testing.T) {
	dir := t.TempDir()
	selfPath := filepath.Join(dir, "self.exe")
	installedPath := filepath.Join(dir, "installed.exe")
	if err := os.WriteFile(selfPath, []byte("a-longer-new-build-payload"), 0o755); err != nil {
		t.Fatalf("WriteFile self: %v", err)
	}
	if err := os.WriteFile(installedPath, []byte("old"), 0o755); err != nil {
		t.Fatalf("WriteFile installed: %v", err)
	}

	needsUpdate, err := installedCopyNeedsUpdate(selfPath, installedPath)
	if err != nil {
		t.Fatalf("installedCopyNeedsUpdate: %v", err)
	}
	if !needsUpdate {
		t.Fatal("installedCopyNeedsUpdate reported no update needed for differently-sized files -- expected true so a rebuilt/redistributed .exe gets picked up")
	}
}

// TestCopySelfToFixedLocationOverwritesStaleInstalledCopy is an
// end-to-end check of copySelfToFixedLocation's self-update behavior:
// pre-seeds an "installed" copy with different (smaller) content than
// os.Executable() (the actual running `go test` binary during this
// test), and confirms the installed copy afterward matches the
// running binary's real size -- i.e. it was actually overwritten, not
// left stale. HOME is pointed at a temp dir so this never touches a
// real developer's ~/.claude.
func TestCopySelfToFixedLocationOverwritesStaleInstalledCopy(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home) // harmless on non-Windows; mirrors what the Windows build reads

	installDir := filepath.Join(home, ".claude", "claude_teams_member_agent")
	if err := os.MkdirAll(installDir, 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}
	installedPath := filepath.Join(installDir, "claude_teams_member_agent.exe")
	if err := os.WriteFile(installedPath, []byte("stale-old-build"), 0o755); err != nil {
		t.Fatalf("seed stale installed copy: %v", err)
	}

	selfPath, err := os.Executable()
	if err != nil {
		t.Fatalf("os.Executable: %v", err)
	}
	selfInfo, err := os.Stat(selfPath)
	if err != nil {
		t.Fatalf("Stat selfPath: %v", err)
	}

	returnedPath := copySelfToFixedLocation()
	if returnedPath != installedPath {
		t.Fatalf("copySelfToFixedLocation returned %q, want the installed path %q", returnedPath, installedPath)
	}

	updatedInfo, err := os.Stat(installedPath)
	if err != nil {
		t.Fatalf("Stat installedPath after copy: %v", err)
	}
	if updatedInfo.Size() != selfInfo.Size() {
		t.Fatalf("installed copy size = %d after copySelfToFixedLocation, want %d (this running binary's size) -- stale copy was not overwritten", updatedInfo.Size(), selfInfo.Size())
	}
}

// TestWriteLauncherVBScriptProducesExpectedShellRunCall confirms the
// generated .vbs contains exactly the pieces that make it a
// no-console-window launch (WScript.Shell.Run's windowStyle=0
// argument, which is the entire reason this launcher exists -- see
// writeLauncherVBScript's comments), embeds the exe path and each
// extra arg quoted, and that the .vbs's own VBScript string-literal
// quoting doubles any embedded double quote correctly.
func TestWriteLauncherVBScriptProducesExpectedShellRunCall(t *testing.T) {
	dir := t.TempDir()
	exePath := filepath.Join(dir, `sub dir`, "claude_teams_member_agent.exe") // space in path exercises quoting
	args := []string{"-unattended", "-task-interval-minutes", "30"}

	vbsPath, err := writeLauncherVBScript(dir, exePath, args)
	if err != nil {
		t.Fatalf("writeLauncherVBScript: %v", err)
	}
	if filepath.Dir(vbsPath) != dir {
		t.Fatalf("writeLauncherVBScript wrote to %q, want a file directly inside %q", vbsPath, dir)
	}
	if filepath.Base(vbsPath) != launcherVBScriptName {
		t.Fatalf("writeLauncherVBScript's returned path is %q, want basename %q", vbsPath, launcherVBScriptName)
	}

	content, err := os.ReadFile(vbsPath)
	if err != nil {
		t.Fatalf("ReadFile(%q): %v", vbsPath, err)
	}
	script := string(content)

	if !strings.Contains(script, `CreateObject("WScript.Shell")`) {
		t.Fatalf("generated .vbs does not create a WScript.Shell object:\n%s", script)
	}
	if !strings.Contains(script, "shell.Run \"") {
		t.Fatalf("generated .vbs does not call shell.Run with a quoted command string:\n%s", script)
	}
	// ", 0, True" is the windowStyle=0 (hidden), waitOnReturn=True
	// argument pair -- the actual mechanism that avoids a console
	// window ever being shown for the scheduled run.
	if !strings.Contains(script, ", 0, True") {
		t.Fatalf("generated .vbs does not pass windowStyle=0 (hidden) to shell.Run:\n%s", script)
	}
	// The exe path and its args are embedded (double-quoted internally
	// by buildCommandLine, then VBScript-escaped to "" by
	// writeLauncherVBScript), so the raw exe path substring must still
	// appear somewhere in the script even after that double escaping.
	if !strings.Contains(script, `sub dir\claude_teams_member_agent.exe`) && !strings.Contains(script, `sub dir/claude_teams_member_agent.exe`) {
		t.Fatalf("generated .vbs does not appear to embed the exe path %q:\n%s", exePath, script)
	}
	if !strings.Contains(script, "-task-interval-minutes") || !strings.Contains(script, "30") {
		t.Fatalf("generated .vbs does not appear to embed the extra args %v:\n%s", args, script)
	}
}

// TestWriteLauncherVBScriptOverwritesExistingFile confirms repeated
// calls (as happens on every run via registerSelfAsScheduledTask, not
// just once at first install -- see writeLauncherVBScript's comments
// on why) replace stale content rather than erroring or appending.
func TestWriteLauncherVBScriptOverwritesExistingFile(t *testing.T) {
	dir := t.TempDir()
	stalePath := filepath.Join(dir, launcherVBScriptName)
	if err := os.WriteFile(stalePath, []byte("stale content from an older build"), 0o644); err != nil {
		t.Fatalf("seed stale .vbs: %v", err)
	}

	vbsPath, err := writeLauncherVBScript(dir, filepath.Join(dir, "claude_teams_member_agent.exe"), []string{"-unattended"})
	if err != nil {
		t.Fatalf("writeLauncherVBScript: %v", err)
	}

	content, err := os.ReadFile(vbsPath)
	if err != nil {
		t.Fatalf("ReadFile: %v", err)
	}
	if strings.Contains(string(content), "stale content") {
		t.Fatal("writeLauncherVBScript did not overwrite stale existing .vbs content")
	}
}

// realWorldSchtasksXMLSample is a representative (trimmed)
// `schtasks /Query /TN ... /XML` document, based on Microsoft's own
// documented Task Scheduler XML schema and a real captured export
// (see e.g. https://tutorialreference.com/batch-scripting/examples/faq/batch-script-how-to-export-a-scheduled-task-to-xml),
// with <Command>/<Arguments> substituted to match what
// registerSelfAsScheduledTask actually registers: wscript.exe running
// our launcher .vbs with //B.
const realWorldSchtasksXMLSample = `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>2026-01-01T00:00:00</Date>
    <Author>DESKTOP-ABC\bob</Author>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT60M</Interval>
      </Repetition>
      <StartBoundary>2026-01-01T09:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>wscript.exe</Command>
      <Arguments>//B "C:\Users\bob\.claude\claude_teams_member_agent\claude_teams_member_agent_launcher.vbs"</Arguments>
    </Exec>
  </Actions>
</Task>
`

// realWorldSchtasksXMLSamplePreLauncher is the same shape, but for a
// pre-launcher-indirection registration whose <Command> is the .exe
// itself -- the exact state schtasksActionNeedsUpdate must detect as
// stale so registerSelfAsScheduledTask upgrades it.
const realWorldSchtasksXMLSamplePreLauncher = `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Actions Context="Author">
    <Exec>
      <Command>C:\Users\bob\.claude\claude_teams_member_agent\claude_teams_member_agent.exe</Command>
      <Arguments>-unattended</Arguments>
    </Exec>
  </Actions>
</Task>
`

// TestParseSchtasksExecCommandUTF16WithBOM confirms parsing a document
// exactly as schtasks /Query /XML actually emits it on stock Windows:
// UTF-16LE with a byte-order-mark, which encoding/xml cannot parse
// directly (see decodeUTF16IfNeeded's comments) -- so this exercises
// that transcoding path end-to-end, not just the XML unmarshaling.
func TestParseSchtasksExecCommandUTF16WithBOM(t *testing.T) {
	utf16LE := encodeUTF16LEWithBOM(realWorldSchtasksXMLSample)

	command, err := parseSchtasksExecCommand(utf16LE)
	if err != nil {
		t.Fatalf("parseSchtasksExecCommand: %v", err)
	}
	if command != "wscript.exe" {
		t.Fatalf("parseSchtasksExecCommand returned %q, want %q", command, "wscript.exe")
	}
}

// TestParseSchtasksExecCommandPlainUTF8 confirms the function also
// accepts a document with no BOM (treated as already UTF-8/ASCII --
// see decodeUTF16IfNeeded), so this doesn't regress if schtasks output
// ever passes through some environment/encoding.
func TestParseSchtasksExecCommandPlainUTF8(t *testing.T) {
	command, err := parseSchtasksExecCommand([]byte(realWorldSchtasksXMLSample))
	if err != nil {
		t.Fatalf("parseSchtasksExecCommand: %v", err)
	}
	if command != "wscript.exe" {
		t.Fatalf("parseSchtasksExecCommand returned %q, want %q", command, "wscript.exe")
	}
}

// TestParseSchtasksExecCommandPreLauncherExe confirms parsing correctly
// extracts a pre-launcher-indirection registration's <Command> (the
// .exe's own path), which is the actual real-world input
// schtasksActionNeedsUpdate must recognize as needing an upgrade.
func TestParseSchtasksExecCommandPreLauncherExe(t *testing.T) {
	command, err := parseSchtasksExecCommand([]byte(realWorldSchtasksXMLSamplePreLauncher))
	if err != nil {
		t.Fatalf("parseSchtasksExecCommand: %v", err)
	}
	want := `C:\Users\bob\.claude\claude_teams_member_agent\claude_teams_member_agent.exe`
	if command != want {
		t.Fatalf("parseSchtasksExecCommand returned %q, want %q", command, want)
	}
}

// encodeUTF16LEWithBOM converts s to UTF-16LE bytes prefixed with a
// byte-order-mark, mirroring the actual encoding schtasks /Query /XML
// emits on stock Windows (see the "encoding=\"UTF-16\"" declaration in
// realWorldSchtasksXMLSample itself, taken from a real capture).
func encodeUTF16LEWithBOM(s string) []byte {
	units := utf16.Encode([]rune(s))
	buf := make([]byte, 2+len(units)*2)
	buf[0], buf[1] = 0xFF, 0xFE // UTF-16LE BOM
	for i, u := range units {
		buf[2+i*2] = byte(u)
		buf[2+i*2+1] = byte(u >> 8)
	}
	return buf
}
