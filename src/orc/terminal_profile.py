"""F10: `orc setup` makes husk windows not accumulate for ANY user, reproducibly.

Terminal.app keeps an empty "husk" window after a worker's shell exits UNLESS the
profile's `shellExitAction` is 0 (close the window when the shell exits cleanly). The
user set this by hand for the current profile (Clear Dark -> 0); `orc setup` turns that
into a reproducible step: it edits the Terminal profile plist via plistlib, backing up
the previous value first so the change is reversible.

This is a defensive, backed-up plist edit (not an irreversible external action): the old
value is saved under a private key so `orc setup --revert` (or a human) can restore it.
Terminal.app should be quit before editing so it does not overwrite the plist on exit --
the CLI warns about that; the edit itself is still written to disk.
"""
import os
import plistlib

# Private key where we stash the previous shellExitAction so the change is reversible.
_BACKUP_KEY = "orcPrevShellExitAction"
# 0 = close the window when the shell exits (no husk). 2 = keep window open (the husk cause).
_CLOSE_ON_EXIT = 0


def terminal_plist_path():
    return os.path.expanduser("~/Library/Preferences/com.apple.Terminal.plist")


def _load(path):
    with open(path, "rb") as f:
        return plistlib.load(f)


def _dump(path, data):
    tmp = path + ".orc.tmp"
    with open(tmp, "wb") as f:
        plistlib.dump(data, f)
    os.replace(tmp, path)


def resolve_profile(data, requested=None):
    """Pick which Terminal profile orc should configure.

    Priority: an explicitly requested profile (config `terminal_profile`) -> the machine's
    default profile ("Default Window Settings") -> the startup profile -> None. Only
    returns a name that actually exists in the plist's Window Settings.
    """
    settings = data.get("Window Settings", {})
    for candidate in (requested,
                      data.get("Default Window Settings"),
                      data.get("Startup Window Settings")):
        if candidate and candidate in settings:
            return candidate
    return None


def current_value(data, profile):
    """The profile's current shellExitAction (int), or None if unset."""
    return data.get("Window Settings", {}).get(profile, {}).get("shellExitAction")


def set_close_on_exit(path, profile):
    """Set the profile's shellExitAction to 0 with a backup. Returns a result dict.

    Result: {"changed": bool, "profile": str, "old": <old value or None>}.
    Idempotent: if already 0, reports changed=False and touches nothing.
    """
    data = _load(path)
    settings = data.setdefault("Window Settings", {})
    prof = settings.get(profile)
    if prof is None:
        return {"changed": False, "profile": profile, "old": None, "missing": True}
    old = prof.get("shellExitAction")
    if old == _CLOSE_ON_EXIT:
        return {"changed": False, "profile": profile, "old": old, "missing": False}
    # back up the previous value (only the first time, so a re-run keeps the true original)
    if _BACKUP_KEY not in prof:
        prof[_BACKUP_KEY] = old if old is not None else ""
    prof["shellExitAction"] = _CLOSE_ON_EXIT
    _dump(path, data)
    return {"changed": True, "profile": profile, "old": old, "missing": False}


def revert(path, profile):
    """Restore the profile's shellExitAction from the orc backup. Returns a result dict."""
    data = _load(path)
    prof = data.get("Window Settings", {}).get(profile)
    if prof is None or _BACKUP_KEY not in prof:
        return {"reverted": False, "profile": profile}
    old = prof.pop(_BACKUP_KEY)
    if old == "":
        prof.pop("shellExitAction", None)
    else:
        prof["shellExitAction"] = old
    _dump(path, data)
    return {"reverted": True, "profile": profile, "restored": old}
