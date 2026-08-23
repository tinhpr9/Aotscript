#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

AOTSETUP_LOCAL_LAUNCHER_V1=1
VERSION="one-command-setup-v2"
PROVISION_VERSION="phase22-aot-registration-v1"
# All provision/wizard/setup children use this exact tested revision.
PROVISION_REF="7ff02cee30791ceae8ed3a5ba88f1dbebb52a81e"
PROVISION_SHA256="a4435be33a5e4336004de9ce392935dc71f4ae62748a9340921f9b318aaa4965"
RAW_BASE="https://raw.githubusercontent.com/tinhpr9/Aotscript/$PROVISION_REF"
MAIN_SETUP_URL="https://raw.githubusercontent.com/tinhpr9/Aotscript/main/setup.sh"
PREFIX="${PREFIX:-/data/data/com.termux/files/usr}"
STATE_BASE="${XDG_STATE_HOME:-$HOME/.local/state}/aotscript"
SETUP_STATE_DIR="$STATE_BASE/setup-driver"
MPROVISION_STATE="$STATE_BASE/mprovision.json"
STORAGE_ROOT="/storage/emulated/0"
if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ]; then
  STORAGE_ROOT="${AOTSCRIPT_SETUP_STORAGE_ROOT:?test storage root is required}"
fi
SHOUKO_DIR="$STORAGE_ROOT/Download/Shouko"
AGENT_PATH="$STORAGE_ROOT/Download/Agent_Core.py"
AGENT_LOG="$STORAGE_ROOT/Download/Agent_Log.txt"
AGENT_CONFIG="$SHOUKO_DIR/agent_config.json"
LAUNCHER="$PREFIX/bin/aotsetup"
LOG_FILE="$SETUP_STATE_DIR/setup.log"
LOCK_DIR="$SETUP_STATE_DIR/setup.lock"
MIGRATION_JOURNAL="$SETUP_STATE_DIR/clone-migration.json"
CURRENT_STEP="startup"
LOCK_HELD=0
LOCK_START_TIME=""
LOCK_SCRIPT_PATH=""
TERMINAL_FD=""
TERMINAL_OPEN=0
TERMINAL_SAVED_STATE=""
PROMPT_RESULT=""
SELECTED_DEVICE_ID=""
SELECTED_DEVICE_GROUP=""

timestamp() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

emit() {
  local level="$1" line
  shift
  line="$(timestamp) [$level] $*"
  mkdir -p "$SETUP_STATE_DIR"
  printf '%s\n' "$line" >> "$LOG_FILE"
  printf '%s\n' "$line"
}

die() {
  emit "LỖI" "Bước '$CURRENT_STEP' thất bại: $*" >&2
  exit 1
}

on_error() {
  local rc="$1" line="$2"
  [ "$rc" -ne 0 ] || return 0
  emit "LỖI" "Bước '$CURRENT_STEP' dừng ở dòng $line (mã lỗi $rc). Log: $LOG_FILE" >&2
}

launcher_structure_check() {
  local candidate="$1"
  local marker
  [ -s "$candidate" ] || return 1
  [ "$(wc -c < "$candidate")" -le 1048576 ] || return 1
  IFS= read -r marker < "$candidate" || return 1
  [ "$marker" = '#!/data/data/com.termux/files/usr/bin/bash' ] || return 1
  grep -Fqx 'AOTSETUP_LOCAL_LAUNCHER_V1=1' "$candidate" || return 1
  grep -Fqx 'install_local_launcher() {' "$candidate" || return 1
  grep -Fqx 'update_local_launcher() {' "$candidate" || return 1
  grep -Fqx 'identity_tool() {' "$candidate" || return 1
  grep -Fqx 'resume_pending_migration() {' "$candidate" || return 1
}

install_candidate() {
  local candidate="$1" target="$2" stage
  [ -s "$candidate" ] || die "Launcher nguồn bị rỗng."
  bash -n "$candidate" || die "Launcher nguồn sai cú pháp."
  launcher_structure_check "$candidate" || die "Launcher nguồn sai cấu trúc."
  mkdir -p "$(dirname "$target")"
  if [ -f "$target" ] && [ ! -L "$target" ] &&
     command -v cmp >/dev/null 2>&1 && cmp -s "$candidate" "$target"; then
    chmod 700 "$target"
    return 0
  fi
  if [ -e "$target" ] || [ -L "$target" ]; then
    [ -f "$target" ] && [ ! -L "$target" ] || die "Target launcher không phải regular file: $target"
  fi
  stage="$(mktemp "$(dirname "$target")/.aotsetup.install.XXXXXX")" || die "Không tạo được launcher tạm."
  cp -p "$candidate" "$stage" || {
    rm -f "$stage"
    die "Không copy được launcher tạm."
  }
  bash -n "$stage" && launcher_structure_check "$stage" || {
    rm -f "$stage"
    die "Launcher tạm không qua gate."
  }
  chmod 700 "$stage"
  mv -f "$stage" "$target" || {
    rm -f "$stage"
    die "Không thay launcher atomic."
  }
  [ -x "$target" ] && bash -n "$target" && launcher_structure_check "$target" ||
    die "Postcondition launcher thất bại."
}

install_local_launcher() {
  CURRENT_STEP="install-local-launcher"
  install_candidate "$1" "$LAUNCHER"
}

update_local_launcher() {
  local stage source=""
  CURRENT_STEP="update-local-launcher"
  if ! command -v curl >/dev/null 2>&1; then
    command -v pkg >/dev/null 2>&1 || die "Thiếu curl và pkg."
    pkg install -y curl </dev/null || die "Không cài được curl cho update."
    hash -r
  fi
  stage="$(mktemp "$(dirname "$LAUNCHER")/.aotsetup.update.XXXXXX")" || die "Không tạo được file update tạm."
  if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] &&
     [ -n "${AOTSCRIPT_SETUP_UPDATE_SOURCE:-}" ]; then
    source="$AOTSCRIPT_SETUP_UPDATE_SOURCE"
    cp -p "$source" "$stage" || {
      rm -f "$stage"
      die "Không copy được update fixture."
    }
  else
    curl -fsSL --retry 3 --connect-timeout 15 </dev/null \
      "$MAIN_SETUP_URL?t=$(date +%s)" -o "$stage" || {
        rm -f "$stage"
        die "Không tải được setup.sh mới từ main."
      }
  fi
  [ -s "$stage" ] || {
    rm -f "$stage"
    die "setup.sh update bị rỗng."
  }
  bash -n "$stage" && launcher_structure_check "$stage" || {
    rm -f "$stage"
    die "setup.sh update không qua syntax/structure gate."
  }
  install_candidate "$stage" "$LAUNCHER"
  rm -f "$stage"
  emit OK "aotsetup đã update từ main; lần chạy sau dùng bản local mới."
}

