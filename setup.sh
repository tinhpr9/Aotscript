#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

AOTSETUP_LOCAL_LAUNCHER_V1=1
VERSION="one-command-setup-v2"
PROVISION_VERSION="phase22-aot-registration-v1"
# All provision/wizard/setup children use this exact tested revision.
PROVISION_REF="92439f16cd168dbf7b6cc2d48c88b5114062189e"
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
  local raw="" android_id="" serial=""
  if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] ||
     [ "${AOTSCRIPT_SETUP_DRY_RUN:-0}" = 1 ]; then
    raw="${AOTSCRIPT_SETUP_HOST_ID:-test-host}"
  else
    android_id="$(su -c 'settings get secure android_id' </dev/null 2>/dev/null | tr -d '\r\n ' || true)"
    serial="$(su -c 'getprop ro.boot.serialno' </dev/null 2>/dev/null | tr -d '\r\n ' || true)"
    case "$android_id" in null|unknown) android_id="" ;; esac
    case "$serial" in null|unknown) serial="" ;; esac
    [ -n "$android_id$serial" ] && raw="$android_id|$serial"
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
    s_id = None
    s_grp = None
    if setup_id.exists():
        s_id = valid_id(read_text(setup_id), setup_id)
    if setup_group.exists():
        s_grp = valid_group(read_text(setup_group), setup_group)
    if s_id or s_grp:
        entries.append(("setup-driver", setup_id, setup_group, s_id or "", s_grp or ""))

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
    sh_id = None
    sh_grp = None
    if shouko_id.exists():
        sh_id = valid_id(read_text(shouko_id), shouko_id)
    if shouko_group.exists():
        sh_grp = valid_group(read_text(shouko_group), shouko_group)
    if sh_id or sh_grp:
        entries.append(("shouko", shouko_id, shouko_group, sh_id or "", sh_grp or ""))
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
    ids = {entry[3] for entry in entries if entry[3]}
    groups = {entry[4] for entry in entries if entry[4]}
    if len(ids) > 1 or len(groups) > 1:
        print("IDENTITY_STATUS=CONFLICT")
        for name, id_path, group_path, device_id, group in entries:
            print(f"IDENTITY_CONFLICT={name}|{id_path}|{device_id}|{group_path}|{group}")
        raise SystemExit(21)
    device_id = next(iter(ids)) if ids else ""
    group = next(iter(groups)) if groups else ""
    for name, id_path, group_path, _, _ in entries:
        print(f"IDENTITY_SOURCE={name}|{id_path}|{group_path}")
    if device_id:
        print(f"SOURCE_ID={device_id}")
    if group:
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
    ids = {entry[3] for entry in entries if entry[3]}
    groups = {entry[4] for entry in entries if entry[4]}
    if len(ids) > 1 or len(groups) > 1:
        fail("clone_source_conflict", code=21)
    if ids and old_id not in ids:
        fail("clone_source_changed")
    if groups and new_group not in valid_groups:
        fail("invalid_device_group:new_group")

    names = {entry[0] for entry in entries}
    is_full = (
        names == {"setup-driver", "mprovision", "shouko"}
        and all(entry[3] == old_id and entry[4] for entry in entries)
    )

    agent_path = storage / "Download" / "Agent_Core.py"
    config_path = shouko / "agent_config.json"
    if not agent_path.is_file() or agent_path.stat().st_size == 0:
        fail(f"missing_agent:{agent_path}")
    validate_agent_config(config_path)
    if journal_path.exists():
        fail(f"migration_journal_exists:{journal_path}")

    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    mode = "full" if (is_full and phase == "complete") else "recovery"

    if mode == "full":
        if phase != "complete":
            fail(f"unsafe_mprovision_phase:{phase or 'missing'}")
        backup = state_base / "foreign-state" / f"{stamp}-{old_id}-to-{new_id}"
    else:
        backup = state_base / "foreign-state" / f"{stamp}-{old_id}-to-{new_id}-recovery"

    counter = 0
    base_backup = backup
    while backup.exists():
        counter += 1
        backup = pathlib.Path(str(base_backup) + f"-{counter}")
    backup.mkdir(parents=True, mode=0o700)

    # Create backup and manifest before any mutation
    manifest_entries = []
    sources = [
        ("setup-device-id", setup_dir / "device_id"),
        ("setup-device-group", setup_dir / "device_group"),
        ("setup-host-fingerprint", setup_dir / "host_fingerprint"),
        ("setup-provision-initialized", setup_dir / "provision_initialized"),
        ("setup-complete", setup_dir / "setup_complete"),
        ("setup-wizard-started", setup_dir / "wizard_started"),
        ("setup-bootstrap-ui-done", setup_dir / "bootstrap_ui_done"),
        ("mprovision", mprovision),
        ("shouko-device-id", shouko / "device_id.txt"),
        ("shouko-device-group", shouko / "device_group.txt"),
        ("shouko-agent-state", shouko / "agent_state.json"),
        ("shouko-provision-report-json", shouko / "provision_report.json"),
        ("shouko-provision-report-text", shouko / "provision_report.txt"),
        ("shouko-aot-group-config", shouko / "aot_group_config.json"),
    ]
    for label, source in sources:
        if not source.exists() or not source.is_file():
            continue
        destination = backup / label
        shutil.copy2(source, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        manifest_entries.append({
            "source_path": str(source),
            "archive_path": str(destination),
            "sha256": digest,
        })
    manifest = {
        "schema_version": 1,
        "mode": mode,
        "source_id": old_id,
        "target_id": new_id,
        "target_group": new_group,
        "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": manifest_entries,
    }
    atomic_json(backup / "manifest.json", manifest)

    journal = {
        "schema_version": 1,
        "mode": mode,
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
    elif data["stage"] not in {"agent_stopped", "archived", "identity_applied", "agent_started", "complete"}:
        fail(f"invalid_migration_stage:{data['stage']}")


def archive_old_state():
    data = load_journal()
    if data["stage"] in {"archived", "identity_applied", "agent_started", "complete"}:
        print("MIGRATION_ARCHIVE=ALREADY_DONE")
        return
    if data["stage"] != "agent_stopped":
        fail(f"invalid_migration_stage:{data['stage']}")
    backup = pathlib.Path(data["backup_dir"])
    backup.mkdir(parents=True, exist_ok=True)
    manifest_path = backup / "manifest.json"
    manifest_entries = []
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        manifest_entries = manifest.get("files", [])

    sources = [
        ("setup-device-id", setup_dir / "device_id"),
        ("setup-device-group", setup_dir / "device_group"),
        ("setup-host-fingerprint", setup_dir / "host_fingerprint"),
        ("setup-provision-initialized", setup_dir / "provision_initialized"),
        ("setup-complete", setup_dir / "setup_complete"),
        ("setup-wizard-started", setup_dir / "wizard_started"),
        ("setup-bootstrap-ui-done", setup_dir / "bootstrap_ui_done"),
        ("mprovision", mprovision),
        ("shouko-device-id", shouko / "device_id.txt"),
        ("shouko-device-group", shouko / "device_group.txt"),
        ("shouko-agent-state", shouko / "agent_state.json"),
        ("shouko-provision-report-json", shouko / "provision_report.json"),
        ("shouko-provision-report-text", shouko / "provision_report.txt"),
        ("shouko-aot-group-config", shouko / "aot_group_config.json"),
    ]
    archived_sources = {item.get("source_path") for item in manifest_entries if isinstance(item, dict)}
    for label, source in sources:
        if not source.exists():
            continue
        if not source.is_file():
            fail(f"archive_source_not_file:{source}")
        if str(source) in archived_sources:
            continue
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
        "mode": data.get("mode", "full"),
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
    if data["stage"] in {"identity_applied", "agent_started", "complete"}:
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

    gc_dir = pathlib.Path.home() / (".aot-" + "group" + "-control")
    if gc_dir.exists():
        for state_file in (
            "aot_group_state.json",
            "aot_worker_update_pending.json",
            "aot_worker_update_health.json",
            "aot_worker_version.json",
        ):
            try:
                (gc_dir / state_file).unlink()
            except FileNotFoundError:
                pass

    aot_cfg = shouko / "aot_group_config.json"
    new_aot_config = {
        "version": 3,
        "device_id": new_id,
        "enabled": True,
        "open_package": None,
    }
    atomic_json(aot_cfg, new_aot_config)

    new_mprovision = {
        "version": provision_version,
        "provision_ref": provision_ref,
        "device_id": new_id,
        "device_group": new_group,
        "phase": "complete" if data.get("mode") == "full" else "automatic",
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
    atomic_text(setup_dir / "bootstrap_ui_done", "yes\n")
    atomic_text(setup_dir / "provision_ref", provision_ref + "\n")
    atomic_text(setup_dir / f"provision-device-{provision_ref}.sh.sha256", provision_sha + "\n")
    save_journal(data, "identity_applied")
    print("MIGRATION_IDENTITY=OK")


def mark_agent_started():
    data = load_journal()
    if data["stage"] == "identity_applied":
        save_journal(data, "agent_started")
    elif data["stage"] not in {"agent_started", "complete"}:
        fail(f"invalid_migration_stage:{data['stage']}")
    print("MIGRATION_AGENT_STARTED=OK")


def mark_complete():
    if not journal_path.exists():
        print("MIGRATION_COMPLETE=NO_JOURNAL")
        return
    if os.environ.get("AOTSCRIPT_SETUP_FAULT_JOURNAL_COMPLETE") == "1":
        fail("fault_injected_journal_complete_fail")
    data = load_journal()
    if data["stage"] in {"identity_applied", "agent_started"}:
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
    "mark-agent-started": mark_agent_started,
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

verify_aot_online() {
  local device_id="$1" aot_register_helper origin agent_cfg aot_cfg output
  local attempt=1 max_attempts=3 verify_ok=0
  agent_cfg="$AGENT_CONFIG"
  aot_cfg="$SHOUKO_DIR/aot_group_config.json"

  if [ -n "${AOTSCRIPT_SETUP_MOCK_AOT_WS:-}" ]; then
    if [ "${AOTSCRIPT_SETUP_MOCK_AOT_WS}" = "offline" ]; then
      emit WARN "Agent heartbeat online nhưng AOT WebSocket offline (mock)."
      return 1
    fi
    if [ "${AOTSCRIPT_SETUP_MOCK_AOT_WS}" = "online" ]; then
      emit OK "AOT WebSocket ONLINE và Hub visible cho thiết bị $device_id (mock)."
      return 0
    fi
  fi

  if [ ! -f "$agent_cfg" ]; then
    emit ERROR "Thiếu agent_config.json để verify AOT Hub."
    return 1
  fi

  if [ ! -f "$aot_cfg" ]; then
    emit ERROR "Thiếu aot_group_config.json để verify AOT Hub."
    return 1
  fi

  aot_register_helper=""
  if [ -f "$STATE_BASE/msetup_registration.py" ]; then
    aot_register_helper="$STATE_BASE/msetup_registration.py"
  elif [ -f "$PREFIX/share/aotscript/msetup_registration.py" ]; then
    aot_register_helper="$PREFIX/share/aotscript/msetup_registration.py"
  elif [ -f "$HOME/.local/share/aotscript/msetup_registration.py" ]; then
    aot_register_helper="$HOME/.local/share/aotscript/msetup_registration.py"
  elif [ -f "$(dirname "$0")/msetup_registration.py" ]; then
    aot_register_helper="$(dirname "$0")/msetup_registration.py"
  else
    aot_register_helper="$(python - <<'PY'
import os, pathlib
gc = pathlib.Path.home() / (".aot-" + "group" + "-control") / "msetup_registration.py"
if gc.is_file():
    print(str(gc))
PY
)"
  fi

  if [ -z "$aot_register_helper" ] || [ ! -f "$aot_register_helper" ]; then
    emit ERROR "Thiếu helper msetup_registration.py để verify AOT Hub."
    return 1
  fi

  origin="$(python - "$agent_cfg" <<'PY'
import json, pathlib, sys, urllib.parse
try:
    data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    raw_url = str(data.get("worker_report_url", "")).strip()
    if not raw_url:
        sys.exit(1)
    u = urllib.parse.urlparse(raw_url)
    if u.scheme not in ("http", "https") or not u.netloc:
        sys.exit(1)
    print(f"{u.scheme}://{u.netloc}")
except Exception:
    sys.exit(1)
PY
)" || {
    emit ERROR "worker_report_url trong agent_config.json không hợp lệ hoặc thiếu scheme/host."
    return 1
  }

  while [ "$attempt" -le "$max_attempts" ]; do
    if output="$(python "$aot_register_helper" verify --origin "$origin" --agent-config "$agent_cfg" --aot-config "$aot_cfg" --device-id "$device_id" --timeout 5 2>&1)"; then
      if printf '%s\n' "$output" | grep -Fq "AOT_SERVER_ONLINE=YES" &&
         printf '%s\n' "$output" | grep -Fq "AOT_HUB_VISIBLE=YES"; then
        verify_ok=1
        break
      fi
    fi
    emit WARN "Lần $attempt/$max_attempts: AOT WebSocket chưa ONLINE trong Hub ($output). Đang thử lại..."
    attempt=$((attempt + 1))
    [ "$attempt" -le "$max_attempts" ] && sleep 2
  done

  if [ "$verify_ok" -eq 1 ]; then
    emit OK "AOT WebSocket ONLINE và Hub visible cho thiết bị $device_id."
    return 0
  fi

  emit ERROR "AOT WebSocket không kết nối hoặc Hub không thấy thiết bị $device_id sau $max_attempts lần thử: $output"
  return 1
}

