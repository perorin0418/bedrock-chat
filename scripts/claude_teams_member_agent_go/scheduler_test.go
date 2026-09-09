package main

import (
	"os"
	"path/filepath"
	"testing"
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
