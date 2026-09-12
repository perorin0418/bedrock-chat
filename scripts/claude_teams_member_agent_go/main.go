// Package main implements claude_teams_member_agent as a single Windows
// executable, replacing the previous claude_teams_member_agent.ps1 +
// claude_teams_member_agent.bat pair.
//
// Why a rewrite, not a translation-in-place: the .ps1/.bat pair hit a
// string of PowerShell-specific footguns during hardening (see
// docs/CLAUDE_TEAMS_OAUTH.md and this repo's history) --
// $PSScriptRoot resolving empty through a .bat->powershell.exe -File
// double-indirection, PowerShell 5.1's ConvertFrom-Json choking on a
// large real-world ~/.claude.json, camelCase/snake_case API-response
// mismatches, execution-policy friction, etc. A single compiled .exe
// removes that entire footgun class structurally: no second
// interpreter hop, no host-version-dependent JSON parser, one
// self-contained binary to distribute.
//
// Distribution model, preserved 1:1 from the .bat era: an admin edits
// two build-time defaults (the org-wide Registration Secret and the
// bedrock-chat API endpoint) and compiles this into a single .exe --
// see BUILD.md in this folder -- then hands that one .exe to every
// member. Members run it once (interactively, for the `claude
// setup-token` browser approval); it registers itself, sets up its own
// hourly Task Scheduler entry, and needs nothing further.
//
// Compatibility, also preserved 1:1: the local config file location
// (%USERPROFILE%\.claude\claude_teams_member_agent.config.json) and
// its JSON shape (token_id/ingest_secret, snake_case) are unchanged
// from the .ps1 version, so a member already registered via the old
// script can switch to this .exe with zero re-registration -- it just
// reads the same file. Every backend HTTP endpoint, request/response
// shape, and error-handling semantic (401 vs status-disabled vs
// network error) is unchanged from the .ps1 version; see
// docs/CLAUDE_TEAMS_OAUTH.md, which documents both.
package main

