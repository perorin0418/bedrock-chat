package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

// agentVersion identifies this build, set at build time via
// -ldflags "-X main.agentVersion=..." (see BUILD.md / build.bat, which
// derive it from the build date + git short SHA). Left empty by a bare
// `go build`, which deliberately disables self-updating entirely for
// that build -- see checkForUpdate: a binary that cannot say which
// version it is has no way to tell whether the published release is
// newer than itself, and an "always differs" fallback would make every
// such build re-download the same .exe on every scheduled run forever.
var agentVersion = ""

// updateStagingName / updatePreviousName are fixed filenames used
// inside the install directory during a self-update.
//
// Staging in the *same* directory as the installed .exe (rather than
// %TEMP%) is deliberate: the final swap must be an os.Rename, and
// rename is only atomic -- and on Windows only succeeds at all without
// a copy fallback -- within a single volume. %TEMP% can easily be on a
// different drive than %USERPROFILE% on a corporate image with a
// redirected temp folder.
const (
	updateStagingName  = "claude_teams_member_agent.exe.new"
	updatePreviousName = "claude_teams_member_agent.exe.old"
)

// maxUpdateDownloadBytes caps how much this agent will read from the
// presigned download URL. The real .exe is a few megabytes; the cap
// exists so a misconfigured manifest key pointing at some enormous
// object (or a truncated/looping response) can't fill a member's disk
// on an unattended scheduled run. Generous enough to leave plenty of
// headroom for the binary growing over time.
const maxUpdateDownloadBytes = 200 << 20 // 200 MiB

// agentReleaseResponse mirrors the bedrock-chat
// POST /claude-teams-tokens/{token_id}/agent-version response. Note the
// camelCase JSON tags: bedrock-chat serializes responses via
// humps.camelize (see backend/app/routes/schemas/base.py), unlike its
// snake_case request bodies -- the same asymmetry already documented on
// registerResponse in api.go.
type agentReleaseResponse struct {
	Version     string `json:"version"`
	SHA256      string `json:"sha256"`
	DownloadURL string `json:"downloadUrl"`
}

type agentReleaseRequest struct {
	IngestSecret string `json:"ingest_secret"`
}

// errNoAgentRelease reports that this deployment publishes no agent
// release (the route answered 404). Not a failure: a deployment where
// the admin still hands out rebuilt .exe files by hand is entirely
// valid, and members must not be warned about it hourly.
var errNoAgentRelease = fmt.Errorf("no agent release is published for this deployment")

// fetchAgentRelease asks bedrock-chat which version this agent should
// be running, authenticated with the same per-token ingest_secret the
// usage-snapshot push and chat-token status check already use.
//
// Sent as a POST with the secret in the body rather than a GET with it
// in the query string (as /status does) because the response is a
// download URL for a binary carrying the org-wide Registration Secret:
// query strings land verbatim in API Gateway access logs and any
// intervening corporate proxy, so this particular credential is kept
// out of the URL. See the route docstring.
func (a *appContext) fetchAgentRelease(tokenID, ingestSecret string) (*agentReleaseResponse, error) {
	uri := fmt.Sprintf("%s/claude-teams-tokens/%s/agent-version", a.apiEndpoint, url.PathEscape(tokenID))
	var resp agentReleaseResponse
	status, err := doJSONRequest(http.MethodPost, uri, agentReleaseRequest{IngestSecret: ingestSecret}, nil, &resp)
	if err != nil {
		if status == http.StatusNotFound {
			return nil, errNoAgentRelease
		}
		return nil, err
	}
	return &resp, nil
}

// selfUpdateIfNeeded is the whole auto-update entry point, called on
// every run once the local config is known (see app.go).
//
// It never touches the running process: the newly downloaded .exe
// replaces the *installed copy* on disk, and the already-registered
// Task Scheduler entry picks it up on its next scheduled run. There is
// deliberately no in-place restart/exec-into-the-new-binary step --
// that would mean a freshly downloaded binary starts running on a
// member's machine within milliseconds of arriving, with no
// opportunity for a bad release to be noticed or rolled back, and it
// would complicate the "did this run report usage?" story for no gain
// given the agent already re-runs on a fixed interval anyway.
//
// Every failure path here is a warning, never fatal: a failed update
// must not stop this run from doing its actual job (reporting usage).
// The member keeps running the version they have and the next
// scheduled run retries.
func (a *appContext) selfUpdateIfNeeded(tokenID, ingestSecret string) {
	installDir, installedPath, err := installedAgentPaths()
	if err != nil {
		warnf("could not determine the installed agent location, so skipping the update check (%v).", err)
		return
	}

	// Clean up the previous version left behind by an earlier update
	// (see swapInInstalledUpdate). Deferred to the *next* run rather
	// than done immediately after the swap because on Windows the old
	// binary may still be the currently-executing image, which cannot
	// be deleted while the process is alive -- by the next scheduled
	// run it no longer is. A failure here is ignored entirely: a
	// leftover ~5MB .old file is harmless and not worth a warning on a
	// member's screen.
	_ = os.Remove(filepath.Join(installDir, updatePreviousName))

	if agentVersion == "" {
		// Built without -X main.agentVersion (a bare `go build`, e.g. a
		// developer's local build). Skipped rather than treated as
		// "unknown, so assume outdated": see agentVersion's comment.
		return
	}

	release, err := a.fetchAgentRelease(tokenID, ingestSecret)
	if err != nil {
		if err == errNoAgentRelease {
			// Normal state for a deployment that distributes updates by
			// hand. Silent by design.
			return
		}
		warnf("could not check for an agent update (%v). Continuing with the current version.", err)
		return
	}

	if release.Version == agentVersion {
		return
	}

	// Deliberately an inequality, not a "newer than" comparison: the
	// manifest is the single source of truth for what members should be
	// running, precisely so an admin can *roll back* by pointing it at
	// an older release and have every member follow. Version strings
	// here are opaque labels (date + git SHA), not an ordered scheme
	// this agent could compare even if it wanted to.
	infof("A different agent version is published (%s; this exe is %s). Updating the installed copy...", release.Version, agentVersion)

	stagingPath := filepath.Join(installDir, updateStagingName)
	if err := downloadAndVerify(release.DownloadURL, release.SHA256, stagingPath); err != nil {
		warnf("could not download/verify agent version %s (%v). Keeping the current version; will retry on the next scheduled run.", release.Version, err)
		// Leave nothing half-written behind for the next run to trip
		// over: the staging file is re-created from scratch each time.
		_ = os.Remove(stagingPath)
		return
	}

	if err := swapInInstalledUpdate(installedPath, stagingPath, installDir); err != nil {
		warnf("downloaded and verified agent version %s but could not install it (%v). Keeping the current version; will retry on the next scheduled run.", release.Version, err)
		_ = os.Remove(stagingPath)
		return
	}

	infof("Installed agent version %s at %s. It takes effect on the next scheduled run.", release.Version, installedPath)
}

