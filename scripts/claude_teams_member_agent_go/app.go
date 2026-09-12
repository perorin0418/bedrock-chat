package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// notifyUser is the seam through which every desktop notification in
// this package is raised. Production code leaves it nil, which means
// "call the real showUserNotification" (a modal MessageBoxW on
// Windows); tests substitute a recorder so they can assert on whether a
// member would actually be interrupted.
//
// Worth a seam of its own because "does this situation deserve a modal
// dialog on a member's screen?" is a product decision that has already
// been got wrong once: a popup fired on every scheduled run for an
// expired local Claude Code login, which is the *normal* state for
// anyone not currently using Claude Code (see reportUsageFailure).
var notifyUser func(title, message string)

func (a *appContext) notify(title, message string) {
	if notifyUser != nil {
		notifyUser(title, message)
		return
	}
	showUserNotification(title, message)
}

type appContext struct {
	apiEndpoint          string
	registrationSecret   string
	displayName          string
	taskIntervalMinutes  int
	taskName             string
	skipTaskRegistration bool
	// Opt out of the release-channel self-update (see updater.go) while
	// still keeping the scheduled task. For an admin pinning a member
	// to a specific build to reproduce a problem, and for tests.
	skipSelfUpdate  bool
	configPath      string
	credentialsPath string
	// Explicit Claude Code CLI location from -claude-path /
	// CLAUDE_TEAMS_AGENT_CLAUDE_PATH. Empty means "search %PATH% and
	// the well-known install locations" -- see claudecli.go.
	claudeCLIPath string
	// Whether -config-path/-credentials-path were explicitly passed
	// (vs. resolved from the default %USERPROFILE%\.claude\... path).
	// Needed so registerSelfAsScheduledTask only re-passes them to the
	// scheduled re-run when they're not already recoverable from
	// scratch -- see that function's comments on schtasks' /TR 261-char
	// limit.
	configPathOverridden      bool
	credentialsPathOverridden bool
}