process_start_time() {
  local pid="$1"
  [ -r "/proc/$pid/stat" ] || return 1
  awk '{print $22}' "/proc/$pid/stat" 2>/dev/null
}

process_uid() {
  local pid="$1"
  [ -r "/proc/$pid/status" ] || return 1
  awk '/^Uid:/ {print $2; exit}' "/proc/$pid/status" 2>/dev/null
}

process_is_running() {
  local pid="$1" state
  kill -0 "$pid" 2>/dev/null || return 1
  state="$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)"
  [ -n "$state" ] && [ "$state" != Z ]
}

lock_read_field() {
  local field="$1"
  [ -s "$LOCK_DIR/$field" ] || return 0
  tr -d '\r\n' < "$LOCK_DIR/$field"
}

lock_write_field() {
  local field="$1" value="$2" tmp
  tmp="$LOCK_DIR/.$field.tmp.$$"
  printf '%s\n' "$value" > "$tmp"
  chmod 600 "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$LOCK_DIR/$field"
}

lock_remove_stale() {
  rm -f \
    "$LOCK_DIR/pid" "$LOCK_DIR/start_time" "$LOCK_DIR/state" \
    "$LOCK_DIR/uid" "$LOCK_DIR/state_dir" "$LOCK_DIR/script_path" \
    "$LOCK_DIR/identity" "$LOCK_DIR"/.*.tmp.* 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || die "Không dọn được lock cũ an toàn."
}

process_matches_setup_owner() {
  local pid="$1" expected_script="$2" expected_state_dir="$3"
  local proc_uid cmdline proc_home="" proc_xdg="" proc_state_dir=""
  [ -r "/proc/$pid/status" ] && [ -r "/proc/$pid/cmdline" ] &&
    [ -r "/proc/$pid/environ" ] || return 1
  proc_uid="$(process_uid "$pid" 2>/dev/null || true)"
  [ "$proc_uid" = "$(process_uid "$$" 2>/dev/null || true)" ] || return 1
  cmdline="$(tr '\0' '\n' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  [ -n "$cmdline" ] || return 1
  if [ -n "$expected_script" ]; then
    printf '%s\n' "$cmdline" | grep -Fx -- "$expected_script" >/dev/null || return 1
  else
    printf '%s\n' "$cmdline" |
      grep -E '(^|/)(aotsetup|setup[.]sh)$' >/dev/null || return 1
  fi
  proc_home="$(
    tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null |
      sed -n 's/^HOME=//p' | head -n 1
  )"
  proc_xdg="$(
    tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null |
      sed -n 's/^XDG_STATE_HOME=//p' | head -n 1
  )"
  if [ -n "$proc_xdg" ]; then
    proc_state_dir="$proc_xdg/aotscript/setup-driver"
  elif [ -n "$proc_home" ]; then
    proc_state_dir="$proc_home/.local/state/aotscript/setup-driver"
  fi
  [ "$proc_state_dir" = "$expected_state_dir" ]
}

lock_set_state() {
  local state="$1" owner start
  [ "$LOCK_HELD" = 1 ] || return 0
  owner="$(lock_read_field pid)"
  start="$(lock_read_field start_time)"
  [ "$owner" = "$$" ] && [ "$start" = "$LOCK_START_TIME" ] || return 0
  lock_write_field state "$state"
}

release_lock() {
  local owner="" start=""
  [ "$LOCK_HELD" = 1 ] || return 0
  owner="$(lock_read_field pid)"
  start="$(lock_read_field start_time)"
  if [ "$owner" = "$$" ] && [ "$start" = "$LOCK_START_TIME" ]; then
    rm -f \
      "$LOCK_DIR/pid" "$LOCK_DIR/start_time" "$LOCK_DIR/state" \
      "$LOCK_DIR/uid" "$LOCK_DIR/state_dir" "$LOCK_DIR/script_path" \
      "$LOCK_DIR/identity" "$LOCK_DIR"/.*.tmp.* 2>/dev/null || true
    rmdir "$LOCK_DIR" 2>/dev/null || true
  fi
  LOCK_HELD=0
}

