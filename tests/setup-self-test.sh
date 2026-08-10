#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP="${AOTSCRIPT_SETUP_UNDER_TEST:-$ROOT/setup.sh}"
PROVISION="${AOTSCRIPT_PROVISION_UNDER_TEST:-$ROOT/provision-device.sh}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
  printf 'SETUP_SELF_TEST=FAIL:%s\n' "$*" >&2
  exit 1
}

host_hash() {
  printf '%s' "$1" | sha256sum | awk '{print $1}'
}

write_complete_fixture() {
  local root="$1" id="$2" group="$3" host="$4"
  local state="$root/state/aotscript"
  local shouko="$root/storage/Download/Shouko"
  mkdir -p "$root/home" "$root/prefix/bin" "$state/setup-driver" \
    "$shouko" "$root/storage/Delta/Autoexecute"
  printf '%s\n' "$id" > "$state/setup-driver/device_id"
  printf '%s\n' "$group" > "$state/setup-driver/device_group"
  printf '%s\n' "$(host_hash "$host")" > "$state/setup-driver/host_fingerprint"
  printf 'yes\n' > "$state/setup-driver/provision_initialized"
  printf 'yes\n' > "$state/setup-driver/bootstrap_ui_done"
  printf '%s\n' \
    "{\"device_id\":\"$id\",\"device_group\":\"$group\",\"phase\":\"complete\",\"run_id\":\"source-run\",\"backup_before\":\"old-local\",\"backup_after_remote\":\"old-remote\",\"publish_next_status\":\"OK\",\"report_json\":\"old-report\"}" \
    > "$state/mprovision.json"
  printf '%s\n' "$id" > "$shouko/device_id.txt"
  printf '%s\n' "$group" > "$shouko/device_group.txt"
  printf '%s\n' '{"device_group":"NOVA","last_command_id":"source-command"}' \
    > "$shouko/agent_state.json"
  printf '%s\n' "{\"device_id\":\"$id\",\"status\":\"complete\"}" \
    > "$shouko/provision_report.json"
  printf 'source-report\n' > "$shouko/provision_report.txt"
  printf '%s\n' '{"worker_report_url":"https://example.invalid/report","agent_report_secret":"self-test-placeholder"}' \
    > "$shouko/agent_config.json"
  printf 'print("fixture agent")\n' > "$root/storage/Download/Agent_Core.py"
  printf 'keep-shouko\n' > "$shouko/cookie.txt"
  printf 'keep-delta\n' > "$root/storage/Delta/keep.txt"
  printf 'keep-autoexec\n' > "$root/storage/Delta/Autoexecute/keep.lua"
  cp -p "$PROVISION" "$state/setup-driver/provision-device-test-cache.sh"
}

run_setup() {
  local root="$1" host="$2"
  shift 2
  HOME="$root/home" \
  XDG_STATE_HOME="$root/state" \
  PREFIX="$root/prefix" \
  AOTSCRIPT_SETUP_TEST_MODE=1 \
  AOTSCRIPT_SETUP_INPUT_MODE=env \
  AOTSCRIPT_SETUP_STORAGE_ROOT="$root/storage" \
  AOTSCRIPT_SETUP_HOST_ID="$host" \
    "$@"
}

bash -n "$SETUP"
bash -n "$PROVISION"
[ "$(bash "$SETUP" --validate-id M74)" = m74 ] || fail validation-id
if bash "$SETUP" --validate-id m074 >/dev/null 2>&1; then
  fail validation-id-invalid
fi
[ "$(bash "$SETUP" --validate-group marmot)" = MARMOT ] || fail validation-group
if bash "$SETUP" --validate-group OTHER >/dev/null 2>&1; then
  fail validation-group-invalid
fi

