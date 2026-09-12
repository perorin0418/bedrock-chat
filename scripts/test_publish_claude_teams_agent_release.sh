#!/usr/bin/env bash
# Tests for publish_claude_teams_agent_release.sh.
#
# This script is load-bearing in a way that is easy to underestimate: it
# decides what every member's machine will download and execute. The two
# mistakes that matter most are (a) publishing a manifest whose version
# doesn't match the binary, which makes every member re-download forever,
# and (b) publishing a manifest whose sha256 doesn't match the object,
# which every agent then refuses -- a silent, org-wide stuck rollout.
#
# `aws` is stubbed with a fake on PATH that records its arguments and
# copies uploaded bodies aside, so the upload payloads themselves can be
# asserted on without touching a real bucket.
#
# Run: scripts/test_publish_claude_teams_agent_release.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUBLISH="$SCRIPT_DIR/publish_claude_teams_agent_release.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FAILURES=0
pass() { echo "  ok: $1"; }
fail() { echo "  FAIL: $1"; echo "    $2"; FAILURES=$((FAILURES + 1)); }

# --- fake aws --------------------------------------------------------
FAKEBIN="$WORK/bin"
mkdir -p "$FAKEBIN"
cat >"$FAKEBIN/aws" <<'FAKE'
#!/usr/bin/env bash
# Record the call, and stash a copy of any --body payload under a name
# derived from its --key so tests can inspect what was uploaded.
echo "$*" >> "$AWS_CALL_LOG"
key=""; body=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --key) key="$2"; shift 2 ;;
        --body) body="$2"; shift 2 ;;
        *) shift ;;
    esac
done
if [[ -n "$body" && -n "$key" ]]; then
    cp "$body" "$AWS_UPLOAD_DIR/$(echo "$key" | tr '/' '_')"
fi
exit 0
FAKE
chmod +x "$FAKEBIN/aws"

export AWS_UPLOAD_DIR="$WORK/uploads"
mkdir -p "$AWS_UPLOAD_DIR"

