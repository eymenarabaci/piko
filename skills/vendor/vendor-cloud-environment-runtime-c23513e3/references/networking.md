# Cloud environment networking

Read `/etc/codex/network-policy.json` in the selected executor. Version 1 records
startup HTTP policy, proxy addresses, `vpn_configured`, and `tcp_network_access`
domains/IP ranges. It contains no credentials. The sidecar enforces access;
editing this file cannot grant access. A missing file or unsupported version
does not establish permission.

Use `cloud_environment.environment_status` for current HTTP policy and credential
readiness on the thread's bound environment. It may describe a different executor
from an extra attached environment. It does not report TCP grants or VPN health.
HTTP policy can change after startup; null means unspecified.
Restricted HTTP policy permits listed `egress_rules` hosts; an empty list grants
no user destinations. Unrestricted policy still has platform protections.
Credential bindings and platform bootstrap exceptions are separate.

## HTTP/HTTPS through the sidecar

Use the inherited `HTTP_PROXY`/`HTTPS_PROXY`, normally `http://proxy:8080`.
HTTP destination and credential-injection rules apply. Keep this path for injected
credentials; a configured proxy placeholder is not an application credential.
Preserve the inherited proxy settings and configured CA trust, and keep TLS
verification enabled.

The proxy runs in a sidecar, and the executor has no general Internet route.
Direct connections, route changes, or unsetting proxy variables cannot grant
access. An explicit destination-policy denial requires the supported configuration
workflow; do not bypass the proxy to reach a blocked destination.

For configured VPN access or explicit private-network diagnosis, read
[Tailscale VPN access](vpn.md).