# Fresh install: bootstrap copy installs a durable local launcher.
fresh="$TMP/fresh"
mkdir -p "$fresh/prefix/bin" "$fresh/storage" "$fresh/home"
bootstrap="$TMP/bootstrap-once.sh"
cp -p "$SETUP" "$bootstrap"
run_setup "$fresh" fresh-host env \
  AOTSCRIPT_SETUP_DRY_RUN=1 \
  AOTSCRIPT_SETUP_DEVICE_ID=m88 \
  AOTSCRIPT_SETUP_GROUP=NOVA \
  AOTSCRIPT_SETUP_CONFIRM=yes \
  AOTSCRIPT_SETUP_CHECKPOINT_ACTION='DA XONG' \
  bash "$bootstrap" > "$TMP/fresh.out"
[ -x "$fresh/prefix/bin/aotsetup" ] || fail local-launcher-install
rm -f "$bootstrap"
run_setup "$fresh" fresh-host env \
  AOTSCRIPT_SETUP_DRY_RUN=1 \
  AOTSCRIPT_SETUP_CHECKPOINT_ACTION='DA XONG' \
  "$fresh/prefix/bin/aotsetup" > "$TMP/local-resume.out"
grep -Fq 'Lần sau chỉ chạy aotsetup' "$TMP/local-resume.out" || fail local-launcher-resume

# Explicit update uses a checked local fixture and leaves a valid launcher.
run_setup "$fresh" fresh-host env \
  AOTSCRIPT_SETUP_UPDATE_SOURCE="$SETUP" \
  "$fresh/prefix/bin/aotsetup" update > "$TMP/update.out"
bash -n "$fresh/prefix/bin/aotsetup" || fail local-update-syntax

# Bound complete identity resumes without invoking pkg or curl.
resume="$TMP/resume"
write_complete_fixture "$resume" m91 MARMOT resume-host
mkdir -p "$resume/fake-bin"
printf '%s\n' '#!/bin/sh' 'printf called > "${AOT_TEST_PKG_MARKER:?}"' 'exit 99' \
  > "$resume/fake-bin/pkg"
printf '%s\n' '#!/bin/sh' 'printf called > "${AOT_TEST_CURL_MARKER:?}"' 'exit 99' \
  > "$resume/fake-bin/curl"
chmod 700 "$resume/fake-bin/pkg" "$resume/fake-bin/curl"
PATH="$resume/fake-bin:$PATH" \
AOT_TEST_PKG_MARKER="$resume/pkg-called" \
AOT_TEST_CURL_MARKER="$resume/curl-called" \
run_setup "$resume" resume-host bash "$SETUP" > "$TMP/resume.out"
[ ! -e "$resume/pkg-called" ] || fail resume-called-pkg
[ ! -e "$resume/curl-called" ] || fail resume-called-curl
grep -Fq 'workflow complete' "$TMP/resume.out" || fail resume-complete

# Consistent clone migration preserves every non-identity sentinel.
clone="$TMP/clone"
write_complete_fixture "$clone" m117 NOVA source-host
before_cookie="$(sha256sum "$clone/storage/Download/Shouko/cookie.txt" | awk '{print $1}')"
before_delta="$(sha256sum "$clone/storage/Delta/keep.txt" | awk '{print $1}')"
before_auto="$(sha256sum "$clone/storage/Delta/Autoexecute/keep.lua" | awk '{print $1}')"
before_config="$(sha256sum "$clone/storage/Download/Shouko/agent_config.json" | awk '{print $1}')"
run_setup "$clone" target-host env \
  AOTSCRIPT_SETUP_DEVICE_ID=m74 \
  AOTSCRIPT_SETUP_GROUP=NOVA \
  AOTSCRIPT_SETUP_CONFIRM=yes \
  bash "$SETUP" > "$TMP/clone.out" 2>&1
