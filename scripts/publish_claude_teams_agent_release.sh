#!/usr/bin/env bash
# Publish a built claude_teams_member_agent.exe to the release bucket so
# every already-registered member's installed copy self-updates to it on
# its next scheduled run (see
# scripts/claude_teams_member_agent_go/updater.go and
# backend/app/claude_teams/agent_release_repository.py).
#
# Usage:
#   scripts/publish_claude_teams_agent_release.sh \
#       --bucket <ClaudeTeamsAgentReleaseBucketName from the CDK output> \
#       --exe    path/to/claude_teams_member_agent.exe \
#       --version 2026.09.10-1430-abc1234
#
# The version MUST match what the .exe reports (`claude_teams_member_agent.exe
# -version`), because that string is exactly what each agent compares
# against to decide whether it is already up to date. Passing a mismatched
# version makes every member re-download the same binary on every run
# forever, so this script verifies the match when it can.
#
# What it does, in order:
#   1. computes the .exe's SHA256 (the digest each agent verifies the
#      bytes it downloads against before installing them -- the security
#      control this whole feature rests on),
#   2. uploads the .exe under a version-scoped key, so previously
#      published releases stay downloadable and a rollback is just a
#      manifest edit,
#   3. uploads manifest.json LAST, so no member can ever be pointed at a
#      release whose object hasn't finished uploading.
#
# Rolling back: re-run with an older --version and the matching --exe, or
# hand-edit manifest.json to name a previously uploaded key.

set -euo pipefail

BUCKET=""
EXE=""
VERSION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bucket) BUCKET="$2"; shift 2 ;;
        --exe) EXE="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        -h|--help) sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "ERROR: unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [[ -z "$BUCKET" || -z "$EXE" || -z "$VERSION" ]]; then
    echo "ERROR: --bucket, --exe and --version are all required. Run with --help." >&2
    exit 1
fi
if [[ ! -f "$EXE" ]]; then
    echo "ERROR: no such file: $EXE" >&2
    exit 1
fi

# Cross-check the version against what the binary itself reports, when
# that's possible (the .exe is a Windows binary, so this only works where
# it can actually execute -- Windows, or Linux with wine). A mismatch here
# is the single most consequential mistake this script can make, so it is
# a hard failure rather than a warning wherever it is detectable.
REPORTED=""
if command -v wine >/dev/null 2>&1; then
    REPORTED="$(wine "$EXE" -version 2>/dev/null | awk '{print $2}' || true)"
elif [[ "${OS:-}" == "Windows_NT" ]]; then
    REPORTED="$("$EXE" -version 2>/dev/null | awk '{print $2}' || true)"
fi
if [[ -n "$REPORTED" && "$REPORTED" != "$VERSION" ]]; then
    echo "ERROR: the exe reports version '$REPORTED' but --version says '$VERSION'." >&2
    echo "       Publishing a mismatched version makes every member re-download this" >&2
    echo "       binary on every scheduled run forever. Fix --version and re-run." >&2
    exit 1
fi
if [[ -z "$REPORTED" ]]; then
    echo "NOTE: could not run the .exe here to confirm its baked-in version."
    echo "      Double-check that '$VERSION' matches 'claude_teams_member_agent.exe -version'."
fi

SHA256="$(sha256sum "$EXE" | awk '{print $1}')"
KEY="releases/${VERSION}/claude_teams_member_agent.exe"

echo "Publishing:"
echo "  bucket:  $BUCKET"
echo "  key:     $KEY"
echo "  version: $VERSION"
echo "  sha256:  $SHA256"

aws s3api put-object \
    --bucket "$BUCKET" \
    --key "$KEY" \
    --body "$EXE" \
    --content-type application/octet-stream \
    >/dev/null

MANIFEST="$(mktemp)"
trap 'rm -f "$MANIFEST"' EXIT
cat >"$MANIFEST" <<JSON
{
  "version": "${VERSION}",
  "sha256": "${SHA256}",
  "key": "${KEY}"
}
JSON

# Last, deliberately: until this object changes, every agent still sees
# the previous release and nothing has been rolled out.
aws s3api put-object \
    --bucket "$BUCKET" \
    --key manifest.json \
    --body "$MANIFEST" \
    --content-type application/json \
    >/dev/null

echo
echo "Published. Already-registered members pick this up on their next"
echo "scheduled run (hourly by default); it takes effect on the run after"
echo "the one that downloads it."
