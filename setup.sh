#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

VERSION="one-command-setup-v1"
# All files fetched by provision-device.sh inherit this exact tested revision.
PROVISION_REF="aaf9ead87a8469ea0fcf79747c4edb24ba1fec45"
PROVISION_SHA256="b71cd9990c1257e1e17be250226fffd688ecfb3499d8a878fb04c401518b6934"
RAW_BASE_DEFAULT="https://raw.githubusercontent.com/tinhpr9/Aotscript/$PROVISION_REF"
STATE_BASE="${XDG_STATE_HOME:-$HOME/.local/state}/aotscript"
SETUP_STATE_DIR="$STATE_BASE/setup-driver"
MPROVISION_STATE="$STATE_BASE/mprovision.json"
LOG_FILE="$SETUP_STATE_DIR/setup.log"
LOCK_DIR="$SETUP_STATE_DIR/setup.lock"
CURRENT_STEP="startup"
LOCK_HELD=0

timestamp() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

emit() {
  local level="$1"
  local line
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
  emit "LỖI" "Bước '$CURRENT_STEP' dừng ở dòng $line (mã lỗi $rc). Xem log: $LOG_FILE" >&2
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

release_lock() {
  local owner=""
  [ "$LOCK_HELD" = 1 ] || return 0
  owner="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [ "$owner" = "$$" ]; then
    rm -f "$LOCK_DIR/pid"
    rmdir "$LOCK_DIR" 2>/dev/null || true
  fi
  LOCK_HELD=0
}

acquire_lock() {
  local owner=""
  mkdir -p "$SETUP_STATE_DIR"
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    LOCK_HELD=1
    return 0
  fi

  owner="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [[ "$owner" =~ ^[0-9]+$ ]] && kill -0 "$owner" 2>/dev/null; then
    die "Một phiên setup khác đang chạy (PID=$owner); không chạy đồng thời."
  fi

  rm -f "$LOCK_DIR/pid" 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || die "Không dọn được khóa setup cũ."
  mkdir "$LOCK_DIR" || die "Không tạo được khóa setup."
  printf '%s\n' "$$" > "$LOCK_DIR/pid"
  LOCK_HELD=1
}

prompt_value() {
  local env_name="$1" prompt="$2" value=""
  if [ "${AOTSCRIPT_SETUP_DRY_RUN:-0}" = 1 ]; then
    case "$env_name" in
      DEVICE_ID) value="${AOTSCRIPT_SETUP_DEVICE_ID:-}" ;;
      GROUP) value="${AOTSCRIPT_SETUP_GROUP:-}" ;;
      CONFIRM) value="${AOTSCRIPT_SETUP_CONFIRM:-}" ;;
      CHECKPOINT) value="${AOTSCRIPT_SETUP_CHECKPOINT_ACTION:-}" ;;
    esac
  fi
  if [ -z "$value" ]; then
    printf '%s' "$prompt" >&2
    IFS= read -r value || die "Không đọc được câu trả lời từ stdin."
  fi
  printf '%s\n' "$value"
}

validate_saved_identity() {
  local device_id="$1" group="$2" saved_id saved_group
  saved_id="$(state_read device_id)"
  saved_group="$(state_read device_group)"

  if [ -n "$saved_id" ] && [ "$saved_id" != "$device_id" ]; then
    die "State cũ thuộc Device ID '$saved_id', không phải '$device_id'. Không ghi đè state."
  fi
  if [ -n "$saved_group" ] && [ "$saved_group" != "$group" ]; then
    die "State cũ thuộc nhóm '$saved_group', không phải '$group'. Không ghi đè state."
  fi
}