acquire_lock() {
  local owner="" stored_start="" actual_start="" owner_state="" owner_script=""
  local owner_state_dir="" attempt
  CURRENT_STEP="lock"
  mkdir -p "$SETUP_STATE_DIR"
  LOCK_START_TIME="$(process_start_time "$$")" || die "Không đọc được process start-time."
  LOCK_SCRIPT_PATH="$(readlink -f "$0" 2>/dev/null || printf '%s' "$0")"
  for attempt in {1..40}; do
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      lock_write_field start_time "$LOCK_START_TIME"
      lock_write_field state RUNNING_STEP
      lock_write_field uid "$(process_uid "$$")"
      lock_write_field state_dir "$SETUP_STATE_DIR"
      lock_write_field script_path "$LOCK_SCRIPT_PATH"
      lock_write_field identity AOTSETUP_LOCK_V2
      lock_write_field pid "$$"
      LOCK_HELD=1
      return 0
    fi
    owner="$(lock_read_field pid)"
    if [ -z "$owner" ] && [ "$attempt" -le 10 ]; then
      sleep 0.05
      continue
    fi
    if ! [[ "$owner" =~ ^[0-9]+$ ]] || ! process_is_running "$owner"; then
      lock_remove_stale
      continue
    fi
    actual_start="$(process_start_time "$owner" 2>/dev/null || true)"
    stored_start="$(lock_read_field start_time)"
    owner_state="$(lock_read_field state)"
    owner_script="$(lock_read_field script_path)"
    owner_state_dir="$(lock_read_field state_dir)"
    if [ -n "$stored_start" ] && [ "$stored_start" != "$actual_start" ]; then
      lock_remove_stale
      continue
    fi
    if [ -z "$stored_start" ] || [ -z "$owner_script" ] ||
       [ -z "$owner_state_dir" ]; then
      if process_matches_setup_owner "$owner" "" "$SETUP_STATE_DIR"; then
        die "Một phiên aotsetup cũ có metadata thiếu đang chạy (PID=$owner); không thể tiếp quản an toàn."
      fi
      lock_remove_stale
      continue
    fi
    if [ "$owner_state_dir" != "$SETUP_STATE_DIR" ] ||
       ! process_matches_setup_owner "$owner" "$owner_script" "$owner_state_dir"; then
      lock_remove_stale
      continue
    fi
    case "$owner_state" in
      WAITING_INPUT)
        emit INFO "Tiếp quản phiên aotsetup đang chờ nhập (PID=$owner)."
        kill -TERM "$owner" 2>/dev/null ||
          die "Không gửi được TERM tới phiên chờ nhập đã xác minh (PID=$owner)."
        for _ in {1..30}; do
          if ! process_is_running "$owner" ||
             [ "$(process_start_time "$owner" 2>/dev/null || true)" != "$stored_start" ]; then
            break
          fi
          sleep 0.1
        done
        if process_is_running "$owner" &&
           [ "$(process_start_time "$owner" 2>/dev/null || true)" = "$stored_start" ]; then
          die "Phiên chờ nhập PID=$owner không dừng sau TERM; lock được giữ nguyên."
        fi
        [ -d "$LOCK_DIR" ] && lock_remove_stale
        ;;
      RUNNING_STEP|STARTING)
        die "Một phiên aotsetup khác đang chạy (PID=$owner, trạng thái=$owner_state)."
        ;;
      *)
        die "Phiên aotsetup PID=$owner có trạng thái lock không hợp lệ; không tự kết thúc."
        ;;
    esac
  done
  die "Không lấy được lock sau khi xử lý cạnh tranh."
}

terminal_prepare() {
  [ "$TERMINAL_OPEN" = 1 ] || {
    if ! exec {TERMINAL_FD}<>/dev/tty; then
      die "Đây là phiên tương tác nhưng không mở được /dev/tty. Hãy chạy aotsetup trong cửa sổ Termux đang hoạt động."
    fi
    TERMINAL_OPEN=1
    TERMINAL_SAVED_STATE="$(stty -g <&"$TERMINAL_FD" 2>/dev/null || true)"
  }
  stty sane echo <&"$TERMINAL_FD" 2>/dev/null || true
}

terminal_restore() {
  [ "$TERMINAL_OPEN" = 1 ] || return 0
  if [ -n "$TERMINAL_SAVED_STATE" ]; then
    stty "$TERMINAL_SAVED_STATE" <&"$TERMINAL_FD" 2>/dev/null || true
  fi
  stty echo <&"$TERMINAL_FD" 2>/dev/null || true
  exec {TERMINAL_FD}>&- 2>/dev/null || true
  TERMINAL_OPEN=0
}

cleanup() {
  terminal_restore
  release_lock
}

state_read() {
  local key="$1"
  [ -s "$SETUP_STATE_DIR/$key" ] || return 0
  tr -d '\r\n' < "$SETUP_STATE_DIR/$key"
}

state_write() {
  local key="$1" value="$2" tmp
  mkdir -p "$SETUP_STATE_DIR"
  tmp="$SETUP_STATE_DIR/.$key.tmp.$$"
  printf '%s\n' "$value" > "$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$SETUP_STATE_DIR/$key"
}

normalize_device_id() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
  [[ "$value" =~ ^m[1-9][0-9]{0,5}$ ]] || return 1
  printf '%s\n' "$value"
}

normalize_group() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  value="$(printf '%s' "$value" | tr '[:lower:]' '[:upper:]')"
  case "$value" in
    NOVA|MARMOT) printf '%s\n' "$value" ;;
    *) return 1 ;;
  esac
}

prompt_value() {
  local kind="$1" prompt="$2" value=""
  if [ "${AOTSCRIPT_SETUP_INPUT_MODE:-tty}" = env ]; then
    [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] ||
      die "Input qua biến môi trường chỉ được phép trong self-test."
    case "$kind" in
      DEVICE_ID) value="${AOTSCRIPT_SETUP_DEVICE_ID:-}" ;;
      GROUP) value="${AOTSCRIPT_SETUP_GROUP:-}" ;;
      CONFIRM) value="${AOTSCRIPT_SETUP_CONFIRM:-}" ;;
      CHECKPOINT) value="${AOTSCRIPT_SETUP_CHECKPOINT_ACTION:-}" ;;
    esac
  fi
  if [ -z "$value" ]; then
    terminal_prepare
    lock_set_state WAITING_INPUT
    printf '%s' "$prompt" >&"$TERMINAL_FD"
    if ! IFS= read -r -u "$TERMINAL_FD" value; then
      lock_set_state RUNNING_STEP
      die "Không đọc được câu trả lời từ terminal điều khiển."
    fi
    lock_set_state RUNNING_STEP
  fi
  PROMPT_RESULT="$value"
}

confirm_once() {
  local message="$1" answer normalized
  prompt_value CONFIRM "$message [y/N]: "
  answer="$PROMPT_RESULT"
  normalized="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
  case "$normalized" in
    y|yes|co|có) return 0 ;;
    *) die "Chưa xác nhận thao tác." ;;
  esac
}

ensure_packages() {
  local package command
  local missing=() packages=(curl python unzip zip)
  CURRENT_STEP="packages"
  if [ "${AOTSCRIPT_SETUP_DRY_RUN:-0}" = 1 ]; then
    emit INFO "DRY-RUN: bỏ qua package mutation."
    return 0
  fi
  for package in "${packages[@]}"; do
    command -v "$package" >/dev/null 2>&1 || missing+=("$package")
  done
  command -v sha256sum >/dev/null 2>&1 || missing+=(coreutils)
  if [ "${#missing[@]}" -gt 0 ]; then
    command -v pkg >/dev/null 2>&1 || die "Thiếu pkg trong Termux."
    emit INFO "Cài package còn thiếu: ${missing[*]}"
    pkg install -y "${missing[@]}" </dev/null || die "pkg install thất bại."
    hash -r
  fi
  for command in curl python unzip zip sha256sum; do
    command -v "$command" >/dev/null 2>&1 || die "Thiếu command $command."
  done
  state_write packages_done yes
}

