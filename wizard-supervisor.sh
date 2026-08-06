#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
HOME="${HOME:-/data/data/com.termux/files/home}"
STATE_DIR="${AOTSCRIPT_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/aotscript}"
STATE_FILE="${AOTSCRIPT_STATE_FILE:-$STATE_DIR/mprovision.json}"
SUPERVISOR_STATE="$STATE_DIR/wizard-supervisor.state"
SUPERVISOR_LOG="$STATE_DIR/wizard-supervisor.log"
PID_FILE="$STATE_DIR/wizard-supervisor.pid"
LOCK_DIR="$STATE_DIR/wizard-supervisor.lock"
MPROVISION="${AOTSCRIPT_MPROVISION:-$PREFIX/bin/mprovision}"
SELF="${AOTSCRIPT_WIZARD_SELF:-$HOME/bin/aotscript-wizard}"
NOTIFICATION_ID="${AOTSCRIPT_NOTIFICATION_ID:-9147}"
TERMUX_API_PACKAGE="com.termux.api"
SWIFT_PACKAGE="org.swiftapps.swiftbackup"
WATCH_INTERVAL="${AOTSCRIPT_WATCH_INTERVAL:-5}"
API_HELP_URL="https://github.com/termux/termux-api#installation"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

now() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

log() {
  printf '%s %s\n' "$(now)" "$*" >> "$SUPERVISOR_LOG"
  chmod 600 "$SUPERVISOR_LOG" 2>/dev/null || true
}

state_get() {
  local key="$1"

  python - "$STATE_FILE" "$key" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(0)

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(2)

if not isinstance(data, dict):
    raise SystemExit(2)

value = data.get(sys.argv[2], "")
print(value if isinstance(value, str) else str(value))
PY
}

supervisor_set() {
  local value="$1"
  local tmp="$SUPERVISOR_STATE.tmp.$$"

  printf '%s\n' "$value" > "$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$SUPERVISOR_STATE"
}

supervisor_get() {
  if [ -s "$SUPERVISOR_STATE" ]; then
    tr -d '\r\n ' < "$SUPERVISOR_STATE"
  else
    printf 'stopped\n'
  fi
}

root_ok() {
  su -c id 2>/dev/null | grep -q 'uid=0(root)'
}

package_exists() {
  local package="$1"

  if root_ok; then
    su -c "pm path '$package'" >/dev/null 2>&1
  else
    /system/bin/pm path "$package" >/dev/null 2>&1
  fi
}

google_account_present() {
  root_ok || return 1

  su -c 'dumpsys account' 2>/dev/null |
    python -c '
import re
import sys
text = sys.stdin.read()
raise SystemExit(
    0 if re.search(r"type=com[.]google(?:[},\\s]|$)", text) else 1
)
'
}

api_app_ready() {
  package_exists "$TERMUX_API_PACKAGE"
}

api_cli_ready() {
  command -v termux-notification >/dev/null 2>&1 &&
    command -v termux-toast >/dev/null 2>&1
}

open_api_install_page() {
  if root_ok; then
    su -c "am start \
      -a android.intent.action.VIEW \
      -d '$API_HELP_URL'" \
      >/dev/null 2>&1 || true
  else
    /system/bin/am start \
      -a android.intent.action.VIEW \
      -d "$API_HELP_URL" \
      >/dev/null 2>&1 || true
  fi
}

ensure_termux_api() {
  if ! api_app_ready; then
    log "TERMUX_API_APP=MISSING"
    open_api_install_page
    return 1
  fi

  if ! api_cli_ready; then
    log "TERMUX_API_CLI=INSTALLING"
    pkg install -y termux-api >> "$SUPERVISOR_LOG" 2>&1 ||
      return 1
  fi

  api_cli_ready
}

notification_help() {
  termux-notification --help 2>&1 || true
}

notification_remove() {
  if command -v termux-notification-remove >/dev/null 2>&1; then
    termux-notification-remove "$NOTIFICATION_ID" >/dev/null 2>&1 || true
  fi
}

