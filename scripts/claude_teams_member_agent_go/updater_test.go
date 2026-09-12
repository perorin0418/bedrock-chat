package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func sha256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func TestDownloadAndVerifyWritesFileOnMatchingDigest(t *testing.T) {
	payload := []byte("pretend this is a windows exe")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write(payload)
	}))
	defer server.Close()

	dest := filepath.Join(t.TempDir(), "staged.exe")
	if err := downloadAndVerify(server.URL, sha256Hex(payload), dest); err != nil {
		t.Fatalf("expected success, got %v", err)
	}
	got, err := os.ReadFile(dest)
	if err != nil {
		t.Fatalf("staged file not written: %v", err)
	}
	if string(got) != string(payload) {
		t.Fatalf("staged file content = %q, want %q", got, payload)
	}
}

// The digest check is the security control this whole feature rests on:
// bytes that don't match what the ingest_secret-authenticated API
// reported must never be installed, no matter what the (untrusted,
// presigned) download URL served.
func TestDownloadAndVerifyRejectsDigestMismatch(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("tampered payload"))
	}))
	defer server.Close()

	dest := filepath.Join(t.TempDir(), "staged.exe")
	err := downloadAndVerify(server.URL, sha256Hex([]byte("the expected payload")), dest)
	if err == nil {
		t.Fatal("expected a sha256 mismatch error, got nil")
	}
	if !strings.Contains(err.Error(), "sha256 mismatch") {
		t.Fatalf("expected a sha256 mismatch error, got %v", err)
	}
}

// A server that (impossibly, since the backend refuses to publish such
// a release) reports no digest must not cause the agent to fall back to
// installing unverified bytes.
func TestDownloadAndVerifyRefusesEmptyDigest(t *testing.T) {
	err := downloadAndVerify("http://127.0.0.1:1/never-requested", "  ", filepath.Join(t.TempDir(), "x.exe"))
	if err == nil || !strings.Contains(err.Error(), "unverified") {
		t.Fatalf("expected a refusal to install unverified bytes, got %v", err)
	}
}

func TestDownloadAndVerifyRejectsEmptyDownload(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer server.Close()

	err := downloadAndVerify(server.URL, sha256Hex([]byte("something")), filepath.Join(t.TempDir(), "x.exe"))
	if err == nil {
		t.Fatal("expected an error for an empty download, got nil")
	}
}

func TestDownloadAndVerifyRejectsHTTPError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
	}))
	defer server.Close()

	err := downloadAndVerify(server.URL, sha256Hex([]byte("x")), filepath.Join(t.TempDir(), "x.exe"))
	if err == nil || !strings.Contains(err.Error(), "403") {
		t.Fatalf("expected the HTTP status to surface, got %v", err)
	}
}