host_fingerprint() {
  local raw="" android_id="" serial="" token_file="" token="" token_tmp=""
  local strategy_file="$SETUP_STATE_DIR/host_fingerprint_strategy"
  if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] ||
     [ "${AOTSCRIPT_SETUP_DRY_RUN:-0}" = 1 ]; then
    raw="${AOTSCRIPT_SETUP_HOST_ID:-test-host}"
  else
    # If device is already bound, honour the strategy used at bind time
    # to prevent root-availability changes from generating a different hash.
    local existing_strategy=""
    if [ -f "$strategy_file" ]; then
      existing_strategy="$(cat "$strategy_file" 2>/dev/null | tr -d '\r\n ' || true)"
    fi
    if [ "$existing_strategy" = "token" ]; then
      # Was bound using token fallback — continue using it even if root now available
      android_id=""
      serial=""
    else
      android_id="$(su -c 'settings get secure android_id' </dev/null 2>/dev/null | tr -d '\r\n ' || true)"
      serial="$(su -c 'getprop ro.boot.serialno' </dev/null 2>/dev/null | tr -d '\r\n ' || true)"
      case "$android_id" in null|unknown|"") android_id="" ;; esac
      case "$serial" in null|unknown|"") serial="" ;; esac
    fi
    if [ -n "$android_id$serial" ]; then
      raw="$android_id|$serial"
      # Persist strategy if not yet recorded
      if [ -z "$existing_strategy" ]; then
        mkdir -p "$SETUP_STATE_DIR"
        printf 'strong\n' > "$strategy_file"
      fi
    else
      token_file="$SETUP_STATE_DIR/host_token"
      if [ -f "$token_file" ]; then
        token="$(cat "$token_file" 2>/dev/null | tr -d '\r\n ' || true)"
        if [ ${#token} -lt 8 ]; then
          token=""
          rm -f "$token_file"
        fi
      fi
      if [ -z "$token" ]; then
        token="$(python -c 'import uuid, time; print(f"{uuid.uuid4().hex}-{int(time.time()*1000)}")' 2>/dev/null || date +%s%N 2>/dev/null || date +%s)-$$"
        mkdir -p "$SETUP_STATE_DIR"
        token_tmp="${token_file}.tmp.$$"
        printf '%s\n' "$token" > "$token_tmp"
        mv -f "$token_tmp" "$token_file"
      fi
      [ -n "$token" ] || die "Không tạo được token host bền vững."
      raw="token:$token"
      # Persist strategy if not yet recorded
      if [ -z "$existing_strategy" ]; then
        mkdir -p "$SETUP_STATE_DIR"
        printf 'token\n' > "$strategy_file"
      fi
    fi
  fi
  [ -n "$raw" ] || die "Không tạo được fingerprint ổn định cho máy hiện tại."
  printf '%s' "$raw" | sha256sum | awk '{print $1}'
}

test_su_stdin_isolation() {
  [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] || return 0
  [ "${AOTSCRIPT_SETUP_TEST_SU_STDIN_PROBE:-0}" = 1 ] || return 0
  su -c true </dev/null || die "Self-test su stdin isolation thất bại."
}

identity_tool() {
  python - "$STATE_BASE" "$SETUP_STATE_DIR" "$STORAGE_ROOT" \
    "$PROVISION_REF" "$PROVISION_SHA256" "$PROVISION_VERSION" "$@" <<'PY'
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys

state_base = pathlib.Path(sys.argv[1])
setup_dir = pathlib.Path(sys.argv[2])
storage = pathlib.Path(sys.argv[3])
provision_ref = sys.argv[4]
provision_sha = sys.argv[5]
provision_version = sys.argv[6]
command = sys.argv[7]
args = sys.argv[8:]
shouko = storage / "Download" / "Shouko"
mprovision = state_base / "mprovision.json"
journal_path = setup_dir / "clone-migration.json"
id_re = re.compile(r"m[1-9][0-9]{0,5}")
hash_re = re.compile(r"[0-9a-f]{64}")
valid_groups = {"NOVA", "MARMOT"}


def fail(message, code=20):
    print(f"IDENTITY_ERROR={message}")
    raise SystemExit(code)


def read_text(path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        fail(f"cannot_read:{path}:{type(exc).__name__}")


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"invalid_json:{path}:{type(exc).__name__}")
    if not isinstance(value, dict):
        fail(f"invalid_json_root:{path}")
    return value


def valid_id(value, path):
    value = str(value or "").strip().lower()
    if not id_re.fullmatch(value):
        fail(f"invalid_device_id:{path}")
    return value


def valid_group(value, path):
    value = str(value or "").strip().upper()
    if value not in valid_groups:
        fail(f"invalid_device_group:{path}")
    return value


def atomic_text(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temp.write_text(value, encoding="utf-8")
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def source_snapshot():
    entries = []
    setup_id = setup_dir / "device_id"
    setup_group = setup_dir / "device_group"
    if setup_id.exists() != setup_group.exists():
        fail(f"incomplete_identity_pair:{setup_dir}")
    if setup_id.exists():
        entries.append(("setup-driver", setup_id, setup_group,
                        valid_id(read_text(setup_id), setup_id),
                        valid_group(read_text(setup_group), setup_group)))

    phase = ""
    if mprovision.exists():
        data = read_json(mprovision)
        for key in ("device_id", "device_group", "phase"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                fail(f"missing_mprovision_field:{mprovision}:{key}")
        phase = data["phase"].strip()
        entries.append(("mprovision", mprovision, mprovision,
                        valid_id(data["device_id"], mprovision),
                        valid_group(data["device_group"], mprovision)))

    shouko_id = shouko / "device_id.txt"
    shouko_group = shouko / "device_group.txt"
    if shouko_id.exists() and shouko_group.exists():
        entries.append(("shouko", shouko_id, shouko_group,
                        valid_id(read_text(shouko_id), shouko_id),
                        valid_group(read_text(shouko_group), shouko_group)))
    elif shouko_id.exists() != shouko_group.exists():
        if setup_id.exists():
            auth_id = valid_id(read_text(setup_id), setup_id)
            auth_group = valid_group(read_text(setup_group), setup_group)
            atomic_text(shouko_id, auth_id + "\n")
            atomic_text(shouko_group, auth_group + "\n")
            entries.append(("shouko", shouko_id, shouko_group, auth_id, auth_group))
        elif not entries:
            pass
        else:
            fail(f"incomplete_identity_pair:{shouko}")
    return entries, phase


def inspect(host_hash):
    if not hash_re.fullmatch(host_hash):
        fail("invalid_host_fingerprint")
    entries, phase = source_snapshot()
    binding = setup_dir / "host_fingerprint"
    bound = ""
    if binding.exists():
        bound = read_text(binding)
        if not hash_re.fullmatch(bound):
            fail(f"invalid_host_binding:{binding}")
    if not entries:
        if bound:
            fail(f"host_binding_without_identity:{binding}")
        print("IDENTITY_STATUS=FRESH")
        return
    ids = {entry[3] for entry in entries}
    groups = {entry[4] for entry in entries}
    if len(ids) != 1 or len(groups) != 1:
        print("IDENTITY_STATUS=CONFLICT")
        for name, id_path, group_path, device_id, group in entries:
            print(f"IDENTITY_CONFLICT={name}|{id_path}|{device_id}|{group_path}|{group}")
        raise SystemExit(21)
    device_id = next(iter(ids))
    group = next(iter(groups))
    for name, id_path, group_path, _, _ in entries:
        print(f"IDENTITY_SOURCE={name}|{id_path}|{group_path}")
    print(f"SOURCE_ID={device_id}")
    print(f"SOURCE_GROUP={group}")
    print(f"MPROVISION_PHASE={phase}")
    if bound == host_hash:
        print("IDENTITY_STATUS=BOUND_CURRENT")
    else:
        print("IDENTITY_STATUS=NEEDS_CONFIRM")


def bind(device_id, group, host_hash):
    valid_id(device_id, "input")
    valid_group(group, "input")
    if not hash_re.fullmatch(host_hash):
        fail("invalid_host_fingerprint")
    atomic_text(setup_dir / "device_id", device_id + "\n")
    atomic_text(setup_dir / "device_group", group + "\n")
    atomic_text(setup_dir / "host_fingerprint", host_hash + "\n")
    shouko.mkdir(parents=True, exist_ok=True)
    atomic_text(shouko / "device_id.txt", device_id + "\n")
    atomic_text(shouko / "device_group.txt", group + "\n")
    print("IDENTITY_BIND=OK")


def validate_agent_config(path):
    data = read_json(path)
    for key in ("worker_report_url", "agent_report_secret"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            fail(f"invalid_agent_config:{path}:{key}")


def plan(old_id, new_id, new_group, host_hash):
    valid_id(old_id, "old_id")
    valid_id(new_id, "new_id")
    valid_group(new_group, "new_group")
    if old_id == new_id or not hash_re.fullmatch(host_hash):
        fail("invalid_clone_plan")
    entries, phase = source_snapshot()
    names = {entry[0] for entry in entries}
    if names not in ({"setup-driver", "mprovision", "shouko"}, {"setup-driver", "shouko"}):
        fail("clone_requires_all_identity_sources")
    if any(entry[3] != old_id for entry in entries):
        fail("clone_source_changed")
    if mprovision.exists() and phase != "complete":
        fail(f"unsafe_mprovision_phase:{phase or 'missing'}")
    agent_path = storage / "Download" / "Agent_Core.py"
    config_path = shouko / "agent_config.json"
    if not agent_path.is_file() or agent_path.stat().st_size == 0:
        fail(f"missing_agent:{agent_path}")
    validate_agent_config(config_path)
    if journal_path.exists():
        fail(f"migration_journal_exists:{journal_path}")
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    backup = state_base / "foreign-state" / f"{stamp}-{old_id}-to-{new_id}"
    counter = 0
    while backup.exists():
        counter += 1
        backup = state_base / "foreign-state" / f"{stamp}-{old_id}-to-{new_id}-{counter}"
    backup.mkdir(parents=True, mode=0o700)
    journal = {
        "schema_version": 1,
        "stage": "planned",
        "source_id": old_id,
        "target_id": new_id,
        "target_group": new_group,
        "host_fingerprint": host_hash,
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "backup_dir": str(backup),
    }
    atomic_json(journal_path, journal)
    print(f"MIGRATION_BACKUP_DIR={backup}")
    print("MIGRATION_PLAN=OK")


def load_journal():
    if not journal_path.exists():
        fail("migration_journal_missing")
    data = read_json(journal_path)
    required = ("stage", "source_id", "target_id", "target_group",
                "host_fingerprint", "created_at", "backup_dir")
    for key in required:
        if not isinstance(data.get(key), str) or not data[key]:
            fail(f"migration_journal_field:{key}")
    valid_id(data["source_id"], "journal")
    valid_id(data["target_id"], "journal")
    valid_group(data["target_group"], "journal")
    if not hash_re.fullmatch(data["host_fingerprint"]):
        fail("migration_journal_host")
    return data


def save_journal(data, stage):
    data["stage"] = stage
    data["updated_at"] = datetime.datetime.now(
        datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    atomic_json(journal_path, data)


def journal_status(host_hash):
    if not journal_path.exists():
        print("MIGRATION_STATUS=NONE")
        return
    data = load_journal()
    if data["host_fingerprint"] != host_hash:
        fail("migration_host_changed")
    print(f"MIGRATION_STATUS={data['stage']}")
    print(f"MIGRATION_SOURCE_ID={data['source_id']}")
    print(f"MIGRATION_TARGET_ID={data['target_id']}")
    print(f"MIGRATION_TARGET_GROUP={data['target_group']}")
    print(f"MIGRATION_BACKUP_DIR={data['backup_dir']}")


def mark_agent_stopped():
    data = load_journal()
    if data["stage"] == "planned":
        save_journal(data, "agent_stopped")
    elif data["stage"] not in {"agent_stopped", "archived", "identity_applied", "complete"}:
        fail(f"invalid_migration_stage:{data['stage']}")


def archive_old_state():
    data = load_journal()
    if data["stage"] in {"archived", "identity_applied", "complete"}:
        print("MIGRATION_ARCHIVE=ALREADY_DONE")
        return
    if data["stage"] != "agent_stopped":
        fail(f"invalid_migration_stage:{data['stage']}")
    backup = pathlib.Path(data["backup_dir"])
    backup.mkdir(parents=True, exist_ok=True)
    sources = [
        ("setup-device-id", setup_dir / "device_id"),
        ("setup-device-group", setup_dir / "device_group"),
        ("setup-host-fingerprint", setup_dir / "host_fingerprint"),
        ("mprovision", mprovision),
        ("shouko-device-id", shouko / "device_id.txt"),
        ("shouko-device-group", shouko / "device_group.txt"),
        ("agent-state", shouko / "agent_state.json"),
        ("provision-report-json", shouko / "provision_report.json"),
        ("provision-report-text", shouko / "provision_report.txt"),
    ]
    manifest_entries = []
    for label, source in sources:
        if not source.exists():
            continue
        if not source.is_file():
            fail(f"archive_source_not_file:{source}")
        destination = backup / label
        shutil.copy2(source, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        if digest != hashlib.sha256(source.read_bytes()).hexdigest():
            fail(f"archive_hash_mismatch:{source}")
        manifest_entries.append({
            "source_path": str(source),
            "archive_path": str(destination),
            "sha256": digest,
        })
    manifest = {
        "schema_version": 1,
        "source_id": data["source_id"],
        "target_id": data["target_id"],
        "target_group": data["target_group"],
        "created_at": data["created_at"],
        "files": manifest_entries,
    }
    atomic_json(backup / "manifest.json", manifest)
    save_journal(data, "archived")
    print("MIGRATION_ARCHIVE=OK")


def apply_identity():
    data = load_journal()
    if data["stage"] in {"identity_applied", "complete"}:
        print("MIGRATION_IDENTITY=ALREADY_APPLIED")
        return
    if data["stage"] != "archived":
        fail(f"invalid_migration_stage:{data['stage']}")
    new_id = data["target_id"]
    new_group = data["target_group"]
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    shouko.mkdir(parents=True, exist_ok=True)
    atomic_text(shouko / "device_id.txt", new_id + "\n")
    atomic_text(shouko / "device_group.txt", new_group + "\n")
    agent_state = {
        "device_group": new_group,
        "common_command_hash": "",
        "group_command_hash": "",
        "last_processed_at": "",
        "last_command_id": "",
        "processed_command_ids": [],
        "last_result": "",
        "setup_health": "unknown",
        "last_reconcile_at": "",
        "last_repair_result": "",
        "needs_manual_action": False,
    }
    atomic_json(shouko / "agent_state.json", agent_state)
    for report in (shouko / "provision_report.json", shouko / "provision_report.txt"):
        try:
            report.unlink()
        except FileNotFoundError:
            pass
    if mprovision.exists():
        new_mprovision = {
            "version": provision_version,
            "provision_ref": provision_ref,
            "device_id": new_id,
            "device_group": new_group,
            "phase": "complete",
            "run_id": f"clone-{data['created_at'].replace(':', '').replace('-', '')}-{new_id}",
            "backup_before": "",
            "backup_before_remote": "",
            "backup_after": "",
            "backup_after_remote": "",
            "swift_install": "1",
            "wizard_step": "",
            "manual_pre_confirmed_at": "",
            "manual_post_confirmed_at": "",
            "completed_at": now,
            "report_remote": "",
            "report_json": "",
            "report_text": "",
            "publish_next_status": "",
            "publish_next_started_at": "",
            "publish_next_completed_at": "",
            "publish_next_failed_step": "",
            "publish_next_history_remote": "",
            "publish_next_shouko_sha256": "",
            "publish_next_delta_sha256": "",
            "clone_source_device_id": data["source_id"],
            "identity_migrated_at": now,
        }
        atomic_json(mprovision, new_mprovision)
    atomic_text(setup_dir / "device_id", new_id + "\n")
    atomic_text(setup_dir / "device_group", new_group + "\n")
    atomic_text(setup_dir / "host_fingerprint", data["host_fingerprint"] + "\n")
    atomic_text(setup_dir / "provision_initialized", "yes\n")
    atomic_text(setup_dir / "setup_complete", "yes\n")
    atomic_text(setup_dir / "wizard_started", "yes\n")
    atomic_text(setup_dir / "bootstrap_ui_done", "yes\n")
    atomic_text(setup_dir / "provision_ref", provision_ref + "\n")
    atomic_text(setup_dir / f"provision-device-{provision_ref}.sh.sha256", provision_sha + "\n")
    save_journal(data, "identity_applied")
    print("MIGRATION_IDENTITY=OK")


def mark_complete():
    data = load_journal()
    if data["stage"] == "identity_applied":
        save_journal(data, "complete")
    elif data["stage"] != "complete":
        fail(f"invalid_migration_stage:{data['stage']}")
    print("MIGRATION_COMPLETE=YES")


dispatch = {
    "inspect": lambda: inspect(args[0]),
    "bind": lambda: bind(*args),
    "plan": lambda: plan(*args),
    "journal-status": lambda: journal_status(args[0]),
    "mark-agent-stopped": mark_agent_stopped,
    "archive": archive_old_state,
    "apply": apply_identity,
    "complete": mark_complete,
}
if command not in dispatch:
    fail(f"unknown_identity_command:{command}")
dispatch[command]()
PY
}

identity_call() {
  local output
  if output="$(identity_tool "$@" 2>&1)"; then
    printf '%s\n' "$output"
    return 0
  fi
  printf '%s\n' "$output" >&2
  return 1
}

list_agent_pids() {
  python - <<'PY'
import os
import pathlib

for entry in os.listdir("/proc"):
    if not entry.isdigit():
        continue
    try:
        args = [part.decode("utf-8", errors="replace") for part in
                pathlib.Path(f"/proc/{entry}/cmdline").read_bytes().split(b"\0") if part]
    except Exception:
        continue
    if any(arg.endswith("/Download/Agent_Core.py") for arg in args):
        print(entry)
PY
}

stop_agent_safely() {
  local pid
  local pids=() remaining=()
  if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ]; then
    state_write test_agent_stopped yes
    return 0
  fi
  mapfile -t pids < <(list_agent_pids)
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    mapfile -t remaining < <(list_agent_pids)
    [ "${#remaining[@]}" -eq 0 ] && return 0
    sleep 1
  done
  die "Agent cũ chưa dừng; identity chưa được đổi."
}

start_agent_safely() {
  local pid
  local pids=()
  if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ]; then
    state_write test_agent_started yes
    return 0
  fi
  [ -s "$AGENT_PATH" ] || die "Thiếu Agent_Core.py sau migration."
  python - "$AGENT_PATH" "$AGENT_CONFIG" <<'PY'
import json
import pathlib
import sys

agent = pathlib.Path(sys.argv[1])
compile(agent.read_text(encoding="utf-8"), str(agent), "exec")
config = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
if not isinstance(config, dict):
    raise SystemExit(1)
for key in ("worker_report_url", "agent_report_secret"):
    if not isinstance(config.get(key), str) or not config[key].strip():
        raise SystemExit(1)
PY
  mapfile -t pids < <(list_agent_pids)
  [ "${#pids[@]}" -eq 0 ] || die "Agent đã chạy trước restart post-migration."
  nohup python -u "$AGENT_PATH" >> "$AGENT_LOG" 2>&1 &
  pid=$!
  sleep 2
  kill -0 "$pid" 2>/dev/null || die "Agent mới thoát sau migration."
  mapfile -t pids < <(list_agent_pids)
  [ "${#pids[@]}" -eq 1 ] || die "Agent post-migration không đúng một tiến trình."
}

resume_pending_migration() {
  local host_hash="$1" output stage source_id target_id
  output="$(identity_call journal-status "$host_hash")" || die "Journal migration không hợp lệ."
  stage="$(printf '%s\n' "$output" | awk -F= '$1=="MIGRATION_STATUS" {print $2}')"
  [ "$stage" != NONE ] || return 1
  [ "$stage" != complete ] || return 1
  source_id="$(printf '%s\n' "$output" | awk -F= '$1=="MIGRATION_SOURCE_ID" {print $2}')"
  target_id="$(printf '%s\n' "$output" | awk -F= '$1=="MIGRATION_TARGET_ID" {print $2}')"
  emit INFO "Resume migration $source_id → $target_id tại stage=$stage"
  if [ "$stage" = planned ]; then
    CURRENT_STEP="clone-stop-agent"
    stop_agent_safely
    identity_call mark-agent-stopped >/dev/null || die "Không journal được agent_stopped."
    stage=agent_stopped
  fi
  if [ "$stage" = agent_stopped ]; then
    CURRENT_STEP="clone-archive"
    identity_call archive >/dev/null || die "Không archive được foreign state."
    if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] &&
       [ "${AOTSCRIPT_SETUP_INTERRUPT_AFTER:-}" = archive ]; then
      emit WARN "TEST: ngắt có chủ đích sau archive."
      return 75
    fi
    stage=archived
  fi
  if [ "$stage" = archived ]; then
    CURRENT_STEP="clone-identity"
    identity_call apply >/dev/null || die "Không apply được identity mới."
    stage=identity_applied
  fi
  if [ "$stage" = identity_applied ]; then
    CURRENT_STEP="clone-start-agent"
    start_agent_safely
    identity_call complete >/dev/null || die "Không complete được journal migration."
  fi
  emit OK "Migration identity đã hoàn tất và Agent lifecycle đã restart."
  return 0
}

download_provision() {
  local target sidecar stage actual_sha
  target="$SETUP_STATE_DIR/provision-device-$PROVISION_REF.sh"
  sidecar="$target.sha256"
  if [ -s "$target" ] && [ "$(cat "$sidecar" 2>/dev/null || true)" = "$PROVISION_SHA256" ] &&
     [ "$(sha256sum "$target" | awk '{print $1}')" = "$PROVISION_SHA256" ] && bash -n "$target"; then
    printf '%s\n' "$target"
    return 0
  fi
  stage="$SETUP_STATE_DIR/.provision-device.tmp.$$"
  rm -f "$stage"
  curl -fsSL --retry 3 --connect-timeout 15 </dev/null \
    "$RAW_BASE/provision-device.sh?t=$(date +%s)" -o "$stage" || {
      rm -f "$stage"
      die "Không tải được provision-device.sh revision pin."
    }
  [ -s "$stage" ] && bash -n "$stage" || {
    rm -f "$stage"
    die "Provision tải về rỗng hoặc sai cú pháp."
  }
  actual_sha="$(sha256sum "$stage" | awk '{print $1}')"
  [ "$actual_sha" = "$PROVISION_SHA256" ] || {
    rm -f "$stage"
    die "Provision SHA-256 không khớp."
  }
  chmod 700 "$stage"
  mv -f "$stage" "$target"
  state_write "provision-device-$PROVISION_REF.sh.sha256" "$PROVISION_SHA256"
  printf '%s\n' "$target"
}

read_mprovision_phase() {
  python - "$MPROVISION_STATE" "$1" "$2" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data, dict):
    raise SystemExit(1)
if data.get("device_id") != sys.argv[2] or data.get("device_group") != sys.argv[3]:
    raise SystemExit(1)
phase = data.get("phase")
if not isinstance(phase, str) or not phase:
    raise SystemExit(1)
print(phase)
PY
}

bootstrap_checkpoint() {
  state_write bootstrap_ui_done yes
}

run_aot_setup() {
  local device_id="$1" group="$2" msetup_script
  CURRENT_STEP="setup-aot"
  if [ "${AOTSCRIPT_SETUP_DRY_RUN:-0}" = 1 ]; then
    state_write provision_initialized yes
    state_write setup_complete yes
    emit OK "DRY-RUN: AOT setup hoàn tất."
    return 0
  fi
  if [ -n "${AOTSCRIPT_SETUP_M166_SOURCE:-}" ] && [ -f "${AOTSCRIPT_SETUP_M166_SOURCE:-}" ]; then
    msetup_script="$AOTSCRIPT_SETUP_M166_SOURCE"
    bash -n "$msetup_script" || die "setup-m166.sh tải về sai cú pháp."
  else
    msetup_script="$(mktemp "$SETUP_STATE_DIR/.setup-m166.XXXXXX")"
    curl -fsSL --retry 3 --connect-timeout 15 \
      "$RAW_BASE/setup-m166.sh?t=$(date +%s)" -o "$msetup_script" || {
        rm -f "$msetup_script"
        die "Không tải được setup-m166.sh."
      }
    [ -s "$msetup_script" ] || {
      rm -f "$msetup_script"
      die "setup-m166.sh tải về bị rỗng."
    }
    bash -n "$msetup_script" || {
      rm -f "$msetup_script"
      die "setup-m166.sh tải về sai cú pháp."
    }
  fi
  AOTSCRIPT_PROVISION_REF="$PROVISION_REF" bash "$msetup_script" "$device_id" "$group" </dev/null || {
    [ -n "${AOTSCRIPT_SETUP_M166_SOURCE:-}" ] || rm -f "$msetup_script"
    die "AOT msetup không hoàn tất."
  }
  [ -n "${AOTSCRIPT_SETUP_M166_SOURCE:-}" ] || rm -f "$msetup_script"
  state_write provision_initialized yes
  state_write setup_complete yes
}

choose_identity() {
  local host_hash="$1" output status source_id source_group raw_id raw_group device_id group resume_rc
  output="$(identity_call inspect "$host_hash")" || die "Identity sources không hợp lệ; không sửa state."
  status="$(printf '%s\n' "$output" | awk -F= '$1=="IDENTITY_STATUS" {print $2}')"
  source_id="$(printf '%s\n' "$output" | awk -F= '$1=="SOURCE_ID" {print $2}')"
  source_group="$(printf '%s\n' "$output" | awk -F= '$1=="SOURCE_GROUP" {print $2}')"
  if [ "$status" = BOUND_CURRENT ]; then
    SELECTED_DEVICE_ID="$source_id"
    SELECTED_DEVICE_GROUP="$source_group"
    return 0
  fi
  case "$status" in
    FRESH|NEEDS_CONFIRM) ;;
    *) die "Identity classification không an toàn: ${status:-EMPTY}." ;;
  esac
  prompt_value DEVICE_ID "Device ID hiện tại (ví dụ m74): "
  raw_id="$PROMPT_RESULT"
  device_id="$(normalize_device_id "$raw_id")" || die "Device ID không hợp lệ."
  prompt_value GROUP "Nhóm hiện tại (NOVA hoặc MARMOT): "
  raw_group="$PROMPT_RESULT"
  group="$(normalize_group "$raw_group")" || die "Nhóm không hợp lệ."
  if [ "$status" = FRESH ] || [ "$device_id" = "$source_id" ]; then
    if [ "$status" = NEEDS_CONFIRM ] && [ "$group" != "$source_group" ]; then
      die "Cùng Device ID nhưng nhóm khác source; không tự đổi group."
    fi
    confirm_once "Xác nhận identity $device_id / $group"
    identity_call bind "$device_id" "$group" "$host_hash" >/dev/null || die "Không bind được identity."
    SELECTED_DEVICE_ID="$device_id"
    SELECTED_DEVICE_GROUP="$group"
    return 0
  fi
  printf 'PHÁT HIỆN CLONE: %s → %s\n' "$source_id" "$device_id" >&2
  confirm_once "Xác nhận migration clone $source_id → $device_id / $group"
  identity_call plan "$source_id" "$device_id" "$group" "$host_hash" >/dev/null ||
    die "Clone state không đủ điều kiện migration an toàn."
  if resume_pending_migration "$host_hash"; then
    SELECTED_DEVICE_ID="$device_id"
    SELECTED_DEVICE_GROUP="$group"
    return 0
  else
    resume_rc=$?
  fi
  [ "$resume_rc" != 75 ] || return 75
  die "Không resume được migration vừa tạo."
}