ensure_packages() {
  local package command
  local missing=()
  local packages=(curl python unzip zip)

  if [ "${AOTSCRIPT_SETUP_DRY_RUN:-0}" = 1 ]; then
    emit INFO "DRY-RUN: bỏ qua cài package Termux."
    return 0
  fi

  command -v pkg >/dev/null 2>&1 || die "Không tìm thấy pkg; hãy chạy script trong Termux."
  for package in "${packages[@]}"; do
    command="$package"
    command -v "$command" >/dev/null 2>&1 || missing+=("$package")
  done
  command -v sha256sum >/dev/null 2>&1 || missing+=(coreutils)

  if [ "${#missing[@]}" -gt 0 ]; then
    emit INFO "Đang cài package Termux còn thiếu: ${missing[*]}"
    pkg install -y "${missing[@]}" || die "pkg install thất bại."
    hash -r
  fi

  for command in curl python unzip zip sha256sum; do
    command -v "$command" >/dev/null 2>&1 || die "Thiếu lệnh '$command' sau khi cài package."
  done
  state_write packages_done yes
}

read_mprovision_identity() {
  python - "$MPROVISION_STATE" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file() or path.stat().st_size == 0:
    raise SystemExit(0)
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"INVALID:{type(exc).__name__}")
if not isinstance(data, dict):
    raise SystemExit("INVALID:state_root")
print("|".join(str(data.get(key, "")) for key in ("device_id", "device_group", "phase")))
PY
}

validate_mprovision_state() {
  local device_id="$1" group="$2" identity mp_id mp_group mp_phase
  [ -s "$MPROVISION_STATE" ] || return 1
  identity="$(read_mprovision_identity)" || die "State mprovision hiện có không đọc được; dừng an toàn."
  IFS='|' read -r mp_id mp_group mp_phase <<< "$identity"
  [ "$mp_id" = "$device_id" ] || die "mprovision hiện có thuộc '$mp_id', không phải '$device_id'."
  [ "$mp_group" = "$group" ] || die "mprovision hiện có thuộc nhóm '$mp_group', không phải '$group'."
  [ -n "$mp_phase" ] || die "State mprovision thiếu phase."
  printf '%s\n' "$mp_phase"
}

show_bootstrap_checkpoint() {
  cat <<'CHECKPOINT'

========== CHECKPOINT GIAO DIỆN BAN ĐẦU ==========
[ ] Tắt Play Protect và cập nhật Google Play.
[ ] Cài Termux:API từ https://f-droid.org/packages/com.termux.api/
[ ] Cài Termux:Boot từ https://f-droid.org/packages/com.termux.boot/ và MỞ app một lần.
[ ] Developer Options: cho phép app trên bộ nhớ ngoài, cho phép resize,
    Smallest width 700dp, freeform windows và desktop mode.
[ ] Cập nhật/chọn keyboard theo quy trình vận hành.

Nhập “MỞ LẠI” để mở lại nguồn F-Droid, hoặc “ĐÃ XONG” để tiếp tục.
Không cần kiểm tra Root; setup không xóa dữ liệu hay reset máy.
==================================================
CHECKPOINT
}

open_bootstrap_links() {
  local url
  for url in \
    "https://f-droid.org/packages/com.termux.api/" \
    "https://f-droid.org/packages/com.termux.boot/"; do
    if command -v termux-open-url >/dev/null 2>&1; then
      termux-open-url "$url" >/dev/null 2>&1 || true
    fi
    printf 'MỞ: %s\n' "$url"
  done
}

bootstrap_checkpoint() {
  local action normalized
  [ "$(state_read bootstrap_ui_done)" = yes ] && return 0
  show_bootstrap_checkpoint
  action="$(prompt_value CHECKPOINT "Lựa chọn: ")"
  normalized="$(printf '%s' "$action" | tr '[:lower:]' '[:upper:]' | tr -d '[:space:]')"
  case "$normalized" in
    MỞLẠI|MOLAI)
      open_bootstrap_links
      emit INFO "Checkpoint vẫn đang chờ. Chạy lại cùng câu lệnh sau khi hoàn tất."
      exit 0
      ;;
    ĐÃXONG|DAXONG)
      state_write bootstrap_ui_done yes
      emit OK "Đã xác nhận checkpoint giao diện ban đầu."
      ;;
    *)
      die "Chỉ chấp nhận MỞ LẠI hoặc ĐÃ XONG tại checkpoint."
      ;;
  esac
}