import (
	"bufio"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// Set at build time via -ldflags (see BUILD.md). Baking these into the
// binary is exactly equivalent, security-wise, to the previous design:
// claude_teams_member_agent.bat carried both values in plaintext and
// was itself copied to the fixed install location by
// copySelfToFixedLocation, so a distributed artifact holding the
// org-wide secret at rest indefinitely is not a new exposure -- see
// docs/CLAUDE_TEAMS_OAUTH.md's note on the Registration Secret's trust
// model. A compiled .exe's strings are at least somewhat less
// casually readable than an open-in-Notepad .bat, if anything a mild
// improvement.
var (
	defaultAPIEndpoint       = ""
	defaultRegistrationSecret = ""
)

const (
	usageAPIURL      = "https://api.anthropic.com/api/oauth/usage"
	anthropicVersion = "2023-06-01"
	oauthBetaHeader  = "oauth-2025-04-20"
	requestTimeout   = 15 * time.Second
	defaultTaskName  = "ClaudeTeamsMemberAgent"
)

var placeholderPattern = regexp.MustCompile(`(?i)PASTE_API_ENDPOINT_HERE|PASTE_REGISTRATION_SECRET_HERE`)

// waitForEnterAtExit controls whether fatalf/main pause for a final
// keypress before the process exits. Set once in main() from the
// -unattended flag (true when this run was launched by our own
// self-registered Task Scheduler entry -- see
// registerSelfAsScheduledTask, which always passes -unattended). A
// member double-clicking the .exe manually (or running it from an
// already-open terminal) leaves this false, so they get a chance to
// read the final output/any error before the window closes, instead
// of it flashing shut immediately the way a console app normally does
// when double-clicked from Explorer.
var waitForEnterAtExit = false

func waitForEnterIfInteractive() {
	if waitForEnterAtExit {
		return
	}
	fmt.Println()
	fmt.Print("Press Enter to close this window...")
	bufio.NewReader(os.Stdin).ReadString('\n')
}

func main() {
	registrationSecret := flag.String("registration-secret", defaultAPIEndpointOrEmpty(defaultRegistrationSecret), "Org-wide self-registration secret from the admin's \"Claude Teams Tokens\" page. Only needed on first run or to re-register a disabled token; ignored otherwise.")
	apiEndpoint := flag.String("api-endpoint", "", "bedrock-chat backend API base URL, e.g. https://xxxxxxxxxx.execute-api.us-east-1.amazonaws.com. Defaults to the value baked in at build time, or CLAUDE_TEAMS_AGENT_API_ENDPOINT if set (see BUILD.md).")
	displayName := flag.String("display-name", "", "Display name shown on the admin page. Defaults to the Claude account email from ~/.claude.json if omitted.")
	taskIntervalMinutes := flag.Int("task-interval-minutes", 60, "How often the self-registered Task Scheduler entry re-runs this exe.")
	taskName := flag.String("task-name", defaultTaskName, "Name of the Task Scheduler entry this exe registers/looks for.")
	skipTaskRegistration := flag.Bool("skip-task-registration", false, "Skip the Task Scheduler self-registration step entirely.")
	skipSelfUpdate := flag.Bool("skip-self-update", false, "Skip the automatic update of the installed copy from the admin's published release. Use to pin this machine to the current build.")
	showVersion := flag.Bool("version", false, "Print this exe's build version (as baked in via -ldflags; see BUILD.md) and exit.")
	configPath := flag.String("config-path", "", "Override the local state file path (token_id/ingest_secret). Defaults to %USERPROFILE%\\.claude\\claude_teams_member_agent.config.json, or CLAUDE_TEAMS_AGENT_CONFIG_PATH if set.")
	credentialsPath := flag.String("credentials-path", "", "Override the local Claude Code CLI credentials file path, used only for usage-limit tracking. Defaults to %USERPROFILE%\\.claude\\.credentials.json, or CLAUDE_TEAMS_AGENT_CREDENTIALS_PATH if set.")
	unattended := flag.Bool("unattended", false, "Set automatically by the self-registered Task Scheduler entry; skips the 'press Enter to close' prompt at exit that a manual double-click run shows. Do not pass this by hand.")
	claudePath := flag.String("claude-path", "", "Full path to the Claude Code CLI executable (e.g. C:\\Users\\you\\.local\\bin\\claude.exe). Only needed when it is not on %PATH% and not in a standard install location; defaults to CLAUDE_TEAMS_AGENT_CLAUDE_PATH if set.")
	flag.Parse()
	waitForEnterAtExit = *unattended

	// Handled before any config/endpoint validation below: `-version`
	// must work on a bare `go build` with nothing baked in, since its
	// whole purpose is telling an admin which build a member is on
	// (including "unset", i.e. a build that self-updating skips -- see
	// updater.go's agentVersion).
	if *showVersion {
		if strings.TrimSpace(agentVersion) == "" {
			fmt.Println("claude_teams_member_agent (version not set at build time; self-update disabled)")
		} else {
			fmt.Printf("claude_teams_member_agent %s\n", agentVersion)
		}
		return
	}

	// Resolution order for api-endpoint: -api-endpoint flag >
	// CLAUDE_TEAMS_AGENT_API_ENDPOINT env var > build-time default. The
	// env var exists solely so registerSelfAsScheduledTask can persist
	// a non-default endpoint for the recurring scheduled run without
	// growing schtasks' /TR command line past its 261-character limit
	// (see scheduler.go); a member's normal manual run never needs it.
	resolvedAPIEndpoint := *apiEndpoint
	if strings.TrimSpace(resolvedAPIEndpoint) == "" {
		resolvedAPIEndpoint = os.Getenv("CLAUDE_TEAMS_AGENT_API_ENDPOINT")
	}
	if strings.TrimSpace(resolvedAPIEndpoint) == "" {
		resolvedAPIEndpoint = defaultAPIEndpoint
	}

	if strings.TrimSpace(resolvedAPIEndpoint) == "" || placeholderPattern.MatchString(resolvedAPIEndpoint) {
		fatalf("-api-endpoint is empty or still a placeholder ('%s'). Ask your admin to rebuild this .exe with the real bedrock-chat API URL baked in (see BUILD.md), or pass -api-endpoint directly.", resolvedAPIEndpoint)
	}
	if !strings.HasPrefix(resolvedAPIEndpoint, "http://") && !strings.HasPrefix(resolvedAPIEndpoint, "https://") {
		fatalf("-api-endpoint ('%s') must start with http:// or https://. Ask your admin for the bedrock-chat Backend API URL (the API Gateway one -- NOT the CloudFront/frontend URL).", resolvedAPIEndpoint)
	}
	if placeholderPattern.MatchString(*registrationSecret) {
		fatalf("-registration-secret is still a placeholder value. Ask your admin to rebuild this .exe with the real Registration Secret baked in (see BUILD.md), or pass -registration-secret directly.")
	}

	configPathOverridden := strings.TrimSpace(*configPath) != ""
	resolvedConfigPath := *configPath
	if !configPathOverridden {
		if envVal := os.Getenv("CLAUDE_TEAMS_AGENT_CONFIG_PATH"); envVal != "" {
			resolvedConfigPath = envVal
			configPathOverridden = true
		} else {
			home, err := os.UserHomeDir()
			if err != nil {
				fatalf("could not determine home directory: %v", err)
			}
			resolvedConfigPath = filepath.Join(home, `.claude`, `claude_teams_member_agent.config.json`)
		}
	}
	credentialsPathOverridden := strings.TrimSpace(*credentialsPath) != ""
	resolvedCredentialsPath := *credentialsPath
	if !credentialsPathOverridden {
		if envVal := os.Getenv("CLAUDE_TEAMS_AGENT_CREDENTIALS_PATH"); envVal != "" {
			resolvedCredentialsPath = envVal
			credentialsPathOverridden = true
		} else {
			home, err := os.UserHomeDir()
			if err != nil {
				fatalf("could not determine home directory: %v", err)
			}
			resolvedCredentialsPath = filepath.Join(home, `.claude`, `.credentials.json`)
		}
	}

	// -claude-path > CLAUDE_TEAMS_AGENT_CLAUDE_PATH > auto-discovery
	// (PATH, then the well-known install locations -- see claudecli.go).
	resolvedClaudePath := strings.TrimSpace(*claudePath)
	if resolvedClaudePath == "" {
		resolvedClaudePath = strings.TrimSpace(os.Getenv(claudeCLIPathEnv))
	}

	app := &appContext{
		apiEndpoint:               strings.TrimRight(resolvedAPIEndpoint, "/"),
		registrationSecret:        *registrationSecret,
		displayName:               *displayName,
		taskIntervalMinutes:       *taskIntervalMinutes,
		taskName:                  *taskName,
		skipTaskRegistration:      *skipTaskRegistration,
		skipSelfUpdate:            *skipSelfUpdate,
		configPath:                resolvedConfigPath,
		credentialsPath:           resolvedCredentialsPath,
		configPathOverridden:      configPathOverridden,
		credentialsPathOverridden: credentialsPathOverridden,
		claudeCLIPath:             resolvedClaudePath,
	}

	app.run()
	waitForEnterIfInteractive()
}

func defaultAPIEndpointOrEmpty(v string) string { return v }

func fatalf(format string, args ...interface{}) {
	fmt.Fprintf(os.Stderr, "ERROR: "+format+"\n", args...)
	waitForEnterIfInteractive()
	os.Exit(1)
}

func warnf(format string, args ...interface{}) {
	fmt.Fprintf(os.Stderr, "WARNING: "+format+"\n", args...)
}

func infof(format string, args ...interface{}) {
	fmt.Printf(format+"\n", args...)
}

