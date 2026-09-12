"""S3-backed release channel for claude_teams_member_agent.exe.

An admin builds the .exe (see scripts/claude_teams_member_agent_go/BUILD.md)
and uploads it, plus a small `manifest.json`, to the release bucket
provisioned in cdk/lib/bedrock-chat-stack.ts
(`ClaudeTeamsAgentReleaseBucket`). Members' installed copies then poll
`GET /claude-teams-tokens/{token_id}/agent-version` on every scheduled run
and self-update when the manifest names a version other than their own (see
`updater.go` in the Go agent).

Why the bucket is never public, and why the download goes through a
short-lived presigned URL minted by that ingest_secret-authenticated route
rather than a plain object URL: the .exe carries the org-wide Registration
Secret baked in at build time (BUILD.md documents this trust model), so
anyone who can download the binary can add tokens to the pool via
`POST /claude-teams-tokens/register`. That exposure already exists for
members who legitimately hold the .exe, but publishing it at a guessable
URL would widen "members the admin handed it to" into "anyone who learns
the URL". Requiring a per-token `ingest_secret` (already on every
registered machine, already used by the usage-snapshot and status routes)
keeps the download restricted to machines that are themselves already in
the pool, without inventing a new credential to distribute or rotate.

The manifest is the single source of truth for "what version should
members be on", deliberately separate from the object itself, so an admin
can stage a new .exe in the bucket and cut over (or roll back) by editing
one small JSON file.
"""

import json
import logging
import os

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.utils import REGION

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Fixed key at the bucket root. Deliberately a constant rather than a
# configurable env var: an admin uploading a release should not have to
# match a deploy-time setting, and the route below has no other way to
# discover which object is the manifest.
MANIFEST_KEY = "manifest.json"

# How long the download URL handed to a member's agent stays valid. Kept
# deliberately short: the URL is fetched and consumed within the same
# scheduled run (seconds apart), so a longer window would only widen the
# period during which a leaked URL is replayable by someone who never
# held an ingest_secret at all. Five minutes leaves ample room for a slow
# corporate proxy without being a durable, shareable link.
DOWNLOAD_URL_EXPIRATION_SECONDS = 300


class AgentReleaseNotConfiguredError(Exception):
    """Raised when no release bucket is configured for this deployment, or
    the bucket holds no manifest yet.

    Not an error condition for the member's agent: it simply means this
    deployment does not publish agent updates (the admin still hands out
    rebuilt .exe files by hand, which keeps working exactly as before),
    so the route maps this to a 404 the agent treats as "nothing to do"
    rather than as a failure worth warning a member about."""


def _bucket_name() -> str:
    return os.environ.get("CLAUDE_TEAMS_AGENT_RELEASE_BUCKET", "")


def _s3_client():
    """An S3 client pinned to the stack's own region (`REGION`), with
    SigV4 explicitly requested.

    Deliberately not `app.utils.generate_presigned_url`, which is
    otherwise the house helper for this: that one signs with
    `BEDROCK_REGION`, because every bucket it serves belongs to the
    Bedrock-side workflow. This bucket is created by the main stack in
    `REGION` instead, and those two are routinely different in this
    project (the whole point of a separate `BEDROCK_REGION` is running
    inference elsewhere). A URL signed for the wrong region fails with
    SignatureDoesNotMatch at download time -- i.e. it would break on
    exactly the cross-region deployments the setting exists for, while
    looking fine on a same-region test.

    SigV4 + path addressing mirror the existing helper: see the boto3
    issue linked there for why they are set explicitly rather than left
    to defaults."""
    return boto3.client(
        "s3",
        region_name=REGION,
        config=Config(signature_version="v4", s3={"addressing_style": "path"}),
    )


def get_agent_release_manifest() -> dict:
    """Return the parsed release manifest from S3.

    Expected shape (extra keys are ignored, so the manifest can grow
    without a backend deploy):

        {
          "version": "2026.09.10-abc1234",
          "sha256": "<hex digest of the .exe>",
          "key": "releases/2026.09.10-abc1234/claude_teams_member_agent.exe"
        }

    Raises AgentReleaseNotConfiguredError when no bucket is configured or
    no manifest has been uploaded yet."""
    bucket = _bucket_name()
    if not bucket:
        raise AgentReleaseNotConfiguredError(
            "CLAUDE_TEAMS_AGENT_RELEASE_BUCKET is not set for this deployment."
        )

    client = _s3_client()
    try:
        response = client.get_object(Bucket=bucket, Key=MANIFEST_KEY)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        # NoSuchKey is the documented code; AWS also returns 404/NotFound
        # for a HeadObject-style miss depending on permissions, and an
        # AccessDenied here (bucket exists but the manifest doesn't, with
        # no s3:ListBucket) is indistinguishable from "not uploaded yet"
        # from the caller's side -- all mean "this deployment has no
        # published release", never "the request was malformed".
        if code in ("NoSuchKey", "NoSuchBucket", "404", "NotFound", "AccessDenied"):
            raise AgentReleaseNotConfiguredError(
                f"No agent release manifest at s3://{bucket}/{MANIFEST_KEY}."
            )
        raise

    raw = response["Body"].read()
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as e:
        # A malformed manifest is an admin mistake, not a member's: fail
        # loudly in logs (so it's visible) but surface it to the agent as
        # "no release configured" so a typo can't wedge every member's
        # scheduled run into an error state.
        logger.error(
            f"Malformed agent release manifest at s3://{bucket}/{MANIFEST_KEY}: {e}"
        )
        raise AgentReleaseNotConfiguredError(
            "Agent release manifest is not valid JSON."
        )

    if not isinstance(manifest, dict):
        raise AgentReleaseNotConfiguredError(
            "Agent release manifest is not a JSON object."
        )
    return manifest


def generate_agent_download_url(key: str) -> str:
    """Mint a short-lived presigned GET URL for the release object at
    `key`. The caller must have already verified the requester's
    ingest_secret (see app/usecases/claude_teams_admin.py)."""
    bucket = _bucket_name()
    if not bucket:
        raise AgentReleaseNotConfiguredError(
            "CLAUDE_TEAMS_AGENT_RELEASE_BUCKET is not set for this deployment."
        )
    return _s3_client().generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=DOWNLOAD_URL_EXPIRATION_SECONDS,
        HttpMethod="GET",
    )