func TestSwapInInstalledUpdateReplacesAndKeepsPrevious(t *testing.T) {
	dir := t.TempDir()
	installed := filepath.Join(dir, "claude_teams_member_agent.exe")
	staging := filepath.Join(dir, updateStagingName)
	if err := os.WriteFile(installed, []byte("old version"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(staging, []byte("new version"), 0o755); err != nil {
		t.Fatal(err)
	}

	if err := swapInInstalledUpdate(installed, staging, dir); err != nil {
		t.Fatalf("expected success, got %v", err)
	}

	got, err := os.ReadFile(installed)
	if err != nil || string(got) != "new version" {
		t.Fatalf("installed exe = %q (err %v), want %q", got, err, "new version")
	}
	// The outgoing binary is kept, not deleted: on Windows it may still
	// be the currently-executing image at this moment. The next run
	// removes it (see selfUpdateIfNeeded).
	prev, err := os.ReadFile(filepath.Join(dir, updatePreviousName))
	if err != nil || string(prev) != "old version" {
		t.Fatalf("previous exe = %q (err %v), want %q", prev, err, "old version")
	}
	if _, err := os.Stat(staging); !os.IsNotExist(err) {
		t.Fatalf("staging file should have been consumed by the rename, stat err = %v", err)
	}
}

// A leftover .old from an earlier update (not yet cleaned up because it
// was still the running image then) must not wedge the next update:
// os.Rename does not overwrite an existing target on Windows.
func TestSwapInInstalledUpdateOverwritesStalePrevious(t *testing.T) {
	dir := t.TempDir()
	installed := filepath.Join(dir, "claude_teams_member_agent.exe")
	staging := filepath.Join(dir, updateStagingName)
	os.WriteFile(filepath.Join(dir, updatePreviousName), []byte("ancient version"), 0o755)
	os.WriteFile(installed, []byte("old version"), 0o755)
	os.WriteFile(staging, []byte("new version"), 0o755)

	if err := swapInInstalledUpdate(installed, staging, dir); err != nil {
		t.Fatalf("expected success despite a stale .old present, got %v", err)
	}
	prev, _ := os.ReadFile(filepath.Join(dir, updatePreviousName))
	if string(prev) != "old version" {
		t.Fatalf("previous exe = %q, want the just-replaced %q", prev, "old version")
	}
}

// Losing the installed .exe entirely is strictly worse than staying on
// an old version: a scheduled task pointing at a missing file silently
// stops reporting usage forever. So a failed second rename must restore
// the original.
func TestSwapInInstalledUpdateRestoresOriginalWhenInstallFails(t *testing.T) {
	dir := t.TempDir()
	installed := filepath.Join(dir, "claude_teams_member_agent.exe")
	if err := os.WriteFile(installed, []byte("old version"), 0o755); err != nil {
		t.Fatal(err)
	}
	// No staging file exists, so the second rename fails.
	err := swapInInstalledUpdate(installed, filepath.Join(dir, updateStagingName), dir)
	if err == nil {
		t.Fatal("expected an error when the staged file is missing, got nil")
	}
	got, readErr := os.ReadFile(installed)
	if readErr != nil || string(got) != "old version" {
		t.Fatalf("installed exe = %q (err %v); the previous version must have been restored", got, readErr)
	}
}

func TestFetchAgentReleaseParsesCamelCaseResponse(t *testing.T) {
	var gotPath string
	var gotBody agentReleaseRequest
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// EscapedPath, not Path: net/http decodes Path, which would
		// hide whether the token_id was escaped on the wire at all.
		gotPath = r.URL.EscapedPath()
		// The secret must travel in the body, never the query string:
		// query strings are recorded in API Gateway access logs and
		// corporate proxies, and this response grants download of a
		// binary carrying the org-wide Registration Secret.
		if r.URL.RawQuery != "" {
			t.Errorf("expected no query string, got %q", r.URL.RawQuery)
		}
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		json.NewDecoder(r.Body).Decode(&gotBody)
		w.Write([]byte(`{"version":"2026.09.10-abc1234","sha256":"deadbeef","downloadUrl":"https://example.invalid/x.exe"}`))
	}))
	defer server.Close()

	app := &appContext{apiEndpoint: server.URL}
	release, err := app.fetchAgentRelease("tok 1", "secret-abc")
	if err != nil {
		t.Fatalf("expected success, got %v", err)
	}
	if gotPath != "/claude-teams-tokens/tok%201/agent-version" {
		t.Fatalf("path = %q, want the token_id percent-escaped in it", gotPath)
	}
	if gotBody.IngestSecret != "secret-abc" {
		t.Fatalf("ingest_secret in body = %q, want %q", gotBody.IngestSecret, "secret-abc")
	}
	if release.Version != "2026.09.10-abc1234" || release.SHA256 != "deadbeef" ||
		release.DownloadURL != "https://example.invalid/x.exe" {
		t.Fatalf("unexpected parsed release: %+v", release)
	}
}

// 404 means "this deployment publishes no agent release", a normal
// state for an org that still hands out rebuilt .exe files manually.
// It must be distinguishable from a real failure so members aren't
// warned about it on every hourly run.
func TestFetchAgentReleaseMaps404ToNoRelease(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		w.Write([]byte(`{"detail":"No agent release is published."}`))
	}))
	defer server.Close()

	app := &appContext{apiEndpoint: server.URL}
	_, err := app.fetchAgentRelease("tok-1", "secret")
	if err != errNoAgentRelease {
		t.Fatalf("expected errNoAgentRelease, got %v", err)
	}
}

func TestFetchAgentReleaseSurfacesOtherErrors(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	}))
	defer server.Close()

	app := &appContext{apiEndpoint: server.URL}
	_, err := app.fetchAgentRelease("tok-1", "wrong-secret")
	if err == nil || err == errNoAgentRelease {
		t.Fatalf("a 401 must surface as a real error, got %v", err)
	}
}

// A build with no version baked in cannot tell whether the published
// release differs from itself, so it must not contact the release
// endpoint at all rather than re-downloading the same .exe forever.
func TestSelfUpdateIfNeededSkipsWhenVersionUnset(t *testing.T) {
	requested := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requested = true
	}))
	defer server.Close()

	t.Setenv("HOME", t.TempDir())
	t.Setenv("USERPROFILE", os.Getenv("HOME"))

	original := agentVersion
	agentVersion = ""
	defer func() { agentVersion = original }()

	app := &appContext{apiEndpoint: server.URL}
	app.selfUpdateIfNeeded("tok-1", "secret")

	if requested {
		t.Fatal("an unversioned build must not check for updates")
	}
}

