"""End-to-end check of the agent auto-update path.

Runs the real FastAPI route (backend/app/routes/claude_teams_ingest.py)
against a moto-backed S3 release bucket, then points the real
cross-compiled Go agent at it and confirms the agent replaces its own
installed copy with the published bytes.

Not part of the unit test suite: it needs a Go toolchain and moto. Run
manually with:

    /tmp/bcvenv/bin/python scripts/e2e_claude_teams_agent_update.py
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ["BEDROCK_REGION"] = "us-east-1"
os.environ["CLAUDE_TEAMS_AGENT_RELEASE_BUCKET"] = "agent-release-bucket"

import boto3
import uvicorn
from fastapi import FastAPI
from moto.server import ThreadedMotoServer

GO = "/usr/local/go/bin/go"
AGENT_DIR = REPO / "scripts" / "claude_teams_member_agent_go"

TOKEN_ID = "tok-e2e"
INGEST_SECRET = "ingest-secret-e2e"
OLD_VERSION = "2026.09.01-old"
NEW_VERSION = "2026.09.10-new"


def fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def main():
    moto = ThreadedMotoServer(port=5111)
    moto.start()
    os.environ["AWS_ENDPOINT_URL"] = "http://127.0.0.1:5111"
    os.environ["AWS_ENDPOINT_URL_S3"] = "http://127.0.0.1:5111"

    tmp = Path(tempfile.mkdtemp())
    try:
        # 1. Build the "new" agent (the release) and the "old" one the
        #    member is currently running. Same source, different baked-in
        #    version -- exactly how a real rebuild differs.
        print("Building the published (new) agent...")
        new_exe = tmp / "new_agent"
        subprocess.run(
            [GO, "build", "-ldflags", f"-X main.agentVersion={NEW_VERSION}", "-o", str(new_exe), "."],
            cwd=AGENT_DIR, check=True,
        )
        new_bytes = new_exe.read_bytes()
        sha256 = hashlib.sha256(new_bytes).hexdigest()

        print("Building the member's currently-installed (old) agent...")
        old_exe = tmp / "old_agent"
        subprocess.run(
            [GO, "build", "-ldflags", f"-X main.agentVersion={OLD_VERSION}", "-o", str(old_exe), "."],
            cwd=AGENT_DIR, check=True,
        )

        # 2. Publish it: object under a version-scoped key, then manifest.
        s3 = boto3.client("s3", endpoint_url="http://127.0.0.1:5111")
        s3.create_bucket(Bucket="agent-release-bucket")
        key = f"releases/{NEW_VERSION}/claude_teams_member_agent.exe"
        s3.put_object(Bucket="agent-release-bucket", Key=key, Body=new_bytes)
        s3.put_object(
            Bucket="agent-release-bucket",
            Key="manifest.json",
            Body=json.dumps({"version": NEW_VERSION, "sha256": sha256, "key": key}).encode(),
        )
        print(f"Published {NEW_VERSION} (sha256 {sha256[:16]}...)")

        # 3. Serve the real route, with only the DynamoDB token lookup
        #    stubbed (the pool table itself is out of scope here).
        from unittest.mock import patch

        from app.claude_teams.token_repository import ClaudeTeamsTokenItem
        from app.routes.claude_teams_ingest import router

        token = ClaudeTeamsTokenItem(
            token_id=TOKEN_ID, display_name="member-pc", enabled=True,
            created_at=1, ingest_secret=INGEST_SECRET,
        )
        api = FastAPI()
        api.include_router(router)

        with patch("app.usecases.claude_teams_admin.get_token", return_value=token):
            config = uvicorn.Config(api, host="127.0.0.1", port=5112, log_level="warning")
            server = uvicorn.Server(config)
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.1)
            if not server.started:
                fail("uvicorn did not start")

            endpoint = "http://127.0.0.1:5112"

            # 4. Set up a fake member home with the old agent installed,
            #    plus the local config a registered machine already has.
            home = tmp / "home"
            install_dir = home / ".claude" / "claude_teams_member_agent"
            install_dir.mkdir(parents=True)
            installed = install_dir / "claude_teams_member_agent.exe"
            shutil.copy(old_exe, installed)
            os.chmod(installed, 0o755)
            (home / ".claude" / "claude_teams_member_agent.config.json").write_text(
                json.dumps({"token_id": TOKEN_ID, "ingest_secret": INGEST_SECRET})
            )

            # 5. A wrong secret must get a 401 and change nothing.
            print("Checking a wrong ingest_secret is refused...")
            import urllib.error
            import urllib.request

            req = urllib.request.Request(
                f"{endpoint}/claude-teams-tokens/{TOKEN_ID}/agent-version",
                data=json.dumps({"ingest_secret": "wrong"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req)
                fail("a wrong ingest_secret was accepted")
            except urllib.error.HTTPError as e:
                if e.code != 401:
                    fail(f"expected 401 for a wrong secret, got {e.code}")
            print("  ok: 401")

            # 6. Run the real agent's update path (as the old installed
            #    copy would on a scheduled run).
            print("Running the old agent's self-update...")
            env = dict(os.environ)
            env["HOME"] = str(home)
            env["USERPROFILE"] = str(home)
            result = subprocess.run(
                [
                    str(old_exe),
                    "-api-endpoint", endpoint,
                    "-registration-secret", "unused",
                    "-unattended",
                    "-skip-task-registration=false",
                    # No Claude Code CLI/credentials in this sandbox, so
                    # the usage report at the end of run() fails -- the
                    # update happens before it, which is itself the point:
                    # updating must not depend on usage reporting working.
                    "-credentials-path", str(tmp / "no-such-credentials.json"),
                    "-task-name", "E2ETestTaskDoesNotExist",
                ],
                env=env, capture_output=True, text=True, timeout=120,
            )
            print("  agent stdout:", result.stdout.strip().replace("\n", "\n    "))
            if result.stderr.strip():
                print("  agent stderr:", result.stderr.strip().replace("\n", "\n    "))

            # 7. The installed copy must now be the published bytes.
            got = installed.read_bytes()
            if got != new_bytes:
                fail(
                    f"installed exe was not replaced (size {len(got)} vs published {len(new_bytes)})"
                )
            print("  ok: installed copy replaced with the published bytes")

            previous = install_dir / "claude_teams_member_agent.exe.old"
            if not previous.exists():
                fail("the previous version was not kept as .old")
            print("  ok: previous version retained for rollback")

            # 8. And it really is the new version, per the binary itself.
            version_out = subprocess.run(
                [str(installed), "-version"], capture_output=True, text=True, timeout=60
            ).stdout.strip()
            if NEW_VERSION not in version_out:
                fail(f"installed exe reports {version_out!r}, expected {NEW_VERSION}")
            print(f"  ok: installed exe reports {version_out!r}")

            # 9. Re-running is a no-op now that versions match.
            print("Re-running the now-current agent (should not re-download)...")
            before = installed.stat().st_mtime_ns
            rerun = subprocess.run(
                [
                    str(installed),
                    "-api-endpoint", endpoint,
                    "-registration-secret", "unused",
                    "-unattended",
                    "-credentials-path", str(tmp / "no-such-credentials.json"),
                    "-task-name", "E2ETestTaskDoesNotExist",
                ],
                env=env, capture_output=True, text=True, timeout=120,
            )
            if "Updating the installed copy" in rerun.stdout:
                fail("an up-to-date agent tried to update again")
            if installed.stat().st_mtime_ns != before:
                fail("an up-to-date agent rewrote the installed copy")
            print("  ok: no re-download when already current")
            if previous.exists():
                fail("the .old file should have been cleaned up on the next run")
            print("  ok: previous version cleaned up on the following run")

            # 10. Tampering: publish a manifest whose sha256 no longer
            #     matches the object it names. The agent must refuse to
            #     install it and keep the version it has -- this is the
            #     control that makes the presigned URL safe to treat as
            #     untrusted transport.
            print("Publishing a tampered release (object != manifest digest)...")
            tampered_key = "releases/2026.09.11-tampered/claude_teams_member_agent.exe"
            s3.put_object(
                Bucket="agent-release-bucket",
                Key=tampered_key,
                Body=b"this is not the binary the manifest promised",
            )
            s3.put_object(
                Bucket="agent-release-bucket",
                Key="manifest.json",
                Body=json.dumps(
                    {
                        "version": "2026.09.11-tampered",
                        # The digest of some other, legitimate content.
                        "sha256": hashlib.sha256(b"what the admin actually built").hexdigest(),
                        "key": tampered_key,
                    }
                ).encode(),
            )
            current_bytes = installed.read_bytes()
            tamper_run = subprocess.run(
                [
                    str(installed),
                    "-api-endpoint", endpoint,
                    "-registration-secret", "unused",
                    "-unattended",
                    "-credentials-path", str(tmp / "no-such-credentials.json"),
                    "-task-name", "E2ETestTaskDoesNotExist",
                ],
                env=env, capture_output=True, text=True, timeout=120,
            )
            if installed.read_bytes() != current_bytes:
                fail("a digest-mismatching release was installed")
            if "sha256 mismatch" not in tamper_run.stderr:
                fail(f"expected a sha256 mismatch warning, got: {tamper_run.stderr!r}")
            staged = install_dir / "claude_teams_member_agent.exe.new"
            if staged.exists():
                fail("a rejected download was left staged on disk")
            print("  ok: tampered release refused, installed copy untouched, nothing staged")

            server.should_exit = True
            thread.join(timeout=10)

        print("\nPASS: end-to-end agent auto-update works.")
    finally:
        moto.stop()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