open_notification_settings() {
  if root_ok; then
    su -c "am start \
      -a android.settings.APP_NOTIFICATION_SETTINGS \
      --es android.provider.extra.APP_PACKAGE '$TERMUX_API_PACKAGE'" \
      >/dev/null 2>&1 || true
  else
    /system/bin/am start \
      -a android.settings.APP_NOTIFICATION_SETTINGS \
      --es android.provider.extra.APP_PACKAGE "$TERMUX_API_PACKAGE" \
      >/dev/null 2>&1 || true
  fi
}

notify() {
  local title="$1"
  local content="$2"
  local mode="${3:-manual}"
  local help
  local args=(
    --id "$NOTIFICATION_ID"
    --title "$title"
    --content "$content"
    --priority high
  )

  ensure_termux_api || {
    log "NOTIFICATION=TERMUX_API_REQUIRED"
    return 1
  }

  help="$(notification_help)"

  if printf '%s\n' "$help" | grep -Fq -- '--ongoing'; then
    args+=(--ongoing)
  fi

  case "$mode" in
    manual)
      args+=(
        --button1 "ĐÃ XONG"
        --button1-action "$SELF done"
        --button2 "MỞ LẠI"
        --button2-action "$SELF open"
        --button3 "DỪNG"
        --button3-action "$SELF stop"
      )
      ;;
    waiting)
      args+=(
        --button1 "MỞ LẠI"
        --button1-action "$SELF open"
        --button2 "DỪNG"
        --button2-action "$SELF stop"
      )
      ;;
    complete)
      args+=(
        --button1 "ẨN"
        --button1-action "$SELF stop"
      )
      ;;
    *)
      log "NOTIFICATION_MODE_INVALID=$mode"
      return 1
      ;;
  esac

  if termux-notification "${args[@]}" >/dev/null 2>&1; then
    termux-toast -s "$content" >/dev/null 2>&1 || true
    log "NOTIFICATION=OK MODE=$mode CONTENT=$content"
    return 0
  fi

  log "NOTIFICATION=FAILED MODE=$mode"
  termux-toast -s "Bật quyền thông báo cho Termux:API" \
    >/dev/null 2>&1 || true
  open_notification_settings
  return 1
}

phase_and_step() {
  printf '%s|%s\n' \
    "$(state_get phase)" \
    "$(state_get wizard_step)"
}

current_message() {
  local phase step
  phase="$(state_get phase)"
  step="$(state_get wizard_step)"

  case "$phase:$step" in
    complete:*)
      printf 'complete|Máy đã hoàn tất. Không còn bước cần làm.|complete\n'
      ;;
    preflight:*|await_root:*)
      printf 'root|Bật root. Hệ thống sẽ tự kiểm tra và tiếp tục.|waiting\n'
      ;;
    manual_pre:await_google_login)
      printf 'google|Đăng nhập Google. Xong sẽ tự mở Swift Backup.|waiting\n'
      ;;
    manual_pre:await_swift_backup_before)
      printf 'swift_before|Backup Termux kèm data, rồi bấm ĐÃ XONG.|manual\n'
      ;;
    manual_post:await_swift_restore)
      printf 'swift_restore|Restore RESTORE_DATA và các app còn lại, rồi bấm ĐÃ XONG.|manual\n'
      ;;
    manual_post:manual_post_remaining)
      printf 'manual_post|Hoàn tất Shouko, cookie, WARP, auto-exec và kiểm tra account; rồi bấm ĐÃ XONG.|manual\n'
      ;;
    await_rclone_before:*|await_rclone_after:*)
      printf 'rclone|Cấu hình gdrive: xong rồi bấm ĐÃ XONG.|manual\n'
      ;;
    automatic:*|await_root_setup:*|finalize:*)
      printf 'working|Đang chạy bước tự động. Vui lòng chờ.|waiting\n'
      ;;
    manual_pre:*)
      printf 'manual_pre|Mở lại bước hiện tại hoặc bấm ĐÃ XONG khi đã hoàn tất.|manual\n'
      ;;
    manual_post:*)
      printf 'manual_post|Mở lại bước hiện tại hoặc bấm ĐÃ XONG khi đã hoàn tất.|manual\n'
      ;;
    *)
      printf 'unknown|Trạng thái chưa xác định. Bấm MỞ LẠI để kiểm tra.|waiting\n'
      ;;
  esac
}

