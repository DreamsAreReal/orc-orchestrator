"""Thin wrapper around the `bd` (beads) queue CLI.

orc keeps its task queue in beads. Each orc task is a bd issue whose `metadata`
carries the orc-specific fields: {"project": <abs path>, "slug": <str>, "text": <str>}.
Gate tasks additionally carry {"gate": true} and a gate card block.

python 3.9-compatible: no match, no 3.10+ typing.
"""
import os
import json
import shutil
import subprocess


BD = shutil.which("bd") or "/opt/homebrew/bin/bd"


class BeadsError(Exception):
    pass


def bd_available():
    return shutil.which("bd") is not None or os.path.exists(BD)


def _run(args, cwd=None, input_text=None):
    """Run `bd <args>` and return (returncode, stdout, stderr)."""
    try:
        p = subprocess.run(
            [BD] + list(args),
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise BeadsError("bd not found")
    return p.returncode, p.stdout, p.stderr


def init(hub):
    """Initialize a beads queue in `hub` if not already present. Idempotent."""
    beads_dir = os.path.join(hub, ".beads")
    if os.path.isdir(beads_dir):
        return False
    os.makedirs(hub, exist_ok=True)
    rc, out, err = _run(["init"], cwd=hub)
    if rc != 0 and not os.path.isdir(beads_dir):
        raise BeadsError("bd init failed: " + (err or out))
    return True


def create(hub, title, priority=2, labels=None, metadata=None):
    """Create a task, set its metadata, return the bd issue id."""
    args = ["q", title, "-p", str(priority)]
    for lbl in (labels or []):
        args += ["-l", lbl]
    rc, out, err = _run(args, cwd=hub)
    if rc != 0:
        raise BeadsError("bd create failed: " + (err or out))
    issue_id = out.strip().splitlines()[-1].strip() if out.strip() else ""
    if not issue_id:
        raise BeadsError("bd create returned no id")
    if metadata is not None:
        rc2, out2, err2 = _run(
            ["update", issue_id, "--metadata", json.dumps(metadata)], cwd=hub
        )
        if rc2 != 0:
            raise BeadsError("bd metadata failed: " + (err2 or out2))
    return issue_id


def ready(hub):
    """Return the ready task list (dicts with id/title/priority/labels/metadata)."""
    rc, out, err = _run(["ready", "--json", "-n", "0"], cwd=hub)
    if rc != 0:
        raise BeadsError("bd ready failed: " + (err or out))
    try:
        data = json.loads(out) if out.strip() else []
    except ValueError:
        raise BeadsError("bd ready returned non-JSON: " + out[:200])
    if isinstance(data, dict):
        data = data.get("issues") or data.get("ready") or []
    return data


def show(hub, issue_id):
    """Return a single task dict or None."""
    rc, out, err = _run(["show", issue_id, "--json"], cwd=hub)
    if rc != 0:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    if isinstance(data, list):
        return data[0] if data else None
    return data


def claim(hub, issue_id):
    """Atomically claim (assignee + in_progress). Idempotent per bd semantics."""
    rc, out, err = _run(["update", issue_id, "--claim"], cwd=hub)
    if rc != 0:
        raise BeadsError("bd claim failed: " + (err or out))
    return True


def close(hub, issue_id):
    rc, out, err = _run(["close", issue_id], cwd=hub)
    if rc != 0:
        raise BeadsError("bd close failed: " + (err or out))
    return True


def set_status(hub, issue_id, status):
    rc, out, err = _run(["update", issue_id, "--status", status], cwd=hub)
    if rc != 0:
        raise BeadsError("bd set-status failed: " + (err or out))
    return True


def reopen(hub, issue_id):
    """Return a task to the ready pool (used by lease/kill-switch)."""
    rc, out, err = _run(["reopen", issue_id], cwd=hub)
    if rc != 0:
        # already open is fine
        return False
    return True


def task_meta(task):
    """Extract orc metadata dict from a bd task dict (never None)."""
    meta = task.get("metadata")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    return meta or {}
