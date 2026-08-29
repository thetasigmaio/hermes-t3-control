#!/usr/bin/env bash
set -eu
umask 077

if [ "$#" -ne 0 ]; then
  printf '%s\n' 'This installer does not accept arguments.' >&2
  exit 2
fi

hermes config path
read -r -p 'Install into this Hermes profile? [y/N] ' CONFIRM
case "$CONFIRM" in y|Y) ;; *) exit 1 ;; esac

VERIFY_DIR="$(mktemp -d)"
trap 'rm -rf -- "$VERIFY_DIR"' EXIT
mkdir -m 700 "$VERIFY_DIR/home" "$VERIFY_DIR/xdg" "$VERIFY_DIR/repo"

safe_git() {
  env -i PATH="$PATH" LC_ALL=C HOME="$VERIFY_DIR/home" XDG_CONFIG_HOME="$VERIFY_DIR/xdg" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git "$@"
}

safe_git -C "$VERIFY_DIR/repo" init -q
safe_git -C "$VERIFY_DIR/repo" -c protocol.file.allow=never fetch -q --no-tags https://github.com/thetasigmaio/hermes-t3-control.git 'refs/tags/v1.2.3:refs/tags/v1.2.3'
TAG_VERIFY="$(safe_git -C "$VERIFY_DIR/repo" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile=/dev/null verify-tag --raw v1.2.3 2>&1 || true)"
if ! printf '%s\n' "$TAG_VERIFY" | grep -Fqx 'Good "git" signature with ED25519 key SHA256:w7wKQukCKTYbelHXBB3necJ6DkvZ9l01ehw83L5r4T4'; then
  printf '%s\n' 'Release tag signature did not match the pinned signer.' >&2
  exit 1
fi

HERMES_T3_CONTROL_REF="$(safe_git -C "$VERIFY_DIR/repo" rev-parse --verify 'v1.2.3^{}')"
printf '%s\n' "$HERMES_T3_CONTROL_REF" | grep -Eq '^[0-9a-f]{40}$'

hermes config set plugins.scan_on_install true
hermes plugins install thetasigmaio/hermes-t3-control --ref "$HERMES_T3_CONTROL_REF" --no-enable
hermes plugins doctor hermes-t3-control --ci
hermes plugins enable hermes-t3-control --no-allow-tool-override