// run is the direct equivalent of claude_teams_member_agent.ps1's
// "--- Main ---" section (see that file's history for why each step
// below is ordered/guarded the way it is; the .ps1 version is kept
// side-by-side in this repo during the transition so behavior can be
// diffed 1:1).
func (a *appContext) run() {
	cfg, err := readLocalConfig(a.configPath)
	if err != nil {
		fatalf("%v", err)
	}

	justRegistered := false
	if cfg == nil {
		regResp, err := a.registerThisMachine()
		if err != nil {
			fatalf("%v", err)
		}
		justRegistered = true
		writeErr := writeLocalConfig(a.configPath, localConfig{
			TokenID:      regResp.TokenID,
			IngestSecret: regResp.IngestSecret,
		})
		if writeErr != nil {
			fatalf("registered token_id=%s but failed to save local config to %s: %v", regResp.TokenID, a.configPath, writeErr)
		}
		infof("Registered as token_id=%s. Saved local config to %s.", regResp.TokenID, a.configPath)
		infof("")
		cfg = &localConfig{TokenID: regResp.TokenID, IngestSecret: regResp.IngestSecret}
	}

	tokenID := cfg.TokenID
	ingestSecret := cfg.IngestSecret

	// Is the chat-side token this machine registered still usable
	// server-side? Skipped when we *just* registered above (trivially
	// fresh, and the member is still at the console -- no reason to
	// nag them). See docs/CLAUDE_TEAMS_OAUTH.md's "Chat-token expiry
	// detection".
	if !justRegistered {
		enabled, ok := a.getChatTokenStatus(tokenID, ingestSecret)
		if ok && !enabled {
			if strings.TrimSpace(a.registrationSecret) != "" {
				infof("This machine's chat-side token is no longer usable by bedrock-chat. Re-registering a fresh one...")
				infof("")
				regResp, err := a.registerThisMachine()
				if err != nil {
					fatalf("%v", err)
				}
				if err := writeLocalConfig(a.configPath, localConfig{
					TokenID:      regResp.TokenID,
					IngestSecret: regResp.IngestSecret,
				}); err != nil {
					fatalf("re-registered token_id=%s but failed to save local config to %s: %v", regResp.TokenID, a.configPath, err)
				}
				infof("Registered as token_id=%s. Saved local config to %s.", regResp.TokenID, a.configPath)
				infof("")
				tokenID = regResp.TokenID
				ingestSecret = regResp.IngestSecret
			} else {
				warnf("bedrock-chat reports this machine's chat-side token as no longer usable. Re-run claude_teams_member_agent.exe (double-click it) to register a fresh one.")
				a.notifyChatTokenDisabled()
			}
		}
	}

	if !a.skipTaskRegistration {
		a.registerSelfAsScheduledTask()
	}

	// Auto-update the installed copy from the admin's published release,
	// if this deployment publishes one. Placed after task registration
	// (so a brand-new install is already scheduled and self-healing
	// before anything replaces its binary) and before the usage report
	// (so a failed/slow update can never be mistaken for a usage-
	// reporting failure -- and, since the swap only takes effect on the
	// *next* scheduled run, this run still reports usage with the
	// version it started as either way).
	//
	// Skipped entirely when -skip-task-registration was passed: that
	// flag means "don't manage anything about my installation", and
	// silently replacing the installed .exe would contradict it.
	if !a.skipTaskRegistration && !a.skipSelfUpdate {
		a.selfUpdateIfNeeded(tokenID, ingestSecret)
	}

	sampledAtMs := time.Now().UnixMilli()

	accessToken, err := a.resolveLocalAccessToken()
	if err != nil {
		fatalf("%v", err)
	}

	usage, statusCode, err := fetchAnthropicUsage(accessToken)
	if err != nil {
		a.reportUsageFailure(tokenID, ingestSecret, statusCode, err, sampledAtMs)
		waitForEnterIfInteractive()
		os.Exit(0)
	}

	fiveHourUtil := usage.FiveHour.Utilization
	fiveHourResets := usage.FiveHour.ResetsAt
	sevenDayUtil := usage.SevenDay.Utilization
	sevenDayResets := usage.SevenDay.ResetsAt

	err = a.ingestUsageSnapshot(tokenID, ingestSecret, usageSnapshotRequest{
		FetchStatus:         "ok",
		FiveHourUtilization: &fiveHourUtil,
		FiveHourResetsAt:    &fiveHourResets,
		SevenDayUtilization: &sevenDayUtil,
		SevenDayResetsAt:    &sevenDayResets,
		SampledAtMs:         &sampledAtMs,
	})
	if err != nil {
		fatalf("also failed to report usage to bedrock-chat: %v", err)
	}
	infof("OK: reported 5h=%.0f%% 7d=%.0f%% for token_id=%s", fiveHourUtil, sevenDayUtil, tokenID)
}

// resolveLocalAccessToken returns the access token to query
// Anthropic's usage endpoint with, refreshing the member's local Claude
// Code login first if -- and only if -- that login has already expired.
//
// "Only if already expired" is the entire safety argument, so it is
// worth stating plainly. This file belongs to the member's own Claude
// Code CLI, and OAuth refresh tokens rotate: whoever refreshes last
// invalidates the other side's copy. Refreshing a *live* token would
// therefore risk logging the member out of the CLI they are actively
// using -- breaking their real work to collect a usage statistic, which
// is plainly the wrong trade. But an already-expired access token means
// the CLI is not currently in use (Claude Code refreshes it as it goes,
// and it lapses within hours of disuse), so there is no live session to
// disturb: the token is dead to both sides, and renewing it is the
// action the CLI itself would take on next launch.
//
// A refresh failure is never fatal here. The existing (expired) token
// is returned and used anyway, so the usual 401 path runs and reports
// auth_error exactly as it did before this existed. That keeps the
// worst case identical to the old behavior rather than worse than it.
func (a *appContext) resolveLocalAccessToken() (string, error) {
	creds, err := readLocalCredentials(a.credentialsPath)
	if err != nil {
		return "", err
	}

	if !creds.isExpired(time.Now()) {
		return creds.AccessToken, nil
	}
	if creds.RefreshToken == "" {
		// Nothing to refresh with (an old credentials file, or one
		// written by a flow that stores no refresh token). Fall through
		// to the 401 path rather than failing the whole run.
		return creds.AccessToken, nil
	}

	infof("The local Claude Code login has expired; refreshing it to read usage limits...")
	refreshed, statusCode, err := refreshAnthropicToken(creds.RefreshToken)
	if err != nil {
		warnf("could not refresh the local Claude Code login (HTTP %d: %v). Continuing with the expired token; usage will be reported as auth_error for this run.", statusCode, err)
		return creds.AccessToken, nil
	}

	updated := localCredentials{
		AccessToken:  refreshed.AccessToken,
		RefreshToken: refreshed.RefreshToken,
	}
	if refreshed.ExpiresIn > 0 {
		updated.ExpiresAt = time.Now().Add(time.Duration(refreshed.ExpiresIn) * time.Second).UnixMilli()
	}

	// Persisted so the member's own CLI picks up the renewed login too,
	// and so the next scheduled run doesn't spend another refresh. A
	// write failure is survivable: this run still has a working token
	// in memory, so it proceeds and simply refreshes again next time.
	if err := writeRefreshedCredentials(a.credentialsPath, updated); err != nil {
		warnf("refreshed the local Claude Code login but could not save it to %s (%v). This run still works; the next run will refresh again.", a.credentialsPath, err)
	}

	return refreshed.AccessToken, nil
}

