# Tailscale VPN access

Use this reference when the selected executor's supported policy snapshot reports
`vpn_configured: true`, or when explicitly diagnosing VPN access. Read
[Cloud environment networking](networking.md) for the policy snapshot and HTTP
policy. The snapshot's VPN setting and CONNECT address do not prove readiness or
an enabled listener; `environment_status` does not report VPN health or TCP grants.

## Choose the connection path

Tailscale runs in the sidecar. The executor has no VPN interface or general
Internet route. Direct connections, route changes, or unsetting proxy variables
cannot enable VPN access. Sidecar controls, VPN credentials, and logs are unavailable.
Do not install or reconfigure Tailscale in the executor to repair access.

| Use | Endpoint | Policy and authentication |
| --- | --- | --- |
| HTTP/HTTPS | Inherited `HTTP_PROXY`/`HTTPS_PROXY`, normally `http://proxy:8080` | HTTP destination and credential-injection rules apply, including for permitted private IPs. Keep this path for injected credentials. |
| Raw TCP | HTTP CONNECT at `proxy:8088` | VPN plus a TCP grant. Opaque bytes, no TLS interception or credential injection. The application supplies authentication; proxy credential placeholders will not work. |

## Access and recovery

1. Inspect the selected executor's policy snapshot and current environment status.
   If VPN configuration is missing or false, report that setup gap; do not treat
   it as a working VPN or change the snapshot to enable access.
2. For HTTP/HTTPS, make a bounded request to the actual authorized service using
   the inherited proxy settings, for example `curl -i --max-time 20 <service-url>`.
   Keep TLS verification enabled. A successful request to another host does not
   prove that this service or its Tailnet hostname is reachable.
3. If an otherwise authorized request fails with a sandbox or proxy-connectivity
   error, retry the same bounded request using the execution tool's normal
   approval flow (`sandbox_permissions: "require_escalated"` where supported).
   Preserve the inherited proxy settings and CA trust. Do not unset proxy
   variables or use `--noproxy` for the remote VPN destination. If escalation is
   unavailable or denied, report the limitation. An explicit destination-policy
   denial requires the appropriate policy change, not an escalation retry.
4. Compare the status and sanitized error from each attempt. If an integrated
   terminal request succeeds while an agent request fails, or only the escalated
   request succeeds, that indicates different execution paths. Do not infer
   an account or rollout problem from "proxy unreachable" alone.
5. If access still fails, distinguish DNS resolution, policy rejection, sidecar
   readiness, and service/protocol errors using the guidance below. Report the
   destination, port, execution path, and sanitized error. Request operator
   diagnostics when the failing layer is outside the executor.

For raw TCP, use the authorized forwarding workflow below. The listener has its
own grants; it is not a workaround for a denied HTTP destination.

## DNS and service identity

Sidecar DNS resolves CONNECT names. MagicDNS/split tailnet DNS is not configured
(`accept_dns=false`). Use a resolvable authorized hostname or granted literal IPv4.
Executor DNS failure alone does not disprove hostname CONNECT.

If a Tailnet hostname fails while its known, authorized IP works, diagnose DNS
separately. Use the IP only where the protocol and destination policy permit it;
for raw TCP, a literal IPv4 needs a matching CIDR grant. Preserve the service's
TLS hostname/SNI and certificate verification when changing the transport
address, as in the forwarding examples below. Do not disable verification to
make an IP-based HTTPS request succeed.

## TCP grants

TCP grants apply only to 8088; HTTP rules do not authorize that listener.
A literal IPv4 address needs a matching CIDR. A hostname needs an exact
domain or `*.` wildcard match; wildcards exclude the apex. Domain grants need no
additional CIDR grant. Current grants cover all TCP ports. Resolved IPv4 addresses
must be within 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, or 100.64.0.0/10; platform-protected
destinations remain blocked. Changed TCP grants require a replacement environment
using the new configuration version.

## Install the forwarding tool at runtime

Use the bundled [socat installer](../scripts/ensure-socat.sh) before creating a
forward. Set `RUNTIME_SKILL_DIR` to the absolute directory containing this skill's
`SKILL.md`, then capture the executable path it prints:

