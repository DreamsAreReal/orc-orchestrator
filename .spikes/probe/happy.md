# Happy Coder Probe Report

## Installation & Versions
✓ **Installed**: `@kmmao/happy-coder@0.105.0` (fork by kmmao of slopus/happy)
- Global command `happy` works; `happy --version` outputs `0.105.0`
- Postinstall hooks (`node-pty`) required `--allow-scripts` flag but resolved

## Pairing Mechanism
**First pairing is MANDATORY and phone-first**:
- `happy --version` or `happy --help` triggered interactive auth prompt: "1. Mobile App / 2. Web Browser"
- Authentication stored in `~/.happy/settings.json`; master secret stays on mobile/web device, CLI receives only derived per-machine key
- No QR code visible in CLI (auth happens via server at `s.sangreal.code.xycloud.info:2443` by default)
- `happy auth login --force` can re-authenticate if credentials lost

## Offline / Headless Modes
**NO headless or offline-first mode detected**:
- Package description: "Mobile and Web client for Claude Code and Codex" — design assumes connected mobile/web peer
- `happy daemon` exists for background spawning but still requires auth token from paired device
- No flag like `--local-only`, `--no-phone`, or `--offline` in help text
- Architecture: CLI ↔ Server (s.sangreal.code.xycloud.info) ↔ Mobile/Web App; server is mandatory for all cross-device comms

## Doctor Diagnostics
`happy doctor` output:
- Platform: darwin arm64, Node 26.4.0
- Auth status: **Not authenticated (no credentials)**
- Daemon: Not running (requires prior auth + daemon start)
- Config: `/Users/admin/.happy/settings.json` (empty profiles, onboardingCompleted=false)
- Server URL: `https://s.sangreal.code.xycloud.info:2443` (hardcoded, can override via `HAPPY_SERVER_URL` env var)

## Loop/Circuit-Breaker Risks
**Medium-to-high for notification layer**:
1. **Pairing blocker**: First `happy` run will hang on auth prompt, requiring interactive mobile/web auth — breaks unattended loops
2. **Server dependency**: All operations require live connection to external server (s.sangreal.code.xycloud.info); if server down, CLI fails even if authenticated
3. **Session takeover**: No explicit mention in help of what happens when phone initiates takeover; likely kills current CLI session (restarts or drops context)
4. **Daemon is persistent but not headless**: `happy daemon` exists for spawning background sessions, but each still needs upfront auth; no fallback to claude if happy unavailable

## Conclusion
**happy is a mobile-first control layer, NOT a drop-in claude replacement for headless flows**. Viable for interactive loop (push notification on gate) but requires:
- Pre-pairing on first setup (interactive auth via phone/web)
- Persistent server connectivity
- Graceful degradation fallback to standard claude if happy unavailable/errored

**For notification layer**: wrap happy in supervisor logic that detects auth state & server health; if offline/unauthenticated, degrade to stdout gate or other signaling.