run_publish() {
    export AWS_CALL_LOG="$WORK/aws_calls.txt"
    : >"$AWS_CALL_LOG"
    rm -f "$AWS_UPLOAD_DIR"/*
    PATH="$FAKEBIN:$PATH" "$PUBLISH" "$@" >"$WORK/stdout.txt" 2>"$WORK/stderr.txt"
    echo $?
}

# A stand-in for the built .exe. `-version` output is shaped exactly like
# main.go's: "claude_teams_member_agent <version>".
make_fake_exe() {
    local path="$1" version="$2"
    cat >"$path" <<EOF
#!/usr/bin/env bash
[ "\$1" = "-version" ] && echo "claude_teams_member_agent $version"
EOF
    chmod +x "$path"
}

echo "Checking argument validation..."

status="$(run_publish --bucket b --exe /nonexistent/path --version v1)"
if [[ "$status" == "1" ]] && grep -q "no such file" "$WORK/stderr.txt"; then
    pass "a missing --exe is rejected"
else
    fail "a missing --exe is rejected" "status=$status stderr=$(cat "$WORK/stderr.txt")"
fi

make_fake_exe "$WORK/agent.exe" "2026.09.10-abc1234"

for missing in "--bucket b --exe $WORK/agent.exe" "--exe $WORK/agent.exe --version v1" "--bucket b --version v1"; do
    status="$(run_publish $missing)"
    if [[ "$status" != "1" ]]; then
        fail "incomplete arguments are rejected" "'$missing' exited $status"
        break
    fi
done
[[ $FAILURES -eq 0 ]] && pass "incomplete arguments are rejected"

status="$(run_publish --bucket b --exe "$WORK/agent.exe" --version v1 --bogus x)"
if [[ "$status" == "1" ]]; then
    pass "an unknown argument is rejected rather than ignored"
else
    fail "an unknown argument is rejected" "exited $status"
fi

echo "Checking the version cross-check..."

# The mistake this guards against is silent and org-wide, so it must be
# a hard failure, and crucially must abort BEFORE any upload happens --
# a half-published release (object uploaded, manifest not) is worse than
# none.
status="$(OS=Windows_NT run_publish --bucket b --exe "$WORK/agent.exe" --version wrong-version)"
if [[ "$status" == "1" ]] && grep -q "reports version" "$WORK/stderr.txt"; then
    if [[ ! -s "$WORK/aws_calls.txt" ]]; then
        pass "a version mismatch aborts before uploading anything"
    else
        fail "a version mismatch aborts before uploading" "aws was still called: $(cat "$WORK/aws_calls.txt")"
    fi
else
    fail "a version mismatch is rejected" "status=$status stderr=$(cat "$WORK/stderr.txt")"
fi

status="$(OS=Windows_NT run_publish --bucket my-bucket --exe "$WORK/agent.exe" --version 2026.09.10-abc1234)"
if [[ "$status" == "0" ]]; then
    pass "a matching version is accepted"
else
    fail "a matching version is accepted" "status=$status stderr=$(cat "$WORK/stderr.txt")"
fi

# Where the .exe can't be executed (building on Linux for Windows), the
# check can't run -- that must degrade to a warning, not a hard failure,
# or publishing would be impossible from a Linux CI box.
status="$(run_publish --bucket my-bucket --exe "$WORK/agent.exe" --version any-version-at-all)"
if [[ "$status" == "0" ]] && grep -q "could not run the .exe" "$WORK/stdout.txt"; then
    pass "an unverifiable version warns but still publishes"
else
    fail "an unverifiable version warns but publishes" "status=$status out=$(cat "$WORK/stdout.txt")"
fi

echo "Checking the upload payloads..."

status="$(OS=Windows_NT run_publish --bucket my-bucket --exe "$WORK/agent.exe" --version 2026.09.10-abc1234)"
expected_sha="$(sha256sum "$WORK/agent.exe" | awk '{print $1}')"
manifest="$AWS_UPLOAD_DIR/manifest.json"

if [[ -f "$manifest" ]]; then
    got_sha="$(grep -o '"sha256": "[^"]*"' "$manifest" | cut -d'"' -f4)"
    got_ver="$(grep -o '"version": "[^"]*"' "$manifest" | cut -d'"' -f4)"
    got_key="$(grep -o '"key": "[^"]*"' "$manifest" | cut -d'"' -f4)"

    # The digest is what each agent verifies downloaded bytes against.
    # If this is wrong, every member refuses the release.
    if [[ "$got_sha" == "$expected_sha" ]]; then
        pass "the manifest carries the exe's real sha256"
    else
        fail "the manifest carries the exe's real sha256" "got $got_sha, want $expected_sha"
    fi

    if [[ "$got_ver" == "2026.09.10-abc1234" ]]; then
        pass "the manifest carries the requested version"
    else
        fail "the manifest carries the requested version" "got $got_ver"
    fi

    # Version-scoped keys are what make rollback possible: publishing a
    # new release must never overwrite a previous one's object.
    if [[ "$got_key" == "releases/2026.09.10-abc1234/claude_teams_member_agent.exe" ]]; then
        pass "the release object key is version-scoped (rollback stays possible)"
    else
        fail "the release object key is version-scoped" "got $got_key"
    fi

    if [[ -f "$AWS_UPLOAD_DIR/$(echo "$got_key" | tr '/' '_')" ]]; then
        pass "the exe is uploaded at the key the manifest names"
    else
        fail "the exe is uploaded at the key the manifest names" "no upload recorded for $got_key"
    fi
else
    fail "a manifest is uploaded" "no manifest.json upload recorded"
fi

# Ordering is a correctness property, not a style choice: if the
# manifest landed first, an agent polling in between would be pointed at
# an object that isn't fully uploaded yet.
manifest_line="$(grep -n "key manifest.json" "$WORK/aws_calls.txt" | head -1 | cut -d: -f1)"
exe_line="$(grep -n "key releases/" "$WORK/aws_calls.txt" | head -1 | cut -d: -f1)"
if [[ -n "$manifest_line" && -n "$exe_line" && "$exe_line" -lt "$manifest_line" ]]; then
    pass "the exe is uploaded before the manifest that points at it"
else
    fail "the exe is uploaded before the manifest" "exe at line $exe_line, manifest at line $manifest_line"
fi

# A second publish of a different version must not disturb the first.
make_fake_exe "$WORK/agent2.exe" "2026.09.11-def5678"
status="$(OS=Windows_NT run_publish --bucket my-bucket --exe "$WORK/agent2.exe" --version 2026.09.11-def5678)"
new_key="$(grep -o '"key": "[^"]*"' "$AWS_UPLOAD_DIR/manifest.json" | cut -d'"' -f4)"
if [[ "$new_key" == "releases/2026.09.11-def5678/claude_teams_member_agent.exe" ]]; then
    pass "a second release uses its own key, leaving the first intact"
else
    fail "a second release uses its own key" "got $new_key"
fi

echo
if [[ $FAILURES -gt 0 ]]; then
    echo "FAILED: $FAILURES check(s)."
    exit 1
fi
echo "PASS: publish script behaves as intended."