grep -Fq 'PHÁT HIỆN CLONE: m117 → m74' "$TMP/clone.out" || fail clone-detection
[ "$(cat "$clone/state/aotscript/setup-driver/device_id")" = m74 ] || fail clone-setup-id
[ "$(cat "$clone/storage/Download/Shouko/device_id.txt")" = m74 ] || fail clone-shouko-id
[ "$(sha256sum "$clone/storage/Download/Shouko/cookie.txt" | awk '{print $1}')" = "$before_cookie" ] || fail clone-cookie-hash
[ "$(sha256sum "$clone/storage/Delta/keep.txt" | awk '{print $1}')" = "$before_delta" ] || fail clone-delta-hash
[ "$(sha256sum "$clone/storage/Delta/Autoexecute/keep.lua" | awk '{print $1}')" = "$before_auto" ] || fail clone-autoexec-hash
[ "$(sha256sum "$clone/storage/Download/Shouko/agent_config.json" | awk '{print $1}')" = "$before_config" ] || fail clone-config-hash
[ ! -e "$clone/storage/Download/Shouko/provision_report.json" ] || fail old-report-active
python - "$clone/state/aotscript/mprovision.json" <<'PY'
import json
import pathlib
import sys

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert data["device_id"] == "m74"
assert data["phase"] == "complete"
assert data["run_id"] != "source-run"
for key in (
    "backup_before", "backup_before_remote", "backup_after",
    "backup_after_remote", "report_json", "report_text",
    "report_remote", "publish_next_status",
):
    assert data[key] == ""
PY
manifest="$(find "$clone/state/aotscript/foreign-state" -name manifest.json -type f -print -quit)"
[ -s "$manifest" ] || fail clone-manifest
python - "$manifest" <<'PY'
import hashlib
import json
import pathlib
import sys

manifest = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert manifest["source_id"] == "m117"
assert manifest["target_id"] == "m74"
assert manifest["files"]
for item in manifest["files"]:
    archived = pathlib.Path(item["archive_path"])
    assert archived.is_file()
    assert hashlib.sha256(archived.read_bytes()).hexdigest() == item["sha256"]
PY
if grep -Fq 'm117' "$SETUP"; then
  fail migration-hardcoded-source-id
fi

# Conflicting sources fail closed and remain byte-identical.
conflict="$TMP/conflict"
write_complete_fixture "$conflict" m117 NOVA conflict-source
python - "$conflict/state/aotscript/mprovision.json" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["device_id"] = "m88"
path.write_text(json.dumps(data) + "\n", encoding="utf-8")
PY
before_conflict="$(sha256sum "$conflict/state/aotscript/setup-driver/device_id" "$conflict/state/aotscript/mprovision.json" "$conflict/storage/Download/Shouko/device_id.txt")"
if run_setup "$conflict" conflict-target env \
   AOTSCRIPT_SETUP_DEVICE_ID=m74 AOTSCRIPT_SETUP_GROUP=NOVA AOTSCRIPT_SETUP_CONFIRM=yes \
   bash "$SETUP" > "$TMP/conflict.out" 2>&1; then
  fail conflicting-sources-accepted
fi
after_conflict="$(sha256sum "$conflict/state/aotscript/setup-driver/device_id" "$conflict/state/aotscript/mprovision.json" "$conflict/storage/Download/Shouko/device_id.txt")"
[ "$before_conflict" = "$after_conflict" ] || fail conflict-mutated-state
grep -Fq 'IDENTITY_CONFLICT=' "$TMP/conflict.out" || fail conflict-list-missing

# Malformed mprovision JSON fails closed.
broken="$TMP/broken"
write_complete_fixture "$broken" m117 NOVA broken-source
printf '{broken\n' > "$broken/state/aotscript/mprovision.json"
broken_before="$(sha256sum "$broken/state/aotscript/mprovision.json" "$broken/storage/Download/Shouko/device_id.txt")"
if run_setup "$broken" broken-target env \
   AOTSCRIPT_SETUP_DEVICE_ID=m74 AOTSCRIPT_SETUP_GROUP=NOVA AOTSCRIPT_SETUP_CONFIRM=yes \
   bash "$SETUP" > "$TMP/broken.out" 2>&1; then
  fail broken-json-accepted