show_current() {
  local code content mode
  IFS='|' read -r code content mode < <(current_message)
  notify "Aotscript Setup" "$content" "$mode" || true
  printf 'WIZARD_CODE=%s\n' "$code"
  printf 'WIZARD_MESSAGE=%s\n' "$content"
  printf 'WIZARD_MODE=%s\n' "$mode"
}

atomic_log_replace() {
  local source="$1"
  chmod 600 "$source" 2>/dev/null || true
  mv -f "$source" "$SUPERVISOR_LOG"
  chmod 600 "$SUPERVISOR_LOG" 2>/dev/null || true
}

run_mprovision() {
  local tmp status
  tmp="$STATE_DIR/wizard-command.tmp.$$"

  if "$MPROVISION" "$@" > "$tmp" 2>&1; then
    atomic_log_replace "$tmp"
    return 0
  fi

  status=$?
  atomic_log_replace "$tmp"
  notify \
    "Aotscript Setup" \
    "Có lỗi. Mở Termux và xem wizard-supervisor.log." \
    waiting || true
  return "$status"
}

open_google() {
  root_ok || return 1
  su -c "am start \
    -a android.settings.ADD_ACCOUNT_SETTINGS \
    --esa account_types com.google" \
    >/dev/null 2>&1
}

open_swift() {
  package_exists "$SWIFT_PACKAGE" || return 1
  if root_ok; then
    su -c "monkey -p '$SWIFT_PACKAGE' \
      -c android.intent.category.LAUNCHER 1" \
      >/dev/null 2>&1
  else
    /system/bin/monkey -p "$SWIFT_PACKAGE" \
      -c android.intent.category.LAUNCHER 1 \
      >/dev/null 2>&1
  fi
}

open_termux() {
  if root_ok; then
    su -c "monkey -p com.termux \
      -c android.intent.category.LAUNCHER 1" \
      >/dev/null 2>&1
  else
    /system/bin/monkey -p com.termux \
      -c android.intent.category.LAUNCHER 1 \
      >/dev/null 2>&1
  fi
}

open_current() {
  local phase step
  phase="$(state_get phase)"
  step="$(state_get wizard_step)"

  case "$phase:$step" in
    preflight:*|await_root:*|await_root_setup:*)
      termux-toast -s "Bật root, rồi hệ thống sẽ tự tiếp tục" \
        >/dev/null 2>&1 || true
      ;;
    manual_pre:await_google_login)
      open_google || return 1
      ;;
    manual_pre:await_swift_backup_before|manual_post:await_swift_restore)
      open_swift || return 1
      ;;
    await_rclone_before:*|await_rclone_after:*)
      open_termux || return 1
      ;;
    manual_post:manual_post_remaining)
      termux-toast -s "Hoàn tất các bước thủ công còn lại" \
        >/dev/null 2>&1 || true
      ;;
    complete:*)
      ;;
    *)
      run_mprovision wizard || return 1
      ;;
  esac

  show_current
}