download_provision() {
  local target tmp actual_sha
  target="$SETUP_STATE_DIR/provision-device-$PROVISION_REF.sh"

  if [ -s "$target" ] && bash -n "$target" &&
     [ "$(sha256sum "$target" | awk '{print $1}')" = "$PROVISION_SHA256" ]; then
    printf '%s\n' "$target"
    return 0
  fi

  tmp="$SETUP_STATE_DIR/.provision-device.tmp.$$"
  rm -f "$tmp"
  curl -fsSL --retry 3 --connect-timeout 15 \
    "$RAW_BASE_DEFAULT/provision-device.sh?t=$(date +%s)" -o "$tmp" || {
      rm -f "$tmp"
      die "Không tải được provision-device.sh từ revision $PROVISION_REF."
    }
  [ -s "$tmp" ] || {
    rm -f "$tmp"
    die "provision-device.sh tải về bị rỗng."
  }
  bash -n "$tmp" || {
    rm -f "$tmp"
    die "provision-device.sh tải về sai cú pháp."
  }
  actual_sha="$(sha256sum "$tmp" | awk '{print $1}')"
  [ "$actual_sha" = "$PROVISION_SHA256" ] || {
    rm -f "$tmp"
    die "SHA-256 của provision-device.sh không khớp revision đã kiểm thử."
  }
  chmod 700 "$tmp"
  mv -f "$tmp" "$target"
  printf '%s\n' "$target"
}

run_provision_once() {
  local device_id="$1" group="$2" provision_script phase=""

  if [ -s "$MPROVISION_STATE" ]; then
    phase="$(validate_mprovision_state "$device_id" "$group")"
    state_write provision_initialized yes
    emit INFO "Provision đã có state phase=$phase; không chạy lại entrypoint hoặc backup."
    return 0
  fi

  if [ "${AOTSCRIPT_SETUP_DRY_RUN:-0}" = 1 ] &&
     [ "$(state_read provision_initialized)" = yes ]; then
    emit INFO "DRY-RUN: provision đã được ghi nhận; không chạy lại."
    return 0
  fi

  if [ "$(state_read provision_initialized)" = yes ]; then
    die "Setup ghi provision đã khởi tạo nhưng thiếu mprovision state; không tự chạy đè."
  fi

  if [ "${AOTSCRIPT_SETUP_DRY_RUN:-0}" = 1 ]; then
    state_write provision_initialized yes
    emit OK "DRY-RUN: provision sẽ chạy đúng một lần tại revision $PROVISION_REF."
    return 0
  fi

  provision_script="$(download_provision)"
  emit INFO "Bắt đầu provision $device_id/$group từ revision $PROVISION_REF."
  AOTSCRIPT_PROVISION_REF="$PROVISION_REF" \
    bash "$provision_script" "$device_id" "$group" || die "provision-device.sh không hoàn tất bước hiện tại."

  [ -s "$MPROVISION_STATE" ] || die "Provision kết thúc nhưng không tạo mprovision state."
  validate_mprovision_state "$device_id" "$group" >/dev/null
  state_write provision_initialized yes
  emit OK "Provision đã khởi tạo; các lần sau sẽ resume, không chạy lại entrypoint."
}

show_wizard_checkpoints() {
  cat <<'CHECKPOINTS'

========== CÁC CHECKPOINT WIZARD CÒN LẠI ==========
Wizard/notification dùng nút “MỞ LẠI / ĐÃ XONG” để tiếp tục đúng phase:
- Google config/import/login nếu state hiện tại yêu cầu.
- Swift Backup: backup app label/data; sau setup restore/backup toàn bộ app còn lại không data.
- Chuẩn bị Delta/Shouko và cập nhật keyboard.
- Toolcheck; setup/check/login cookie; chỉnh auto-exec; lấy key Shouko.
- Bật 1.1.1.1/WARP; kiểm tra đủ user và account không trùng.

CẢNH BÁO: “97598239454123, kêu AI chỉnh link” chưa xác định file/định dạng,
nên setup KHÔNG tự sửa. Đây vẫn là checkpoint thủ công theo đúng quy trình máy.

`mprovision done pre` tự kiểm tra rclone. `done post` tự chạy ui-post,
audit, backup cuối và publish; không chạy riêng các bước đó.
===================================================
CHECKPOINTS
}

