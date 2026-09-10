package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type appContext struct {
	apiEndpoint          string
	registrationSecret   string
	displayName          string
	taskIntervalMinutes  int
	taskName             string
	skipTaskRegistration bool
	configPath           string
	credentialsPath      string
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
				installDir := filepath.Join(homeDirOrEmpty(), ".claude", "claude_teams_member_agent")
				showUserNotification(
					"Claude Teams: chat token needs renewing",
					fmt.Sprintf(
						"The Claude token you shared with bedrock-chat for chat has stopped working (expired or revoked), so your seat is no longer serving chat requests. To fix it, double-click claude_teams_member_agent.exe in %s -- it will run 'claude setup-token' and register the new token for you. Usage reporting keeps working in the meantime.",
						installDir,
					),
				)
			}
		}
	}

	if !a.skipTaskRegistration {
		a.registerSelfAsScheduledTask()
	}

	sampledAtMs := time.Now().UnixMilli()

	accessToken, err := readLocalAccessToken(a.credentialsPath)
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

// reportUsageFailure mirrors claude_teams_usage_sync's own
// fetch_status semantics (see the .ps1 predecessor's identically named
// logic): a 401 from Anthropic's /api/oauth/usage means THIS machine's
// local .credentials.json accessToken is expired/revoked
// ("auth_error"), not that a usage limit was hit, and is unrelated to
// the chat-side token registered separately at first run. Any other
// failure (network, 5xx, ...) is a generic "error" and does NOT mean
// the token is invalid.
func (a *appContext) reportUsageFailure(tokenID, ingestSecret string, statusCode int, fetchErr error, sampledAtMs int64) {
	var fetchStatus, errorMessage string
	if statusCode == 401 {
		fetchStatus = "auth_error"
		errorMessage = fmt.Sprintf(
			"401 Unauthorized calling /api/oauth/usage with this machine's .claude\\.credentials.json accessToken (expired or revoked) -- run 'claude login' on this machine to refresh it. This is unrelated to the chat-side token registered at first run, which is unaffected.",
		)
		showUserNotification(
			"Claude Teams: usage tracking needs re-login",
			"Your Claude Code login has expired, so 5-hour/7-day usage tracking stopped working (chat itself is unaffected). Run 'claude login' in a terminal to fix it -- tracking resumes automatically on the next scheduled run, or re-run claude_teams_member_agent.exe now to confirm it right away.",
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