fi
broken_after="$(sha256sum "$broken/state/aotscript/mprovision.json" "$broken/storage/Download/Shouko/device_id.txt")"
[ "$broken_before" = "$broken_after" ] || fail broken-json-mutated-state
grep -Fq 'invalid_json:' "$TMP/broken.out" || fail broken-json-message

# Interruption after archive resumes from journal without another identity prompt.
interrupted="$TMP/interrupted"
write_complete_fixture "$interrupted" m117 NOVA interrupt-source
set +e
run_setup "$interrupted" interrupt-target env \
  AOTSCRIPT_SETUP_DEVICE_ID=m74 AOTSCRIPT_SETUP_GROUP=NOVA AOTSCRIPT_SETUP_CONFIRM=yes \
  AOTSCRIPT_SETUP_INTERRUPT_AFTER=archive bash "$SETUP" > "$TMP/interrupted-first.out" 2>&1
interrupt_rc=$?
set -e
[ "$interrupt_rc" -eq 75 ] || fail interruption-exit-code
[ "$(cat "$interrupted/storage/Download/Shouko/device_id.txt")" = m117 ] || fail interruption-mutated-early
run_setup "$interrupted" interrupt-target bash "$SETUP" > "$TMP/interrupted-second.out" 2>&1
[ "$(cat "$interrupted/storage/Download/Shouko/device_id.txt")" = m74 ] || fail interruption-resume-id
grep -Fq 'stage=archived' "$TMP/interrupted-second.out" || fail interruption-resume-stage

# Lock excludes a concurrent launcher.
locked="$TMP/locked"
write_complete_fixture "$locked" m95 NOVA lock-host
run_setup "$locked" lock-host env AOTSCRIPT_SETUP_HOLD_LOCK_SECONDS=3 \
  bash "$SETUP" > "$TMP/lock-owner.out" 2>&1 &
owner=$!
for _ in {1..40}; do
  [ -d "$locked/state/aotscript/setup-driver/setup.lock" ] && break
  sleep 0.1
done
[ -d "$locked/state/aotscript/setup-driver/setup.lock" ] || fail lock-not-acquired
if run_setup "$locked" lock-host bash "$SETUP" > "$TMP/lock-second.out" 2>&1; then
  fail concurrent-run-accepted
fi
grep -Fq 'Một phiên aotsetup khác đang chạy' "$TMP/lock-second.out" || fail lock-message
wait "$owner"

if grep -Fq 'aot-group-control' "$SETUP"; then
  fail group-control-reference
fi
grep -Fq 'CACHE_DIR="$STATE_DIR/setup-driver"' "$PROVISION" || fail provision-cache-wrapper
if grep -Fq 'Bật root trước khi bắt đầu' "$PROVISION"; then
  fail root-checkpoint-visible
fi
timeout 180 env AOTSCRIPT_SETUP_UNDER_TEST="$SETUP" \
  python "$ROOT/tests/setup-terminal-lock-test.py" ||
  fail terminal-lock-regressions

printf 'AOTSETUP_FRESH_INSTALL=PASS\n'
printf 'AOTSETUP_LOCAL_LAUNCHER=PASS\n'
printf 'AOTSETUP_EXPLICIT_UPDATE=PASS\n'
printf 'AOTSETUP_RESUME_NO_PKG_CURL=PASS\n'
printf 'AOTSETUP_CLONE_MIGRATION=PASS\n'
printf 'AOTSETUP_CONFLICT_FAIL_CLOSED=PASS\n'
printf 'AOTSETUP_BROKEN_JSON_FAIL_CLOSED=PASS\n'
printf 'AOTSETUP_INTERRUPTION_RESUME=PASS\n'
printf 'AOTSETUP_NON_IDENTITY_HASHES=PASS\n'
printf 'AOTSETUP_LOCK=PASS\n'
printf 'AOTSETUP_NO_GROUP_CONTROL=PASS\n'