resume_pending_migration() {
  local host_hash="$1" output stage source_id target_id target_group
  output="$(identity_call journal-status "$host_hash")" || die "Journal migration không hợp lệ."
  stage="$(printf '%s\n' "$output" | awk -F= '$1=="MIGRATION_STATUS" {print $2}')"
  [ "$stage" != NONE ] || return 1
  [ "$stage" != complete ] || return 1
  source_id="$(printf '%s\n' "$output" | awk -F= '$1=="MIGRATION_SOURCE_ID" {print $2}')"
  target_id="$(printf '%s\n' "$output" | awk -F= '$1=="MIGRATION_TARGET_ID" {print $2}')"
  target_group="$(printf '%s\n' "$output" | awk -F= '$1=="MIGRATION_TARGET_GROUP" {print $2}')"
  emit INFO "Resume migration $source_id → $target_id tại stage=$stage"

  if [ "$stage" = planned ]; then
    if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] &&
       [ "${AOTSCRIPT_SETUP_INTERRUPT_AFTER:-}" = plan ]; then
      emit WARN "TEST: ngắt có chủ đích sau plan (trước mutation)."
      return 75
    fi
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
    if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] &&
       [ "${AOTSCRIPT_SETUP_INTERRUPT_AFTER:-}" = apply ]; then
      emit WARN "TEST: ngắt có chủ đích sau apply identity."
      return 75
    fi
    stage=identity_applied
  fi

  if [ "$stage" = identity_applied ]; then
    CURRENT_STEP="clone-start-agent"
    start_agent_safely
    identity_call mark-agent-started >/dev/null || die "Không journal được agent_started."
    if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] &&
       [ "${AOTSCRIPT_SETUP_INTERRUPT_AFTER:-}" = start_agent ]; then
      emit WARN "TEST: ngắt có chủ đích sau start agent."
      return 75
    fi
    stage=agent_started
  fi

  if [ "$stage" = agent_started ]; then
    SELECTED_DEVICE_ID="$target_id"
    SELECTED_DEVICE_GROUP="$target_group"
    emit OK "Migration identity $source_id → $target_id đã sẵn sàng; tiếp tục provision & AOT validation."
    return 0
  fi

  die "Migration stage không xác định: $stage"
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

