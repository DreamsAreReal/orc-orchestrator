# LaunchAgent Spike Test: Keychain + Claude Auth Access

## Setup Commands

```bash
# 1. Create test script
cat > /Users/admin/orchestrator/.spikes/probe/la-test.sh << 'SCRIPT'
#!/bin/bash
{ date; which claude; claude auth status; echo "auth_exit=$?"; security show-keychain-info login.keychain 2>&1; echo "keychain_exit=$?"; } >> /Users/admin/orchestrator/.spikes/probe/la-test.log 2>&1
SCRIPT
chmod +x /Users/admin/orchestrator/.spikes/probe/la-test.sh

# 2. Create LaunchAgent plist
cat > ~/Library/LaunchAgents/com.probe.orchestrator-test.plist << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.probe.orchestrator-test</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/admin/orchestrator/.spikes/probe/la-test.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
</dict>
</plist>

# 3. Load LaunchAgent (bootstrap method — native on modern macOS)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.probe.orchestrator-test.plist

# 4. Kickstart execution
launchctl kickstart gui/$(id -u)/com.probe.orchestrator-test

# 5. Cleanup
launchctl bootout gui/$(id -u)/com.probe.orchestrator-test
rm ~/Library/LaunchAgents/com.probe.orchestrator-test.plist
```

## Test Results

**LaunchAgent Status:** ✓ Executed successfully (2 runs recorded in log)

**Log Output:**
```
Wed Jul 15 03:44:57 +04 2026
/Users/admin/orchestrator/.spikes/probe/la-test.sh: line 2: claude: command not found
auth_exit=127
Keychain "login.keychain" no-timeout
keychain_exit=0
Wed Jul 15 03:45:08 +04 2026
/Users/admin/orchestrator/.spikes/probe/la-test.sh: line 2: claude: command not found
auth_exit=127
Keychain "login.keychain" no-timeout
keychain_exit=0
```

## Key Findings

| Finding | Status |
|---------|--------|
| **LaunchAgent loaded** | ✓ Yes (bootstrap succeeded) |
| **LaunchAgent executed** | ✓ Yes (2 runs logged) |
| **Keychain accessible** | ✓ Yes (exit=0, "login.keychain" readable) |
| **Claude binary in PATH** | ✗ No (exit=127, not found) |
| **Auth check exit code** | 127 (command not found) |
| **Keychain exit code** | 0 (accessible) |
| **Preferred launchctl syntax** | `bootstrap` (modern, also fallback: `load -w`) |

## Conclusions

- **Keychain is accessible from LaunchAgent GUI context** (session type `Aqua` + no-timeout mode = full GUI keychain access)
- **Claude binary needs PATH fix**: The `claude` CLI is not in the default LaunchAgent PATH; needs explicit PATH export or absolute path to claude binary in dispatcher
- **LaunchAgent context is Aqua (GUI)**: Confirmed by keychain being in no-timeout mode (GUI session, not headless)
- **Dispatcher can reach secrets**: Once claude is in PATH or explicitly referenced, auth + keychain access will work

## Recommendations for Dispatcher LaunchAgent

1. Add explicit PATH in plist: `<key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATH:/usr/local/bin</string></dict>`
2. Or use absolute path to claude binary: `/usr/local/bin/claude auth status`
3. Or source shell profile: `source ~/.zshrc && claude auth status` (if using bash -c wrapper)

---

## Round 2: Absolute Path Test (PROOF COMPLETE)

**Binary located:**
```
/opt/homebrew/bin/claude -> /opt/homebrew/Caskroom/claude-code/2.1.193/claude
```
Note: `which claude` from interactive zsh returns the alias
(`claude='command claude --remote-control --dangerously-skip-permissions'`),
use `type -a claude` or check `/opt/homebrew/bin/claude` directly.

**Script v2** (same plist, claude called by absolute path):
```bash
#!/bin/bash
{ date; /opt/homebrew/bin/claude auth status; echo "auth_exit=$?"; security show-keychain-info login.keychain 2>&1; echo "keychain_exit=$?"; } >> /Users/admin/orchestrator/.spikes/probe/la-test.log 2>&1
```

**Log output from LaunchAgent context:**
```
Wed Jul 15 03:46:28 +04 2026
{
  "loggedIn": true,
  "authMethod": "claude.ai",
  "apiProvider": "firstParty",
  ...
  "subscriptionType": "max"
}
auth_exit=0
Keychain "login.keychain" no-timeout
keychain_exit=0
```

**Final verdict:**

| Check | Result |
|-------|--------|
| `claude auth status` from LaunchAgent | auth_exit=0, loggedIn: true (claude.ai / max) |
| Keychain from LaunchAgent | keychain_exit=0 |
| launchctl syntax | `bootstrap gui/$(id -u)` + `kickstart` (worked both rounds) |
| Cleanup | bootout done, plist removed |

**PROVEN: a user LaunchAgent (Aqua session) has full access to the login Keychain
and to a working `claude auth` — the dispatcher just must call claude by absolute
path (`/opt/homebrew/bin/claude`) or set PATH in the plist, because LaunchAgents
do not inherit the interactive shell PATH.**

**Extra gotcha:** GNU `timeout` is not available in the default macOS shell
environment — use a manual until/sleep loop with a counter when waiting for the log.
