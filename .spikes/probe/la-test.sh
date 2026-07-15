#!/bin/bash
{ date; /opt/homebrew/bin/claude auth status; echo "auth_exit=$?"; security show-keychain-info login.keychain 2>&1; echo "keychain_exit=$?"; } >> /Users/admin/orchestrator/.spikes/probe/la-test.log 2>&1
