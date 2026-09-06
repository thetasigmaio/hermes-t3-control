#!/usr/bin/env bash
set -eu
umask 077

if [ "$#" -ne 0 ]; then
  printf '%s\n' 'This installer does not accept arguments.' >&2
  exit 2
fi

printf '%s\n' 'Hermes T3 Control 1.3.1 installer'
printf '%s\n' 'Active Hermes profile:'
hermes config path
read -r -p 'Install into this Hermes profile? [y/N] ' CONFIRM
case "$CONFIRM" in y|Y) ;; *) exit 1 ;; esac

printf '%s\n' 'Verifying the signed v1.3.1 release...'
VERIFY_DIR="$(mktemp -d)"
PLUGIN_CREATED=0
SCAN_SETTING_CHANGED=0
SCAN_SETTING_ORIGINAL=

cleanup() {
  STATUS=$?
  trap - EXIT
  set +e
  if [ "$STATUS" -ne 0 ]; then
    if [ "$PLUGIN_CREATED" -eq 1 ]; then
      if hermes plugins disable hermes-t3-control >/dev/null 2>&1 \
        && read_plugin_enabled_state \
        && [ "$PLUGIN_ENABLED_STATE" = disabled ]; then
        printf '%s\n' 'Installation did not finish. The new plugin remains installed but disabled; resolve the reported error before retrying.' >&2
      else
        printf '%s\n' 'Installation failed; could not confirm that the new plugin is disabled.' >&2
      fi
    fi
    if [ "$SCAN_SETTING_CHANGED" -eq 1 ]; then
      if ! restore_scan_setting; then
        printf '%s\n' 'Installation failed; could not restore the previous scan setting.' >&2
      fi
    fi
  fi
  rm -rf -- "$VERIFY_DIR"
  exit "$STATUS"
}
trap cleanup EXIT
mkdir -m 700 "$VERIFY_DIR/home" "$VERIFY_DIR/xdg" "$VERIFY_DIR/repo"

safe_git() {
  env -i PATH="$PATH" LC_ALL=C HOME="$VERIFY_DIR/home" XDG_CONFIG_HOME="$VERIFY_DIR/xdg" GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git "$@"
}

read_scan_setting() {
  SCAN_STDOUT="$VERIFY_DIR/scan.stdout"
  SCAN_STDERR="$VERIFY_DIR/scan.stderr"
  if hermes config get plugins.scan_on_install --json >"$SCAN_STDOUT" 2>"$SCAN_STDERR"; then
    SCAN_STATUS=0
  else
    SCAN_STATUS=$?
  fi
  if [ "$SCAN_STATUS" -eq 0 ] && [ ! -s "$SCAN_STDERR" ]; then
    if printf '%s\n' true | cmp -s - "$SCAN_STDOUT"; then
      SCAN_SETTING_CURRENT=true
      return 0
    fi
    if printf '%s\n' false | cmp -s - "$SCAN_STDOUT"; then
      SCAN_SETTING_CURRENT=false
      return 0
    fi
  elif [ "$SCAN_STATUS" -eq 1 ] && [ ! -s "$SCAN_STDOUT" ] \
    && printf '%s\n' 'Config key not set: plugins.scan_on_install' | cmp -s - "$SCAN_STDERR"; then
    SCAN_SETTING_CURRENT=unset
    return 0
  fi
  return 1
}

restore_scan_setting() {
  if read_scan_setting && [ "$SCAN_SETTING_CURRENT" = "$SCAN_SETTING_ORIGINAL" ]; then
    return 0
  fi
  case "$SCAN_SETTING_ORIGINAL" in
    unset) hermes config unset plugins.scan_on_install >/dev/null 2>&1 ;;
    true|false) hermes config set plugins.scan_on_install "$SCAN_SETTING_ORIGINAL" >/dev/null 2>&1 ;;
    *) return 1 ;;
  esac
  read_scan_setting && [ "$SCAN_SETTING_CURRENT" = "$SCAN_SETTING_ORIGINAL" ]
}