advance_safe() {
  local phase step
  phase="$(state_get phase)"
  step="$(state_get wizard_step)"

  case "$phase:$step" in
    complete:*)
      show_current
      ;;
    preflight:*|await_root:*)
      run_mprovision wizard
      show_current
      ;;
    manual_pre:await_google_login)
      if google_account_present; then
        run_mprovision wizard
      else
        open_google || true
      fi
      show_current
      ;;
    manual_pre:await_swift_backup_before)
      show_current
      ;;
    manual_pre:*)
      run_mprovision wizard
      show_current
      ;;
    automatic:*|await_root_setup:*|await_rclone_before:*)
      run_mprovision wizard
      show_current
      ;;
    manual_post:await_swift_restore|manual_post:manual_post_remaining)
      show_current
      ;;
    manual_post:*)
      run_mprovision wizard
      show_current
      ;;
    await_rclone_after:*|finalize:*)
      run_mprovision wizard
      show_current
      ;;
    *)
      run_mprovision wizard
      show_current
      ;;
  esac
}

done_action() {
  local phase step
  phase="$(state_get phase)"
  step="$(state_get wizard_step)"

  case "$phase:$step" in
    manual_pre:await_swift_backup_before)
      run_mprovision wizard
      ;;
    manual_post:await_swift_restore)
      run_mprovision wizard
      ;;
    manual_post:manual_post_remaining)
      run_mprovision done post
      ;;
    await_rclone_before:*|await_rclone_after:*)
      run_mprovision resume
      ;;
    preflight:*|await_root:*|manual_pre:await_google_login)
      advance_safe
      return
      ;;
    complete:*)
      show_current
      return
      ;;
    *)
      run_mprovision wizard
      ;;
  esac

  advance_safe
}

pid_is_watcher() {
  local pid="$1"
  local cmdline

  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  [ -r "/proc/$pid/cmdline" ] || return 1

  cmdline="$(
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true
  )"

  case "$cmdline" in
    *"$SELF watch"*) return 0 ;;
    *) return 1 ;;
  esac
}

stop_watcher() {
  local pid=""

  supervisor_set stopped

  if [ -s "$PID_FILE" ]; then
    pid="$(tr -d '\r\n ' < "$PID_FILE")"
  fi

  if pid_is_watcher "$pid"; then
    kill "$pid" 2>/dev/null || true
  fi

  rm -f "$PID_FILE"
  notification_remove
  command -v termux-wake-unlock >/dev/null 2>&1 &&
    termux-wake-unlock >/dev/null 2>&1 || true
}

watch_loop() {
  local phase step

  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    log "WATCH=ALREADY_RUNNING"
    return 0
  fi

  cleanup_watch() {
    rm -f "$PID_FILE"
    rmdir "$LOCK_DIR" 2>/dev/null || true
    command -v termux-wake-unlock >/dev/null 2>&1 &&
      termux-wake-unlock >/dev/null 2>&1 || true
  }
  trap cleanup_watch EXIT INT TERM

  printf '%s\n' "$$" > "$PID_FILE"
  chmod 600 "$PID_FILE"

  command -v termux-wake-lock >/dev/null 2>&1 &&
    termux-wake-lock >/dev/null 2>&1 || true

  log "WATCH=START PID=$$"

  while [ "$(supervisor_get)" = running ]; do
    phase="$(state_get phase)"
    step="$(state_get wizard_step)"

    case "$phase:$step" in
      preflight:*|await_root:*)
        if root_ok; then
          advance_safe || true
        fi
        ;;
      manual_pre:await_google_login)
        if google_account_present; then
          advance_safe || true
        fi
        ;;
      complete:*)
        show_current || true
        supervisor_set stopped
        break
        ;;
    esac

    sleep "$WATCH_INTERVAL"
  done

  log "WATCH=STOP"
}

start_watcher() {
  local pid=""

  if [ -s "$PID_FILE" ]; then
    pid="$(tr -d '\r\n ' < "$PID_FILE")"
  fi

  if pid_is_watcher "$pid"; then
    return 0
  fi

  rm -f "$PID_FILE"
  supervisor_set running

  nohup "$SELF" watch \
    >> "$SUPERVISOR_LOG" 2>&1 &

  log "WATCH=SPAWNED PID=$!"
}

start_action() {
  if ! ensure_termux_api; then
    printf 'TERMUX_API=REQUIRED\n'
    printf 'OPENED=%s\n' "$API_HELP_URL"
    exit 2
  fi

  start_watcher
  advance_safe
}

