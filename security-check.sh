#!/bin/sh
# Read-only check that the server can only be reached over WireGuard.
# Changes nothing. Run: sh ~/agent-setup/security-check.sh   (asks for sudo once)
# Override with e.g.: WG=10.0.0.0/24 IFACE=eth0 sh security-check.sh
WG=${WG:-10.0.0.0/24}            # the WireGuard subnet
IFACE=${IFACE:-wlp4s0}             # the LAN interface
ME=$(id -un)
WG_GLOB=$(echo "$WG" | sed 's|\.[0-9]*/24$|.*|')   # 10.0.0.0/24 -> 10.0.0.* (sshd AllowUsers form)
fails=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fails=$((fails + 1)); }
info() { printf '       %s\n' "$1"; }

sudo -v || exit 1

echo "Firewall zones"
zones=$(firewall-cmd --get-active-zones)
echo "$zones" | sed 's/^/       /'
echo "$zones" | grep -A1 '^public' | grep -qw "$IFACE" && ok "LAN ($IFACE) is in the public zone" || bad "$IFACE is not in the public zone"
echo "$zones" | grep -q '^trusted' && bad "the trusted zone has interfaces (everything open to them)" || ok "nothing in the trusted zone"
echo "$zones" | grep -qw tailscale0 && info "tailscale0 still listed (fine if tailscaled is off)"

echo "Public zone"
svc=$(sudo firewall-cmd --zone=public --list-services)
ports=$(sudo firewall-cmd --zone=public --list-ports)
fwd=$(sudo firewall-cmd --zone=public --list-forward-ports)
echo "$svc" | grep -qw ssh && bad "ssh service open to everyone" || ok "no ssh service (only the WireGuard rule)"
others=$(echo "$svc" | tr ' ' '\n' | grep -vx -e '' -e dhcpv6-client)
[ -z "$others" ] && ok "no other services open" || bad "services open to everyone: $others"
[ -z "$ports" ] && ok "no ports open to everyone" || bad "ports open to everyone: $ports"
[ -z "$fwd" ] && ok "no port forwards" || bad "port forwards: $fwd"
rules=$(sudo firewall-cmd --zone=public --list-rich-rules)
echo "$rules" | sed 's/^/       /'
notwg=$(echo "$rules" | grep 'accept' | grep -v "source address=\"$WG\"")
[ -z "$notwg" ] && ok "every accept rule is for WireGuard ($WG)" || bad "accept rules for other sources:
$notwg"
for p in 22 8088 8090; do
  echo "$rules" | grep "source address=\"$WG\"" | grep -q "port=\"$p\"" && ok "port $p allowed from WireGuard" || bad "no WireGuard rule for port $p"
done
echo "$rules" | grep -q 'port="3000"' && bad "port 3000 still has a rule (Open WebUI is gone)" || ok "no rule for port 3000"

echo "Listening on the network (not localhost)"
listen=$(ss -Htuln | awk '{print $1, $5}' | grep -v -e '127\.0\.0\.' -e '\[::1\]' -e '%lo:' | sort -u)
echo "$listen" | sed 's/^/       /'
info "Expected: tcp 22 (sshd), tcp 8088 and 8090 (caddy.socket), plus DHCP/mDNS-style UDP."
info "Anything else here is only reachable if a firewall rule allows it (checked above)."

echo "SSH server (effective settings)"
cfg=$(sudo sshd -T 2>/dev/null)
chk() { echo "$cfg" | grep -qix "$1" && ok "$1" || bad "want '$1', have '$(echo "$cfg" | grep -i "^${1%% *} " | head -1)'"; }
chk "passwordauthentication no"
chk "kbdinteractiveauthentication no"
chk "permitrootlogin no"
chk "pubkeyauthentication yes"
chk "allowusers $ME@$WG_GLOB"
chk "gssapiauthentication no"
chk "x11forwarding no"
keys=$(grep -c . "$HOME/.ssh/authorized_keys" 2>/dev/null || echo 0)
info "authorized_keys: $keys key(s). Remove any you don't recognise:"
[ -f "$HOME/.ssh/authorized_keys" ] && awk '{print "         " $1, $NF}' "$HOME/.ssh/authorized_keys"
perm=$(stat -c %a "$HOME/.ssh/authorized_keys" 2>/dev/null)
[ "$perm" = 600 ] && ok "authorized_keys permissions 600" || bad "authorized_keys permissions are $perm (want 600)"

echo "Tailscale"
if systemctl is-active -q tailscaled; then bad "tailscaled is running"; else ok "tailscaled is stopped"; fi
if systemctl is-enabled -q tailscaled 2>/dev/null; then bad "tailscaled starts at boot"; else ok "tailscaled won't start at boot"; fi
# tailcat serves SSH to the tailnet itself; it may not need tailscaled, so check it separately
if systemctl --user is-active -q tailcat 2>/dev/null; then bad "tailcat is running (SSH over the tailnet, outside the firewall)"; else ok "tailcat is stopped"; fi
if systemctl --user is-enabled -q tailcat 2>/dev/null; then bad "tailcat starts at login"; else ok "tailcat won't start at login"; fi

echo
[ "$fails" -eq 0 ] && echo "All server checks passed. Now do the tests from your other devices." || echo "$fails check(s) failed."