read_plugin_enabled_state() {
  PLUGIN_LIST="$VERIFY_DIR/plugins.json"
  if ! hermes plugins list --user --enabled --json >"$PLUGIN_LIST" 2>/dev/null; then
    return 1
  fi
  if python3 - "$PLUGIN_LIST" 2>/dev/null <<'PY'
import json
import pathlib
import sys

try:
    value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    raise SystemExit(2)
if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
    raise SystemExit(2)
raise SystemExit(0 if any(item.get("name") == "hermes-t3-control" for item in value) else 1)
PY
  then
    PLUGIN_ENABLED_STATE=enabled
    return 0
  else
    PLUGIN_LIST_STATUS=$?
  fi
  if [ "$PLUGIN_LIST_STATUS" -eq 1 ]; then
    PLUGIN_ENABLED_STATE=disabled
    return 0
  fi
  return 1
}

safe_git -C "$VERIFY_DIR/repo" init -q
safe_git -C "$VERIFY_DIR/repo" -c protocol.file.allow=never fetch -q --no-tags https://github.com/thetasigmaio/hermes-t3-control.git 'refs/tags/v1.3.1:refs/tags/v1.3.1'
TAG_OBJECT_TYPE="$(safe_git -C "$VERIFY_DIR/repo" cat-file -t refs/tags/v1.3.1)"
if [ "$TAG_OBJECT_TYPE" != tag ]; then
  printf '%s\n' 'Release ref is not an annotated tag.' >&2
  exit 1
fi
TAG_VERIFY="$(safe_git -C "$VERIFY_DIR/repo" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile=/dev/null verify-tag --raw v1.3.1 2>&1 || true)"
if ! printf '%s\n' "$TAG_VERIFY" | grep -Fqx 'Good "git" signature with ED25519 key SHA256:w7wKQukCKTYbelHXBB3necJ6DkvZ9l01ehw83L5r4T4'; then
  printf '%s\n' 'Release tag signature did not match the pinned signer.' >&2
  exit 1
fi
TAG_EMBEDDED_NAME="$(safe_git -C "$VERIFY_DIR/repo" for-each-ref --format='%(tag)' refs/tags/v1.3.1)"
if [ "$TAG_EMBEDDED_NAME" != v1.3.1 ]; then
  printf '%s\n' 'Release tag name did not match the requested version.' >&2
  exit 1
fi

HERMES_T3_CONTROL_REF="$(safe_git -C "$VERIFY_DIR/repo" rev-parse --verify 'v1.3.1^{commit}')"
printf '%s\n' "$HERMES_T3_CONTROL_REF" | grep -Eq '^[0-9a-f]{40}$'

if ! read_scan_setting; then
  printf '%s\n' 'Could not read the active Hermes scan setting.' >&2
  exit 1
fi
SCAN_SETTING_ORIGINAL="$SCAN_SETTING_CURRENT"
case "$SCAN_SETTING_ORIGINAL" in
  true) ;;
  false|unset) ;;
  *)
    printf '%s\n' 'Existing scan setting is not a boolean.' >&2
    exit 1
    ;;
esac

printf '%s\n' 'Installing the verified release disabled for validation...'
if [ "$SCAN_SETTING_ORIGINAL" != true ]; then
  SCAN_SETTING_CHANGED=1
  if ! hermes config set plugins.scan_on_install true >/dev/null 2>&1 \
    || ! read_scan_setting \
    || [ "$SCAN_SETTING_CURRENT" != true ]; then
    printf '%s\n' 'Hermes did not enable install-time scanning.' >&2
    exit 1
  fi
fi
hermes plugins install thetasigmaio/hermes-t3-control --ref "$HERMES_T3_CONTROL_REF" --no-enable
PLUGIN_CREATED=1
printf '%s\n' 'Validating and enabling Hermes T3 Control...'
hermes plugins doctor hermes-t3-control --ci
hermes plugins enable hermes-t3-control --no-allow-tool-override
if ! read_plugin_enabled_state || [ "$PLUGIN_ENABLED_STATE" != enabled ]; then
  printf '%s\n' 'Hermes did not enable the installed plugin.' >&2
  exit 1
fi
printf '%s\n' 'Hermes T3 Control 1.3.1 is installed and enabled.'
printf '%s\n' 'Reload the process that owns your Hermes session before trying the tools.'
