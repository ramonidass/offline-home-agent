# Remote access: WireGuard only

The server isn't reachable from the internet at all. My router runs a WireGuard server; the phone
and laptop each have their own peer. Over the tunnel they can reach exactly three ports on the
server: 22 (SSH), 8090 (ntfy) and 8088 (the page). Nothing on the LAN can reach them.

Examples below use `10.0.0.0/24` for the WireGuard subnet and `192.168.8.20` for the server.

## Router (OpenWrt / LuCI)

- **Interface** `wg0`: WireGuard, listen port 51820, address `10.0.0.1/24`, firewall zone `vpn`.
- **One peer per device**, each with its own key pair and a `/32` address (`10.0.0.2/32`,
  `10.0.0.3/32`, ...), so a lost device is one peer to delete.
- **Zone `vpn`**: input reject, forward reject, masquerading off (the server should see `10.0.0.x`,
  not the router's address).
- **Traffic rules**: wan → device, UDP 51820, accept; vpn → lan, destination `192.168.8.20`,
  TCP `22 8090 8088`, accept. Nothing else from `vpn`.
- A dynamic DNS name for the endpoint if your public address changes.

On the client, set Allowed IPs to just what it needs: `10.0.0.0/24, 192.168.8.20/32`.

Android runs one VPN at a time. If you also use a commercial VPN, either switch between them or run
the commercial one on the router and send the WireGuard peers' internet traffic through it.

## Server firewall (firewalld)

```bash
Z=$(firewall-cmd --get-default-zone)
sudo firewall-cmd --permanent --zone=$Z --remove-service=ssh
sudo firewall-cmd --permanent --zone=$Z --remove-service=mdns
for port in 22 8090 8088; do
  sudo firewall-cmd --permanent --zone=$Z --add-rich-rule="rule family=ipv4 source address=10.0.0.0/24 port port=$port protocol=tcp accept"
done
sudo firewall-cmd --reload
```

Check that the LAN interface is in that zone (`firewall-cmd --get-active-zones`), and that nothing
is in `trusted`, which accepts everything.

## SSH

Keys only, no root, one user, and only from WireGuard addresses. The `AllowUsers` line is a
second lock that holds even if a firewall rule is wrong.

```bash
sudo tee /etc/ssh/sshd_config.d/10-hardening.conf >/dev/null <<EOF
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
AllowUsers $USER@10.0.0.*
GSSAPIAuthentication no
X11Forwarding no
MaxAuthTries 3
LoginGraceTime 20
EOF
sudo sshd -t && sudo systemctl enable --now sshd && sudo systemctl reload sshd
```

Add each device's public key to `~/.ssh/authorized_keys` first (ed25519, with a passphrase; a
hardware key with `ed25519-sk` is better still).

## Verify

On the server:

```bash
WG=10.0.0.0/24 IFACE=<lan interface> sh ~/agent-setup/security-check.sh
```

It changes nothing and reports PASS/FAIL for the zones, the rules, the effective `sshd` settings,
key file permissions, and whether Tailscale is still running.

From other devices:

| From | Test | Expected |
|---|---|---|
| Phone on mobile data, WireGuard on | `ssh you@192.168.8.20`, open the page | works |
| Phone on home Wi-Fi, WireGuard off | `ssh -o ConnectTimeout=5 you@192.168.8.20` | times out |
| Laptop on the LAN, no WireGuard | `nmap -Pn -p 22,8088,8090 192.168.8.20` | all `filtered` |
| Outside, no WireGuard | SSH to your public IPv4 and the server's IPv6 | both time out |
| Any | `ssh -o PubkeyAuthentication=no you@...` | `Permission denied` |

## Why not Tailscale

Tailscale worked fine and needs no open port. I moved off it to have one way in instead of two,
with no coordination server involved. If your ISP puts you behind CGNAT, incoming WireGuard won't
work, and Tailscale (with Tailnet Lock, its own SSH server off, and its interface out of the
`trusted` zone) is the better choice.