// downloadAndVerify fetches url into destPath and confirms the bytes
// hash to expectedSHA256 (hex, case-insensitive).
//
// The digest check is the load-bearing security control of this whole
// feature, and is why the presigned URL can be treated as untrusted
// transport: even if the release bucket were misconfigured, or an
// attacker somehow substituted the object, bytes that don't match the
// digest the ingest_secret-authenticated API reported are never
// installed. The hash is computed while streaming to disk (io.MultiWriter)
// rather than by re-reading the file afterward, so there is no window in
// which a different set of bytes could be verified than the ones written.
//
// destPath is written and only *then* checked: a mismatching download
// is deleted by the caller. It is never renamed into place, so a
// failed verification can never result in an executed binary.
func downloadAndVerify(url, expectedSHA256, destPath string) error {
	expected := strings.ToLower(strings.TrimSpace(expectedSHA256))
	if expected == "" {
		// Should be impossible -- the backend refuses to publish a
		// release without a digest (see get_agent_release) -- but an
		// agent must never fall back to installing unverified bytes
		// just because a server told it to.
		return fmt.Errorf("release manifest reported no sha256; refusing to install an unverified binary")
	}

	resp, err := httpClient.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("HTTP %d downloading the update", resp.StatusCode)
	}

	out, err := os.OpenFile(destPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o755)
	if err != nil {
		return err
	}
	hasher := sha256.New()
	written, copyErr := io.Copy(io.MultiWriter(out, hasher), io.LimitReader(resp.Body, maxUpdateDownloadBytes+1))
	closeErr := out.Close()
	if copyErr != nil {
		return copyErr
	}
	if closeErr != nil {
		return closeErr
	}
	if written > maxUpdateDownloadBytes {
		return fmt.Errorf("update download exceeded the %d-byte limit; refusing it", int64(maxUpdateDownloadBytes))
	}
	if written == 0 {
		return fmt.Errorf("update download was empty")
	}

	actual := hex.EncodeToString(hasher.Sum(nil))
	if actual != expected {
		return fmt.Errorf("sha256 mismatch: expected %s, downloaded %s", expected, actual)
	}
	return nil
}

// swapInInstalledUpdate replaces installedPath with the verified
// staging file, keeping the outgoing binary as
// claude_teams_member_agent.exe.old.
//
// Two renames, never a copy-over-the-top, because on Windows an .exe
// that is currently executing cannot be *written to* -- and during a
// scheduled run the installed copy is exactly that, this very process's
// own image. It can, however, be *renamed* while running (Windows
// permits renaming a mapped image; it's writing that is blocked), which
// is what makes an in-place update of a running agent possible at all.
//
// Order matters: move the running/old binary aside first, then move the
// verified new one into the now-free name. If the second rename fails,
// the first is undone so the member is never left with no installed
// agent at all -- a scheduled task pointing at a missing .exe would
// silently stop reporting usage forever, which is strictly worse than
// staying on an old version.
func swapInInstalledUpdate(installedPath, stagingPath, installDir string) error {
	previousPath := filepath.Join(installDir, updatePreviousName)
	// A leftover .old from a previous update (not yet cleaned up
	// because it was still the running image at the time) would make
	// this rename fail on Windows, where Rename does not overwrite an
	// existing file the way it does on Unix.
	_ = os.Remove(previousPath)

	if err := os.Rename(installedPath, previousPath); err != nil {
		return fmt.Errorf("moving the current installed exe aside: %w", err)
	}
	if err := os.Rename(stagingPath, installedPath); err != nil {
		// Put the old one back rather than leaving the install
		// directory with no claude_teams_member_agent.exe.
		if restoreErr := os.Rename(previousPath, installedPath); restoreErr != nil {
			return fmt.Errorf(
				"installing the new exe failed (%v) AND restoring the previous one failed (%v). The previous version is still on disk as %s -- rename it back to %s, or re-run the admin-distributed exe, to recover",
				err, restoreErr, previousPath, installedPath,
			)
		}
		return fmt.Errorf("installing the new exe: %w", err)
	}
	return nil
}

// installedAgentPaths returns the fixed per-user install directory and
// the installed .exe path inside it -- the same location
// copySelfToFixedLocation copies to. Shared so the updater and the
// scheduler can never disagree about where the installed copy lives.
func installedAgentPaths() (installDir string, installedPath string, err error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", "", err
	}
	installDir = filepath.Join(home, ".claude", "claude_teams_member_agent")
	return installDir, filepath.Join(installDir, "claude_teams_member_agent.exe"), nil
}
