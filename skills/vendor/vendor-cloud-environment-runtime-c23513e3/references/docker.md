# Docker in a managed cloud environment

This environment is a Docker container and supports Docker in Docker. The
platform starts the Docker daemon and configures Docker's default proxy settings.

When Docker is needed, check the managed daemon explicitly. Clear endpoint,
context, and TLS selectors for this command so inherited settings or a saved
`currentContext` cannot redirect the check to another daemon:

```sh
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS \
    -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
    docker --host=unix:///var/run/docker.sock info
```

Keep `DOCKER_CONFIG` and registry credentials unchanged. If this local-socket
check fails, check whether `dockerd` is running and check your user ID with
`id -u`. Starting or restarting the daemon requires root (UID 0). A non-root
task user has socket access but no permission
to start or restart the daemon; no privileged startup helper is provided. In
that case, report the diagnostic and request environment recovery.

As root, you may start a stopped daemon using the inherited `HTTP_PROXY`,
`HTTPS_PROXY`, and `NO_PROXY` environment variables and the configured system CA
trust. Docker's client proxy defaults alone do not configure daemon egress.
Keep the existing storage driver, data directory, and socket group. For the
provided daemon defaults, the following command refuses to start a second daemon
and removes the stale pidfile only after confirming no `dockerd` process is
running:

```sh
(
set -e
if pgrep -x dockerd >/dev/null; then
    echo 'dockerd is already running; inspect it before restarting.' >&2
    exit 1
else
    # pgrep returns 1 for no matches; other errors must not trigger recovery.
    [ "$?" -eq 1 ] || exit 1
fi
install -d -m 0700 /run/codex-docker
rm -f /run/codex-docker/dockerd.pid
docker_socket_gid="$(stat -c %g /var/run/docker.sock 2>/dev/null || id -g)"
nohup /usr/local/bin/dind /usr/local/bin/dockerd \
    --host=unix:///var/run/docker.sock \
    --pidfile=/run/codex-docker/dockerd.pid \
    --storage-driver="${CAAS_DIND_STORAGE_DRIVER:-vfs}" \
    --group="$docker_socket_gid" \
    >/run/codex-docker/dockerd.log 2>&1 </dev/null &
)
```

Repeat the local-socket health check above until it succeeds before Docker work;
inspect the daemon log if it does not become ready within 60 seconds. To restart
an unhealthy daemon as root, inspect its log and existing arguments, stop that
specific process, wait for it to exit, and start it with the same settings and
proxy environment.
Restarting interrupts running containers. Do not launch a competing daemon or
change storage drivers to recover it.

## Builds that use the network

Internet access goes through a proxy. Docker's client defaults
supply the proxy to builds and new containers. Inner containers do not inherit
the outer container's certificate stores or its `proxy` hosts entry. Preserve
the platform's reachable proxy address and existing Docker configuration,
including registry credentials.

If a tool in a Dockerfile needs HTTPS access, mount the provided CA during the
build and configure that tool's trust settings. Keep TLS verification enabled.
For Node/npm, adapt the relevant dependency-installing step:

```dockerfile
# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=secret,id=proxy_ca,required=true \
    NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca \
    npm ci --strict-ssl=true
COPY . .
```

Pass the certificate in the build command:

```sh
docker build --secret id=proxy_ca,src="$CODEX_PROXY_CERT" -t app .
```

The mount exists only for that `RUN`. Repeat it in every networked step and
every stage that needs it. The CA is public, but the BuildKit secret mount keeps
this session-specific file out of image layers. Do not `COPY` it into the image.

There is no universal CA environment variable. For Python Requests use
`REQUESTS_CA_BUNDLE`, for pip use `PIP_CERT`, and for uv or clients that honor it
use `SSL_CERT_FILE`. These settings may replace the normal roots instead of
extending them. Mount the outer system bundle from
`/etc/ssl/certs/ca-certificates.crt` as a separate build secret when a combined
bundle is appropriate. Set the inner tool's variable to its mounted path.
For Java, Gradle, Bazel, or OS-managed trust, adapt that tool's trust store and
ensure session certificates and generated stores do not remain in image layers.
Do not fix a certificate error with `--insecure`, `strict-ssl=false`, or by
disabling certificate verification.

Buildx and Compose builds need the same secret supplied through their build
configuration. Proxy defaults do not supply the CA automatically.

## Running containers

New containers receive Docker's default proxy environment. Give a process that
needs HTTPS a read-only CA mount and its appropriate trust setting. For example:

```sh
docker run --rm \
    --mount "type=bind,src=$CODEX_PROXY_CERT,dst=/run/proxy-ca.pem,readonly" \
    -e NODE_EXTRA_CA_CERTS=/run/proxy-ca.pem \
    node:22-bookworm-slim \
    node -e 'fetch("https://registry.npmjs.org").then(r => { if (!r.ok) process.exit(1); })'
```

Use a combined CA bundle for clients whose setting replaces default roots.
Existing containers retain the environment they were created with; recreate
them if their saved proxy address belongs to a prior session.

## Diagnose the failing layer

- The local-socket health check fails: inspect the daemon/socket setup before
  changing a Dockerfile.
- Pull fails: inspect registry authentication, the configured network policy,
  and the exact registry error. Proxy build arguments do not configure daemon
  egress. Registry rate limits may require a registry account.
- A build step cannot connect: check the inherited proxy and destination policy.
  Do not replace the configured proxy address with the outer-only `proxy` hostname.
- A build or runtime process reports a certificate error: supply the CA to that
  particular step/process, including later build stages, then retry with TLS
  verification enabled.

Network access remains subject to the environment's configured policy. A blocked
destination requires an approved configuration change. Do not bypass the proxy
or use host networking to work around policy. Do not print Docker config,
registry credentials, or the full process environment while diagnosing failures.