run_provision_once() {
  local device_id="$1" group="$2" script phase
  CURRENT_STEP="provision"
  if [ -s "$MPROVISION_STATE" ]; then
    phase="$(read_mprovision_phase "$device_id" "$group")" || die "mprovision state không khớp identity."
    state_write provision_initialized yes
    emit INFO "Resume mprovision phase=$phase; không chạy lại entrypoint/backup."
    return 0
  fi
  if [ "${AOTSCRIPT_SETUP_DRY_RUN:-0}" = 1 ]; then
    state_write provision_initialized yes
    emit OK "DRY-RUN: provision fresh sẽ chạy đúng một lần."
    return 0
  fi
  [ "$(state_read provision_initialized)" != yes ] || die "Thiếu mprovision state sau lần khởi tạo trước."
  script="$(download_provision)"
  AOTSCRIPT_PROVISION_REF="$PROVISION_REF" bash "$script" "$device_id" "$group" </dev/null ||
    die "Provision không hoàn tất bước hiện tại."
  [ -s "$MPROVISION_STATE" ] || die "Provision không tạo state."
  read_mprovision_phase "$device_id" "$group" >/dev/null || die "State provision postcondition sai."
  state_write provision_initialized yes
}

show_bootstrap_checkpoint() {
  cat <<'CHECKPOINT'

========== CHECKPOINT GIAO DIỆN BAN ĐẦU ==========
[ ] Tắt Play Protect và cập nhật Google Play.
[ ] Cài Termux:API và Termux:Boot từ F-Droid; mở Termux:Boot một lần.
[ ] Developer Options: external storage, resize, 700dp, freeform, desktop mode.
[ ] Cập nhật keyboard theo quy trình vận hành.
Nhập “MỞ LẠI” hoặc “ĐÃ XONG”.
==================================================
CHECKPOINT
}

