#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'cloud-environment socat: %s\n' "$*" >&2
  exit 1
}

case "$(uname -s):$(uname -m)" in
  Linux:x86_64) ;;
  *) fail "The bundled socat runtime requires Linux x86_64; use a maintained socat installation for this platform." ;;
esac

umask 077
cache_base="${XDG_CACHE_HOME:-${HOME}/.cache}/cloud-environment"
mkdir -p -- "$cache_base"
cache_base="$(cd -- "$cache_base" && pwd -P)"
install_root="$cache_base/socat-1.8.0.3-1+deb13u1-libwrap0-7.6.q-36-amd64"

check_runtime() {
  local features
  if ! features="$("$1" -V)"; then
    fail "The bundled socat cannot run. It requires glibc 2.38 or newer, libssl.so.3, and libcrypto.so.3; use a maintained system socat on other images."
  fi
  [[ "$features" == *'#define WITH_PROXY 1'* && "$features" == *'#define WITH_TCP 1'* ]] ||
    fail "The socat runtime does not support TCP and HTTP CONNECT."
}

if [[ -d "$install_root" ]]; then
  check_runtime "$install_root/socat"
  printf '%s\n' "$install_root/socat"
  exit 0
fi

for command in curl sha256sum dpkg-deb mktemp; do
  command -v "$command" >/dev/null || fail "Required command is unavailable: $command"
done

staging="$(mktemp -d "$cache_base/.socat-XXXXXX")"
trap 'rm -rf -- "$staging"' EXIT

download() {
  local name="$1" url="$2" sha256="$3"
  if ! curl --fail --location --silent --show-error --retry 2 \
    --connect-timeout 10 --max-time 60 --proto '=https' --proto-redir '=https' \
    --output "$staging/$name.deb" "$url"; then
    fail "Could not download $name. The environment HTTP policy must permit deb.debian.org."
  fi
  if ! (cd -- "$staging" && printf '%s  %s.deb\n' "$sha256" "$name" | sha256sum --check --status); then
    fail "Checksum verification failed for $name."
  fi
  dpkg-deb --extract "$staging/$name.deb" "$staging/runtime"
}

# These pins include the Debian security fix absent from the base image's APT snapshot.
download socat \
  'https://deb.debian.org/debian/pool/main/s/socat/socat_1.8.0.3-1+deb13u1_amd64.deb' \
  'c87c1e6eccc6d5828138dda794d28a7f283fe4e4010074fffbe4dc8354eef08d'
download libwrap0 \
  'https://deb.debian.org/debian/pool/main/t/tcp-wrappers/libwrap0_7.6.q-36_amd64.deb' \
  'cde12afa15d6b1556c5e0564d22edf3b99e6b8fa94c59ccd8b8eebbb62dc19ec'

cat > "$staging/runtime/socat" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail
runtime="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
export LD_LIBRARY_PATH="$runtime/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$runtime/usr/bin/socat1" "$@"
WRAPPER
chmod 0700 "$staging/runtime" "$staging/runtime/socat"
check_runtime "$staging/runtime/socat"

# Rename within the cache filesystem so concurrent callers only see complete runtimes.
if ! mv -T -- "$staging/runtime" "$install_root" 2>/dev/null; then
  [[ -d "$install_root" ]] || fail "Could not publish the socat runtime in $cache_base."
  check_runtime "$install_root/socat"
fi
printf '%s\n' "$install_root/socat"