start_wizard() {
  local wizard phase=""
  if [ "${AOTSCRIPT_SETUP_DRY_RUN:-0}" = 1 ]; then
    state_write wizard_started yes
    emit OK "DRY-RUN: wizard sẽ được khởi động/resume."
    return 0
  fi

  phase="$(validate_mprovision_state "$(state_read device_id)" "$(state_read device_group)")"
  if [ "$phase" = complete ]; then
    state_write setup_complete yes
    emit OK "mprovision đã complete; không chạy lại provision hoặc backup."
    return 0
  fi

  if command -v aotscript-wizard >/dev/null 2>&1; then
    wizard="$(command -v aotscript-wizard)"
  elif [ -x "$HOME/bin/aotscript-wizard" ]; then
    wizard="$HOME/bin/aotscript-wizard"
  else
    die "Không tìm thấy aotscript-wizard sau provision."
  fi

  emit INFO "Khởi động/resume wizard tại mprovision phase=$phase."
  "$wizard" start || die "Wizard chưa chạy được. Kiểm tra Termux:API từ F-Droid và log wizard-supervisor.log."
  state_write wizard_started yes
  emit OK "Wizard đã chạy; dùng notification MỞ LẠI / ĐÃ XONG hoặc chạy lại cùng câu lệnh."
}

main() {
  local raw_id raw_group raw_confirm device_id group confirmation normalized_confirm

  case "${1:-}" in
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
    "") ;;
    *) die "Tham số không hỗ trợ: $1" ;;
  esac

  CURRENT_STEP="lock"
  acquire_lock
  trap release_lock EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'on_error "$?" "$LINENO"' ERR

  if [ -n "${AOTSCRIPT_SETUP_HOLD_LOCK_SECONDS:-}" ]; then
    sleep "$AOTSCRIPT_SETUP_HOLD_LOCK_SECONDS"
  fi

  CURRENT_STEP="identity"
  raw_id="$(prompt_value DEVICE_ID "Device ID (ví dụ m88): ")"
  device_id="$(normalize_device_id "$raw_id")" || die "Device ID phải có dạng m88 (m + 1–6 chữ số, số đầu không phải 0)."
  raw_group="$(prompt_value GROUP "Nhóm (NOVA hoặc MARMOT): ")"
  group="$(normalize_group "$raw_group")" || die "Nhóm chỉ được là NOVA hoặc MARMOT."
  validate_saved_identity "$device_id" "$group"

  raw_confirm="$(prompt_value CONFIRM "Bắt đầu/resume setup $device_id thuộc $group? [y/N]: ")"
  confirmation="$(printf '%s' "$raw_confirm" | tr '[:upper:]' '[:lower:]')"
  normalized_confirm="$(printf '%s' "$confirmation" | tr -d '[:space:]')"
  case "$normalized_confirm" in
    y|yes|có|co) ;;
    *) die "Người dùng chưa xác nhận bắt đầu." ;;
  esac

  CURRENT_STEP="packages"
  ensure_packages
  if [ -s "$MPROVISION_STATE" ]; then
    validate_mprovision_state "$device_id" "$group" >/dev/null
  fi
  state_write device_id "$device_id"
  state_write device_group "$group"
  state_write provision_ref "$PROVISION_REF"
  emit INFO "Identity=$device_id group=$group revision=$PROVISION_REF"

  CURRENT_STEP="bootstrap-checkpoint"
  bootstrap_checkpoint

  CURRENT_STEP="provision"
  run_provision_once "$device_id" "$group"

  CURRENT_STEP="wizard"
  show_wizard_checkpoints
  start_wizard

  emit OK "Bước hiện tại hoàn tất. Log: $LOG_FILE"
}

main "$@"
