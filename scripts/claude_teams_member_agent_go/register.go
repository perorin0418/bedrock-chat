package main

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// registerThisMachine is the direct equivalent of the .ps1
// predecessor's Register-ThisMachine. See that function's extensive
// comments (preserved here) for why each step is ordered/guarded the
// way it is.
func (a *appContext) registerThisMachine() (*registerResponse, error) {
	if strings.TrimSpace(a.registrationSecret) == "" {
		return nil, fmt.Errorf(
			"no local config found at %s and -registration-secret was not provided. Ask your admin for the registration secret (Claude Teams Tokens > Registration Secret) and pass it as -registration-secret on this first run",
			a.configPath,
		)
	}

	infof("First run: no local config found. Setting up this machine as a new Claude Teams pool token...")
	infof("")

	// Deliberately does NOT read %USERPROFILE%\.claude\.credentials.json
	// here, even if it already exists. 'claude setup-token' mints a
	// long-lived, inference-only-scoped token and PRINTS it to this
	// terminal -- per Anthropic's own docs, it never saves it anywhere
	// (not to .credentials.json, not to any file). A pre-existing
	// .credentials.json is written by the normal interactive `claude
	// login`/`/login` flow instead, and typically carries a much
	// broader scope (user:profile, org:create_api_key, etc. depending
	// on the account) -- silently reusing that file here would send a
	// broader-scoped credential to bedrock-chat than intended without
	// the member realizing it. Always running setup-token and having
	// the member paste its output guarantees the token actually sent
	// is the narrow-scope one.
	infof("Running 'claude setup-token' -- a browser window will open for you to approve.")
	infof("It will print a long-lived OAuth token to this terminal when done. It does NOT save that token anywhere -- copy it, you'll be asked to paste it below.")
	cmd := exec.Command("claude", "setup-token")
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("'claude setup-token' failed: %w. Fix the login and re-run this program", err)
	}
	infof("")

	// Displayed (not masked) input: the token was just printed to this
	// same terminal moments ago by 'claude setup-token' above, so
	// masking the paste would only make it impossible to tell whether
	// the paste actually went through (it doesn't protect anything
	// already on screen). Read as a plain line instead of
	// golang.org/x/term's password mode.
	fmt.Print("Paste the OAuth token 'claude setup-token' printed above: ")
	chatToken, err := readLine()
	if err != nil {
		return nil, fmt.Errorf("could not read pasted token: %w", err)
	}
	chatToken = strings.TrimSpace(chatToken)
	if chatToken == "" {
		return nil, fmt.Errorf("no token entered. Re-run this program (re-running 'claude setup-token' if needed) and paste the printed token when prompted")
	}

	displayName := a.displayName
	if strings.TrimSpace(displayName) == "" {
		displayName, err = resolveDisplayName()
		if err != nil {
			return nil, err
		}
	}

	resp, err := a.registerToken(displayName, chatToken)
	if err != nil {
		return nil, err
	}
	return resp, nil
}

// resolveDisplayName is the direct equivalent of the .ps1
// predecessor's Resolve-DisplayName. Only ever called on a genuine
// first run (see registerThisMachine), so an interactive prompt here
// is safe -- the member is already sitting at the console approving
// 'claude setup-token' in a browser moments earlier.
func resolveDisplayName() (string, error) {
	home, err := os.UserHomeDir()
	if err == nil {
		claudeJSONPath := filepath.Join(home, ".claude.json")
		if email, ok := resolveDisplayNameFromClaudeJSON(claudeJSONPath); ok {
			return email, nil
		}
		warnf("Could not automatically determine your Claude account email from %s.", claudeJSONPath)
	}

	// No emailAddress found (missing file, unreadable, or field
	// absent) -- ask the member directly rather than silently falling
	// back to a hostname/username string an admin may not recognize.
	// An empty answer is a hard failure, not a silent fallback: an
	// unrecognizable "hostname-username" entry on the admin page is
	// exactly the confusing state this prompt exists to avoid.
	fmt.Print("Enter a display name for this token (e.g. your Claude account email): ")
	reader := bufio.NewReader(os.Stdin)
	entered, _ := reader.ReadString('\n')
	entered = strings.TrimSpace(entered)
	if entered == "" {
		return "", fmt.Errorf("no display name entered. Re-run this program and enter a display name (e.g. your Claude account email) when prompted")
	}
	return entered, nil
}

// readLine reads one line from stdin, echoed normally (the terminal's
// own default behavior).
func readLine() (string, error) {
	reader := bufio.NewReader(os.Stdin)
	line, err := reader.ReadString('\n')
	if err != nil {
		return "", err
	}
	return line, nil
}
