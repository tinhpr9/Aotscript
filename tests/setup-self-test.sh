#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP="$ROOT/setup.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
  printf 'SETUP_SELF_TEST=FAIL: %s\n' "$*" >&2
  exit 1
}

run_dry() {
  local home="$1" id="$2" group="$3"
  HOME="$home" \
  XDG_STATE_HOME="$home/state" \
  AOTSCRIPT_SETUP_DEVICE_ID="$id" \
  AOTSCRIPT_SETUP_GROUP="$group" \
  AOTSCRIPT_SETUP_CONFIRM=yes \
  AOTSCRIPT_SETUP_CHECKPOINT_ACTION="DA XONG" \
  AOTSCRIPT_SETUP_DRY_RUN=1 \
    bash "$SETUP"
}

[ "$(bash "$SETUP" --validate-id M88)" = m88 ] || fail "valid ID normalization"
if bash "$SETUP" --validate-id m088 >/dev/null 2>&1; then
  fail "invalid ID accepted"
fi
[ "$(bash "$SETUP" --validate-group nova)" = NOVA ] || fail "valid group normalization"
if bash "$SETUP" --validate-group OTHER >/dev/null 2>&1; then
  fail "invalid group accepted"
fi

mkdir -p "$TMP/resume"
first="$(run_dry "$TMP/resume" M88 nova)"
printf '%s\n' "$first" | grep -Fq 'provision sẽ chạy đúng một lần' || fail "first dry provision"
second="$(run_dry "$TMP/resume" m88 NOVA)"
printf '%s\n' "$second" | grep -Fq 'DRY-RUN: wizard sẽ được khởi động/resume' || fail "resume wizard"
[ "$(cat "$TMP/resume/state/aotscript/setup-driver/device_id")" = m88 ] || fail "durable ID state"
[ "$(cat "$TMP/resume/state/aotscript/setup-driver/provision_initialized")" = yes ] || fail "durable provision state"

if run_dry "$TMP/resume" m89 NOVA >"$TMP/mismatch.out" 2>&1; then
  fail "changed ID was accepted"
fi
grep -Fq "State cũ thuộc Device ID 'm88'" "$TMP/mismatch.out" || fail "changed ID message"

mkdir -p "$TMP/existing/state/aotscript"
printf '%s\n' '{"device_id":"m91","device_group":"NOVA","phase":"manual_pre"}' \
  > "$TMP/existing/state/aotscript/mprovision.json"
existing="$(run_dry "$TMP/existing" m91 NOVA)"
printf '%s\n' "$existing" | grep -Fq 'không chạy lại entrypoint hoặc backup' || fail "existing provision resume"

mkdir -p "$TMP/existing-mismatch/state/aotscript"
printf '%s\n' '{"device_id":"m92","device_group":"MARMOT","phase":"manual_pre"}' \
  > "$TMP/existing-mismatch/state/aotscript/mprovision.json"
if run_dry "$TMP/existing-mismatch" m93 MARMOT >"$TMP/mprovision-mismatch.out" 2>&1; then
  fail "mprovision ID mismatch was accepted"
fi
grep -Fq "mprovision hiện có thuộc 'm92'" "$TMP/mprovision-mismatch.out" || fail "mprovision mismatch message"

mkdir -p "$TMP/lock"
HOME="$TMP/lock" \
XDG_STATE_HOME="$TMP/lock/state" \
AOTSCRIPT_SETUP_DEVICE_ID=m90 \
AOTSCRIPT_SETUP_GROUP=MARMOT \
AOTSCRIPT_SETUP_CONFIRM=yes \
AOTSCRIPT_SETUP_CHECKPOINT_ACTION="DA XONG" \
AOTSCRIPT_SETUP_DRY_RUN=1 \
AOTSCRIPT_SETUP_HOLD_LOCK_SECONDS=3 \
  bash "$SETUP" >"$TMP/lock-owner.out" 2>&1 &
owner_pid=$!

for _ in {1..30}; do
  [ -d "$TMP/lock/state/aotscript/setup-driver/setup.lock" ] && break
  sleep 0.1
done
[ -d "$TMP/lock/state/aotscript/setup-driver/setup.lock" ] || fail "lock owner did not acquire lock"

if HOME="$TMP/lock" \
   XDG_STATE_HOME="$TMP/lock/state" \
   AOTSCRIPT_SETUP_DEVICE_ID=m90 \
   AOTSCRIPT_SETUP_GROUP=MARMOT \
   AOTSCRIPT_SETUP_CONFIRM=yes \
   AOTSCRIPT_SETUP_CHECKPOINT_ACTION="DA XONG" \
   AOTSCRIPT_SETUP_DRY_RUN=1 \
     bash "$SETUP" >"$TMP/lock-second.out" 2>&1; then
  fail "concurrent setup was accepted"
fi
grep -Fq 'Một phiên setup khác đang chạy' "$TMP/lock-second.out" || fail "concurrent lock message"
wait "$owner_pid"

grep -Fq 'AOTSCRIPT_PROVISION_REF="$PROVISION_REF"' "$SETUP" || fail "pinned ref not delegated"
grep -Fq 'PROVISION_SHA256="b71cd9990c1257e1e17be250226fffd688ecfb3499d8a878fb04c401518b6934"' "$SETUP" || fail "pinned provision hash"
if grep -Fq 'aot-group-control' "$SETUP"; then
  fail "group control referenced by setup"
fi
if grep -Eqi '(ghp_[A-Za-z0-9]|password[[:space:]]*=|cookie[[:space:]]*=|agent_report_secret[[:space:]]*=)' "$SETUP"; then
  fail "possible secret in setup"
fi

printf 'SETUP_VALIDATION_SELF_TEST=PASS\n'
printf 'SETUP_RESUME_SELF_TEST=PASS\n'
printf 'SETUP_ID_MISMATCH_SELF_TEST=PASS\n'
printf 'SETUP_EXISTING_PROVISION_SELF_TEST=PASS\n'
printf 'SETUP_LOCK_SELF_TEST=PASS\n'
printf 'SETUP_DRY_RUN_SELF_TEST=PASS\n'
printf 'SETUP_NO_GROUP_CONTROL_SELF_TEST=PASS\n'
