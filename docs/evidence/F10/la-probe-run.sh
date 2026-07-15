#!/bin/bash
{ date; echo "PATH=$PATH"; "/opt/homebrew/bin/claude" auth status; echo "auth_exit=$?";   security show-keychain-info login.keychain 2>&1; echo "keychain_exit=$?"; }   >> "/Users/admin/orchestrator/docs/evidence/F10/la-probe.log" 2>&1
