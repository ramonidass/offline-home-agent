#!/bin/sh
set -eu
export HOME=/root

if [ "${1:-run}" = "init" ]; then
  # One-time setup: a keychain for Bridge (pass + a GPG key with no passphrase), then log in.
  gpg --list-secret-keys 'ProtonMail Bridge' >/dev/null 2>&1 || \
    gpg --batch --passphrase '' --quick-gen-key 'ProtonMail Bridge' default default never
  pass init 'ProtonMail Bridge' >/dev/null
  exec protonmail-bridge --cli
fi

# Bridge only accepts connections from 127.0.0.1, so forward IMAP from the container network.
# SMTP (sending) is deliberately not forwarded: nothing else can send mail as you.
socat TCP-LISTEN:1144,fork,reuseaddr TCP:127.0.0.1:1143 &
exec protonmail-bridge --noninteractive
