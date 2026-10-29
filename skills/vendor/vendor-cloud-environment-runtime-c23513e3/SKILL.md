---
name: cloud-environment-runtime
description: Read at the start of work in a managed cloud environment. Inspect network policy and configured credentials; build and run Docker containers with the session proxy and CA trust; use Tailscale VPN access, configure TCP forwarding, and diagnose access failures.
---

Read this skill at the start of work in a managed cloud environment, including
resumed work. Before building or running Docker containers, read
[Docker in Docker](references/docker.md) and follow its proxy and CA guidance.

Use `cloud_environment.environment_status` at the start of work in a managed cloud
environment and when diagnosing network or authentication failures. It takes no
arguments. The runtime selects the instance and checks access as the task's actor.
The result describes the thread's managed instance; it does not describe extra
attached environments or the latest editable configuration draft.

Before network work, read [Cloud environment networking](references/networking.md)
and inspect the selected executor's `/etc/codex/network-policy.json`. If the
supported policy snapshot reports `vpn_configured: true`, also read
[Tailscale VPN access](references/vpn.md). The flag indicates configuration,
not VPN health. Also read the VPN reference when explicitly diagnosing VPN access,
even if the flag is missing or false. `environment_status` reports HTTP policy
and credential readiness, not TCP grants or VPN health.

Use the returned network policy, secret bindings, runtime variable names, and
outbound identity aliases to plan commands. Do not print credential values, dump
the process environment, or inspect secret files to discover their contents.
A configured variable may hold a proxy placeholder; that alone does not mean its
credential is missing. Use the configured SDK or CLI normally.

Treat desired configuration and observed readiness separately. Require
`observations_current: true` and the individual state `ready` before treating a
secret, runtime variable, or outbound identity as ready. Network policy is applied
only when its current state is `enforced`. Missing or any other states, including
`skipped` and `unsupported`, do not establish readiness. Recheck after setup
completes. Ready observations describe setup, not guaranteed authorization for
every remote API call. Diagnose the actual command's error too.

For CLI or SDK profile selection, read the worker's non-secret JSON manifest at
`OIC_MANIFEST_PATH`, if present. Version 1 contains a `connections` list. Select
the entry whose `alias` matches the identity needed for the task; `is_default`
identifies the provider's default, not necessarily the intended identity. Use the
selected entry's fields to set these environment variables for that command only:

| `provider_kind` | Environment variable ← manifest field |
| --- | --- |
| `azure` | `IDENTITY_ENDPOINT` ← `identity_endpoint`; `AZURE_CONFIG_DIR` ← `config_dir` |
| `aws` | `AWS_PROFILE` ← `alias`; `AWS_CONFIG_FILE` ← `config_file` |
| `gcp` | `CLOUDSDK_CONFIG` ← `config_dir`; `CLOUDSDK_ACTIVE_CONFIG_NAME` ← `configuration_name`; `GOOGLE_APPLICATION_CREDENTIALS` ← `credentials_file` |

Preserve the inherited runtime authentication headers and guard variables. Use
credential file paths as selectors without reading or printing their contents.
Do not invent aliases, replace injected credentials, or start interactive login
merely because a token is not visible. Inspect the entry's `setup` outcome when
diagnosing CLI failures; a warning does not establish successful CLI setup. If
the manifest version, required entry, or selector fields are unavailable or
unsupported, report the missing setup instead of silently using another identity.

This plugin reads runtime context. Configuration changes require the environment
configuration workflow and its user review. If the tool reports no attached
managed environment, do not reuse an instance ID or readiness result from a prior
turn.