main() {
  local self_path command host_hash device_id group migration_rc=0 choose_rc=0
  self_path="$0"
  command="${1:-}"
  case "$command" in
    --validate-id)
      [ "$#" = 2 ] || return 2
      normalize_device_id "$2"
      return
      ;;
    --validate-group)
      [ "$#" = 2 ] || return 2
      normalize_group "$2"
      return
      ;;
  esac
  install_local_launcher "$self_path"
  case "$command" in
    "") ;;
    --install-launcher-only)
      [ "$#" = 1 ] || die "--install-launcher-only không nhận tham số khác."
      emit OK "Launcher local đã được cài và xác minh."
      return 0
      ;;
    update)
      [ "$#" = 1 ] || die "aotsetup update không nhận tham số khác."
      update_local_launcher
      return 0
      ;;
    *) die "Chỉ hỗ trợ: aotsetup hoặc aotsetup update." ;;
  esac
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP
  trap 'on_error "$?" "$LINENO"' ERR
  acquire_lock
  if [ -n "${AOTSCRIPT_SETUP_HOLD_LOCK_SECONDS:-}" ]; then
    sleep "$AOTSCRIPT_SETUP_HOLD_LOCK_SECONDS"
  fi
  ensure_packages
  test_su_stdin_isolation
  host_hash="$(host_fingerprint)"
  if resume_pending_migration "$host_hash"; then
    migration_rc=0
  else
    migration_rc=$?
  fi
  case "$migration_rc" in
    0)
      device_id="$(state_read device_id)"
      group="$(state_read device_group)"
      ;;
    1)
      if choose_identity "$host_hash"; then
        device_id="$SELECTED_DEVICE_ID"
        group="$SELECTED_DEVICE_GROUP"
      else
        choose_rc=$?
        [ "$choose_rc" != 75 ] || return 75
        die "Không chọn được identity an toàn (rc=$choose_rc)."
      fi
      ;;
    75)
      return 75
      ;;
    *) die "Pending migration thất bại (rc=$migration_rc)." ;;
  esac
  state_write provision_ref "$PROVISION_REF"
  emit INFO "Identity hiện tại: $device_id / $group"
  bootstrap_checkpoint
  local is_complete=no
  if [ "$(state_read setup_complete)" = yes ]; then
    is_complete=yes
  elif [ -s "$MPROVISION_STATE" ]; then
    if [ "$(read_mprovision_phase "$device_id" "$group" 2>/dev/null || true)" = complete ]; then
      is_complete=yes
      state_write setup_complete yes
    fi
  fi
  if [ "$is_complete" = yes ]; then
    emit OK "Identity hợp lệ; workflow complete, không replay provision/backup/restore. Lần sau chỉ chạy aotsetup."
    return 0
  fi
  run_aot_setup "$device_id" "$group"
  emit OK "AOT setup hoàn tất. Lần sau chỉ chạy aotsetup."
}

if [ "${AOTSCRIPT_SETUP_SOURCE_ONLY:-0}" != 1 ] && [ "${BASH_SOURCE[0]}" = "$0" ]; then
  main "$@"
fi