self_test() {
  local tmp fake_home fake_prefix fake_state fake_bin output

  tmp="$(mktemp -d)"
  fake_home="$tmp/home"
  fake_prefix="$tmp/prefix"
  fake_state="$tmp/state"
  fake_bin="$tmp/bin"

  mkdir -p "$fake_home/bin" "$fake_prefix/bin" "$fake_state" "$fake_bin"

  cat > "$fake_state/mprovision.json" <<'JSON'
{
  "phase": "manual_pre",
  "wizard_step": "await_swift_backup_before"
}
JSON

  cat > "$fake_prefix/bin/mprovision" <<'SH'
#!/bin/sh
printf 'FAKE_MPROVISION=%s\n' "$*"
SH
  chmod 700 "$fake_prefix/bin/mprovision"

  cat > "$fake_bin/su" <<'SH'
#!/bin/sh
case "$*" in
  *"pm path com.termux.api"*) exit 0 ;;
  *"pm path org.swiftapps.swiftbackup"*) exit 0 ;;
  *"uid=0"*) exit 0 ;;
  *)
    if [ "${1:-}" = -c ] && [ "${2:-}" = id ]; then
      printf 'uid=0(root) gid=0(root)\n'
      exit 0
    fi
    exit 0
    ;;
esac
SH
  chmod 700 "$fake_bin/su"

  cat > "$fake_bin/termux-notification" <<'SH'
#!/bin/sh
if [ "${1:-}" = --help ]; then
  printf '%s\n' '--ongoing --button1 --button1-action --button2 --button2-action --button3 --button3-action'
  exit 0
fi
printf 'NOTIFY %s\n' "$*"
SH
  chmod 700 "$fake_bin/termux-notification"

  cat > "$fake_bin/termux-toast" <<'SH'
#!/bin/sh
exit 0
SH
  chmod 700 "$fake_bin/termux-toast"

  output="$(
    PATH="$fake_bin:$PATH" \
    HOME="$fake_home" \
    PREFIX="$fake_prefix" \
    AOTSCRIPT_STATE_DIR="$fake_state" \
    AOTSCRIPT_STATE_FILE="$fake_state/mprovision.json" \
    AOTSCRIPT_MPROVISION="$fake_prefix/bin/mprovision" \
    AOTSCRIPT_WIZARD_SELF="$fake_home/bin/aotscript-wizard" \
    bash "$0" status
  )"

  printf '%s\n' "$output" | grep -Fxq 'PHASE=manual_pre'
  printf '%s\n' "$output" | grep -Fxq 'WIZARD_STEP=await_swift_backup_before'

  bash -n "$0"
  rm -rf "$tmp"
  printf 'WIZARD_SUPERVISOR_SELF_TEST=OK\n'
}

main() {
  local command="${1:-start}"

  case "$command" in
    start)
      [ "$#" = 1 ] || exit 2
      start_action
      ;;
    done)
      [ "$#" = 1 ] || exit 2
      ensure_termux_api || exit 2
      done_action
      ;;
    open)
      [ "$#" = 1 ] || exit 2
      ensure_termux_api || exit 2
      open_current
      ;;
    stop)
      [ "$#" = 1 ] || exit 2
      stop_watcher
      ;;
    watch)
      [ "$#" = 1 ] || exit 2
      watch_loop
      ;;
    status)
      [ "$#" = 1 ] || exit 2
      printf 'PHASE=%s\n' "$(state_get phase)"
      printf 'WIZARD_STEP=%s\n' "$(state_get wizard_step)"
      printf 'SUPERVISOR=%s\n' "$(supervisor_get)"
      ;;
    self-test)
      [ "$#" = 1 ] || exit 2
      self_test
      ;;
    *)
      printf 'Cách dùng: aotscript-wizard start|done|open|stop|status\n' >&2
      exit 2
      ;;
  esac
}

main "$@"
