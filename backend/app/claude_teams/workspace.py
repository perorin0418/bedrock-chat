"""Per-chat-turn temporary workspace for the Claude Code CLI subprocess.

Bot instructions are written as a `CLAUDE.md` file in a fresh temporary
directory, which the CLI reads automatically as project memory (a
legitimate, documented Claude Code feature — not an impersonation trick).
The directory (and any `CLAUDE_CONFIG_DIR` scoped inside it, wired up by the
caller) is deleted when the chat turn ends, so nothing leaks across a
warm-started Lambda's reused execution environment.
"""

import contextlib
import logging
import os
import shutil
import tempfile

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

CLAUDE_MD_FILENAME = "CLAUDE.md"


@contextlib.contextmanager
def claude_teams_workspace(instructions: list[str]):
    """Yield a fresh temporary directory, containing a `CLAUDE.md` built
    from `instructions` (skipped if `instructions` is empty). The directory
    is always removed on exit, including when the body raises."""
    workspace_dir = tempfile.mkdtemp(prefix="claude-teams-")
    try:
        joined = "\n\n".join(instructions).strip()
        if joined:
            claude_md_path = os.path.join(workspace_dir, CLAUDE_MD_FILENAME)
            with open(claude_md_path, "w", encoding="utf-8") as f:
                f.write(joined)
        yield workspace_dir
    finally:
        shutil.rmtree(workspace_dir, ignore_errors=True)