// notifyChatTokenDisabled interrupts the member with a modal dialog
// because bedrock-chat has permanently disabled the chat-side token
// this machine registered.
//
// This is the one situation in this program that earns a popup, and it
// is worth contrasting with the one that no longer gets one (see
// reportUsageFailure). The chat-side token is held server-side only --
// `claude setup-token` printed it once and saved it nowhere -- so
// nothing on the member's machine can notice it lapsed. Meanwhile their
// seat silently serves no chat at all until someone acts, and the fix
// is a single double-click they can perform. Rare, invisible otherwise,
// actionable, and costly to ignore: all four are why this one stays.
func (a *appContext) notifyChatTokenDisabled() {
	installDir := filepath.Join(homeDirOrEmpty(), ".claude", "claude_teams_member_agent")
	a.notify(
		"Claude Teams: chat token needs renewing",
		fmt.Sprintf(
			"The Claude token you shared with bedrock-chat for chat has stopped working (expired or revoked), so your seat is no longer serving chat requests. To fix it, double-click claude_teams_member_agent.exe in %s -- it will run 'claude setup-token' and register the new token for you. Usage reporting keeps working in the meantime.",
			installDir,
		),
	)
}

// reportUsageFailure mirrors claude_teams_usage_sync's own
// fetch_status semantics (see the .ps1 predecessor's identically named
// logic): a 401 from Anthropic's /api/oauth/usage means THIS machine's
// local .credentials.json accessToken is expired/revoked
// ("auth_error"), not that a usage limit was hit, and is unrelated to
// the chat-side token registered separately at first run. Any other
// failure (network, 5xx, ...) is a generic "error" and does NOT mean
// the token is invalid.
//
// Deliberately silent (no desktop popup) on that 401, unlike the
// chat-token case in run(). The local Claude Code login this reads
// expires within hours of not using Claude Code, and this agent never
// refreshes it -- so for a member who simply isn't using Claude Code
// right now, the 401 is the normal, expected state, not a problem to
// fix. Popping a dialog on every scheduled run would nag exactly the
// members with nothing to act on, and the thing it asked them to
// restore (usage-limit numbers for someone not consuming any usage)
// carries almost no information anyway. The snapshot is still reported
// as auth_error so the admin page reflects reality, and the warning
// still goes to stderr for anyone running the exe by hand.
func (a *appContext) reportUsageFailure(tokenID, ingestSecret string, statusCode int, fetchErr error, sampledAtMs int64) {
	var fetchStatus, errorMessage string
	if statusCode == 401 {
		fetchStatus = "auth_error"
		errorMessage = fmt.Sprintf(
			"401 Unauthorized calling /api/oauth/usage with this machine's .claude\\.credentials.json accessToken (expired or revoked) -- run 'claude login' on this machine to refresh it. This is unrelated to the chat-side token registered at first run, which is unaffected.",
		)
	} else {
		fetchStatus = "error"
		errorMessage = fmt.Sprintf("HTTP %d: %v", statusCode, fetchErr)
	}

	err := a.ingestUsageSnapshot(tokenID, ingestSecret, usageSnapshotRequest{
		FetchStatus:       fetchStatus,
		FetchErrorMessage: &errorMessage,
		SampledAtMs:       &sampledAtMs,
	})
	if err != nil {
		fatalf("also failed to report the failure itself to bedrock-chat: %v", err)
	}
	warnf("%s", errorMessage)
}

func homeDirOrEmpty() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return home
}