func TestSelfUpdateIfNeededSkipsWhenVersionMatches(t *testing.T) {
	downloadRequested := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/agent-version") {
			w.Write([]byte(`{"version":"same-version","sha256":"aa","downloadUrl":"` + r.Host + `/dl"}`))
			return
		}
		downloadRequested = true
	}))
	defer server.Close()

	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)

	original := agentVersion
	agentVersion = "same-version"
	defer func() { agentVersion = original }()

	app := &appContext{apiEndpoint: server.URL}
	app.selfUpdateIfNeeded("tok-1", "secret")

	if downloadRequested {
		t.Fatal("an up-to-date agent must not download anything")
	}
}

func TestSelfUpdateIfNeededInstallsPublishedVersion(t *testing.T) {
	newBinary := []byte("the newly published exe bytes")
	var server *httptest.Server
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/agent-version") {
			json.NewEncoder(w).Encode(map[string]string{
				"version":     "2026.09.10-new",
				"sha256":      sha256Hex(newBinary),
				"downloadUrl": server.URL + "/download",
			})
			return
		}
		w.Write(newBinary)
	}))
	defer server.Close()

	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	installDir := filepath.Join(home, ".claude", "claude_teams_member_agent")
	if err := os.MkdirAll(installDir, 0o755); err != nil {
		t.Fatal(err)
	}
	installed := filepath.Join(installDir, "claude_teams_member_agent.exe")
	if err := os.WriteFile(installed, []byte("the old exe"), 0o755); err != nil {
		t.Fatal(err)
	}

	original := agentVersion
	agentVersion = "2026.09.01-old"
	defer func() { agentVersion = original }()

	app := &appContext{apiEndpoint: server.URL}
	app.selfUpdateIfNeeded("tok-1", "secret")

	got, err := os.ReadFile(installed)
	if err != nil || string(got) != string(newBinary) {
		t.Fatalf("installed exe = %q (err %v), want the downloaded bytes", got, err)
	}
}

// A tampered/corrupt download must leave the installed copy untouched
// and leave no staged file behind for the next run to trip over.
func TestSelfUpdateIfNeededKeepsCurrentVersionOnDigestMismatch(t *testing.T) {
	var server *httptest.Server
	server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/agent-version") {
			json.NewEncoder(w).Encode(map[string]string{
				"version":     "2026.09.10-new",
				"sha256":      sha256Hex([]byte("what the manifest promised")),
				"downloadUrl": server.URL + "/download",
			})
			return
		}
		w.Write([]byte("something else entirely"))
	}))
	defer server.Close()

	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	installDir := filepath.Join(home, ".claude", "claude_teams_member_agent")
	os.MkdirAll(installDir, 0o755)
	installed := filepath.Join(installDir, "claude_teams_member_agent.exe")
	os.WriteFile(installed, []byte("the old exe"), 0o755)

	original := agentVersion
	agentVersion = "2026.09.01-old"
	defer func() { agentVersion = original }()

	app := &appContext{apiEndpoint: server.URL}
	app.selfUpdateIfNeeded("tok-1", "secret")

	got, _ := os.ReadFile(installed)
	if string(got) != "the old exe" {
		t.Fatalf("installed exe = %q, want it left untouched after a digest mismatch", got)
	}
	if _, err := os.Stat(filepath.Join(installDir, updateStagingName)); !os.IsNotExist(err) {
		t.Fatalf("a rejected download must not be left staged on disk, stat err = %v", err)
	}
}

// The previous version is deleted on the run *after* the update, when
// it is no longer the executing image.
func TestSelfUpdateIfNeededCleansUpPreviousVersion(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	installDir := filepath.Join(home, ".claude", "claude_teams_member_agent")
	os.MkdirAll(installDir, 0o755)
	previous := filepath.Join(installDir, updatePreviousName)
	os.WriteFile(previous, []byte("last week's exe"), 0o755)

	original := agentVersion
	agentVersion = "" // stops before any network call; cleanup happens first
	defer func() { agentVersion = original }()

	app := &appContext{apiEndpoint: "http://127.0.0.1:1"}
	app.selfUpdateIfNeeded("tok-1", "secret")

	if _, err := os.Stat(previous); !os.IsNotExist(err) {
		t.Fatalf("the previous version should have been cleaned up, stat err = %v", err)
	}
}