bootstrap_checkpoint() {
  local action normalized
  [ "$(state_read bootstrap_ui_done)" = yes ] && return 0
  show_bootstrap_checkpoint
  prompt_value CHECKPOINT "Lựa chọn: "
  action="$PROMPT_RESULT"
  normalized="$(printf '%s' "$action" | tr '[:lower:]' '[:upper:]' | tr -d '[:space:]')"
  case "$normalized" in
    MỞLẠI|MOLAI)
      command -v termux-open-url >/dev/null 2>&1 && {
        termux-open-url "https://f-droid.org/packages/com.termux.api/" </dev/null >/dev/null 2>&1 || true
        termux-open-url "https://f-droid.org/packages/com.termux.boot/" </dev/null >/dev/null 2>&1 || true
      }
      emit INFO "Checkpoint vẫn đang chờ."
      exit 0
      ;;
    ĐÃXONG|DAXONG) state_write bootstrap_ui_done yes ;;
    *) die "Chỉ chấp nhận MỞ LẠI hoặc ĐÃ XONG." ;;
  esac
}

start_wizard() {
  local device_id="$1" group="$2" phase wizard
  CURRENT_STEP="wizard"
  if [ "${AOTSCRIPT_SETUP_DRY_RUN:-0}" = 1 ]; then
    verify_aot_online "$device_id" || die "AOT WebSocket chưa ONLINE."
    identity_call complete >/dev/null || die "Không hoàn tất được migration journal."
    if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] &&
       [ "${AOTSCRIPT_SETUP_INTERRUPT_AFTER:-}" = journal_complete ]; then
      emit WARN "TEST: ngắt có chủ đích sau journal complete."
      return 75
    fi
    state_write wizard_started yes
    state_write setup_complete yes
    return 0
  fi
  phase="$(read_mprovision_phase "$device_id" "$group")" || die "Không đọc được phase wizard."
  if [ "$phase" = complete ]; then
    verify_aot_online "$device_id" || die "AOT WebSocket chưa ONLINE."
    identity_call complete >/dev/null || die "Không hoàn tất được migration journal."
    if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] &&
       [ "${AOTSCRIPT_SETUP_INTERRUPT_AFTER:-}" = journal_complete ]; then
      emit WARN "TEST: ngắt có chủ đích sau journal complete."
      return 75
    fi
    state_write setup_complete yes
    emit OK "Identity hợp lệ; workflow complete, không replay provision/backup/restore."
    return 0
  fi
  if command -v aotscript-wizard >/dev/null 2>&1; then
    wizard="$(command -v aotscript-wizard)"
  elif [ -x "$HOME/bin/aotscript-wizard" ]; then
    wizard="$HOME/bin/aotscript-wizard"
  else
    die "Thiếu aotscript-wizard."
  fi
  "$wizard" start </dev/null || die "Wizard chưa chạy được; xem wizard-supervisor.log."
  verify_aot_online "$device_id" || die "AOT WebSocket chưa ONLINE sau khi chạy wizard."
  identity_call complete >/dev/null || die "Không hoàn tất được migration journal."
  if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] &&
     [ "${AOTSCRIPT_SETUP_INTERRUPT_AFTER:-}" = journal_complete ]; then
    emit WARN "TEST: ngắt có chủ đích sau journal complete."
    return 75
  fi
  state_write wizard_started yes
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
  run_provision_once "$device_id" "$group"
  start_wizard "$device_id" "$group"
  emit OK "Checkpoint hiện tại đã xử lý. Lần sau chỉ chạy aotsetup."
}

main "$@"
# Fix clone recovery and websocket validation