```sh
SOCAT="$(bash "$RUNTIME_SKILL_DIR/scripts/ensure-socat.sh")"
```

If the skill is available only through resource reads, read the linked installer,
save it in the selected executor, and invoke `bash` on that saved path instead.

The installer downloads pinned, checksum-verified socat and libwrap packages into
`${XDG_CACHE_HOME:-$HOME/.cache}/cloud-environment`. It needs no root access or
system package changes, and reuses the cached runtime without downloading again.
The patched socat version is independent of the executor image's APT snapshot.

This runtime supports Linux x86_64 with glibc 2.38 or newer and OpenSSL 3
(`libssl.so.3` and `libcrypto.so.3`), including `cloud-agent-base`. Installation
requires `bash`, `curl`, `sha256sum`, and `dpkg-deb`. Other images may use their
own maintained socat installation; set `SOCAT` to that executable's absolute path.

First-time downloads use the inherited HTTP proxy and require permitted HTTPS
access to `deb.debian.org`. A TCP grant does not authorize these downloads. If
HTTP policy denies them, explicitly suggest temporarily adding the exact host
`deb.debian.org` through the reviewed HTTP configuration flow for HTTPS downloads.
Preserve existing rules and wait for the updated policy to be enforced in the
selected environment. After installation succeeds and rerunning the installer
confirms cache reuse, suggest removing only the rule added for this installation
through the same flow. A replacement environment or cleared cache may need
download access again. An existing maintained socat is also an option. Do not
bypass the proxy or disable TLS verification.

## Forward an authorized endpoint

```sh
"$SOCAT" -d -d TCP4-LISTEN:15432,bind=127.0.0.1,reuseaddr,fork \
  PROXY:proxy:db.example.internal:5432,proxyport=8088,resolve=0
```

Choose a free unprivileged loopback port. Keep the process running while the
client runs; retain its execution session or PID/stderr log and stop it afterward.
`resolve=0` preserves the hostname for sidecar authorization and DNS. No custom
proxy header is needed; standard socat lacks `proxy-header`. A listening port
alone proves nothing: CONNECT starts when the application connects.

Preserve TLS server identity and verification while connecting through loopback:

```sh
psql 'host=db.example.internal hostaddr=127.0.0.1 port=15432 sslmode=verify-full'
```

For HTTPS with remote port 443 forwarded to local 18443:

```sh
curl --noproxy '*' \
  --connect-to service.example.internal:443:127.0.0.1:18443 \
  https://service.example.internal/
```

The original URL preserves Host/SNI/certificate identity. This `--noproxy`
exception applies only to a client connecting through an explicitly established
loopback forward; the forward still uses the authorized sidecar CONNECT path.
Do not use it for a direct request to a remote VPN address. Use normal client
configuration for private service CAs; the HTTP interception CA is not the
service CA. Never disable TLS or SSH host-key verification.

## Limits and diagnosis

- TCP only: no UDP, QUIC, ICMP, inbound connections, transparent routing, or
  automatic cluster discovery. Additional endpoints need authorization and forwards.
- Shell/enterprise restrictions can independently block listeners or `proxy:8088`;
  request access through that policy's supported workflow.
- Check `"$SOCAT" -V`, `getent hosts proxy`, and `ss -ltn`. Rerun the installer
  to recover the cached executable path. If a previously working cache is damaged,
  remove only its versioned socat directory and rerun the installer.
- Make a real client connection. A proxy 403 means an invalid CONNECT request
  or rejected policy/destination;
  `vpn_no_ipv4` means no IPv4 answer; `vpn_protected_destination` rejects protected
  or nonprivate addresses. If no grant matches, request a reviewed configuration change.
  503 `vpn_not_ready` or `vpn_policy_unavailable` needs operator
  investigation. Report destination, port, and sanitized status/stderr.
- Successful CONNECT proves only TCP transport. Then check protocol, TLS/CA,
  and application authentication. Timeouts can involve VPN ACLs, subnet-route
  approval, firewalls, or service listeners; ask the operator for sidecar diagnostics.
  Recreate forwards after environment restart; reconnect clients after VPN loss.
