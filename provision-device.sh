#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

VERSION="phase2-v1"
RAW="https://raw.githubusercontent.com/tinhpr9/Aotscript/main"
SWIFT_FILE_ID="1-5O8rQI9zzeVTIZcYoFmgj0gm8LW4nYI"
SD="${MPROVISION_SD:-/storage/emulated/0}"
DL="${MPROVISION_DL:-$SD/Download}"
SHOUKO="$DL/Shouko"
DELTA="$SD/Delta"
STATE_DIR="${MPROVISION_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/aotscript}"
STATE="$STATE_DIR/mprovision.json"
BACKUPS="${MPROVISION_BACKUPS:-$SD/Aotscript-Backups}"
SWIFT_APK="$DL/SwiftBackup.apk"
WINTERHUB="${MPROVISION_WINTERHUB:-$HOME/.termux/boot/winterhub.sh}"
AGENT_BOOT="${MPROVISION_AGENT_BOOT:-$HOME/.termux/boot/01-agent.sh}"
AGENT="$DL/Agent_Core.py"
WRAPPER="$HOME/bin/mprovision"
REPORT_JSON="$SHOUKO/provision_report.json"
REPORT_TEXT="$SHOUKO/provision_report.txt"

ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; }
die() { printf '[LỖI] %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'__MP_USAGE__'
Lần đầu:
  tải provision-device.sh từ main, chạy bash -n, rồi:
  bash provision-device.sh 116 NOVA

Điều khiển:
  mprovision status
  mprovision checklist
  mprovision done pre
  mprovision done post
  mprovision resume
  mprovision report

Quy tắc checkpoint:
  - Xong THỦ CÔNG 1: dùng mprovision done pre
  - Xong THỦ CÔNG 2: dùng mprovision done post
  - resume không tự bỏ qua checkpoint thủ công.
__MP_USAGE__
}

norm_id() {
  local value
  value="$(printf '%s' "$1" | tr -d '\r\n ' | tr '[:upper:]' '[:lower:]')"
  [[ "$value" =~ ^[1-9][0-9]{0,5}$ ]] && value="m$value"
  [[ "$value" =~ ^m[1-9][0-9]{0,5}$ ]] || return 1
  printf '%s\n' "$value"
}

norm_group() {
  local value
  value="$(printf '%s' "$1" | tr -d '\r\n _-' | tr '[:lower:]' '[:upper:]')"
  case "$value" in
    1|NHOM1|GROUP1|MARMOT) printf 'MARMOT\n' ;;
    2|NHOM2|GROUP2|NOVA) printf 'NOVA\n' ;;
    *) return 1 ;;
  esac
}

state_set() {
  mkdir -p "$STATE_DIR"
  python - "$STATE" "$@" <<'__MP_STATE_SET_PY__'
import datetime, json, os, pathlib, sys
path = pathlib.Path(sys.argv[1])
data = {}
if path.exists():
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("state root is not object")
for item in sys.argv[2:]:
    if "=" not in item:
        raise SystemExit("state item missing =")
    key, value = item.split("=", 1)
    if not key:
        raise SystemExit("state key empty")
    data[key] = value
data["updated_at"] = datetime.datetime.now(
    datetime.timezone.utc
).strftime("%Y-%m-%dT%H:%M:%SZ")
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
try:
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checked = json.loads(tmp.read_text(encoding="utf-8"))
    if not isinstance(checked, dict):
        raise ValueError("state root is not object")
    os.replace(tmp, path)
finally:
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
__MP_STATE_SET_PY__
}

state_get() {
  python - "$STATE" "$1" <<'__MP_STATE_GET_PY__'
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
if not path.exists():
    raise SystemExit(0)
data = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data, dict):
    raise SystemExit(2)
value = data.get(sys.argv[2], "")
print(value if isinstance(value, str) else str(value))
__MP_STATE_GET_PY__
}

install_wrapper() {
  local tmp stamp prefix
  mkdir -p "$HOME/bin"
  tmp="$WRAPPER.tmp.$$"
  stamp="$(date +%Y%m%d-%H%M%S)"
  cat > "$tmp" <<'__MP_WRAPPER__'
#!/data/data/com.termux/files/usr/bin/bash
set -u
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT INT TERM
/data/data/com.termux/files/usr/bin/curl -fsSL --retry 3 --connect-timeout 15 \
  "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/provision-device.sh?t=$(date +%s)" \
  -o "$TMP" || exit 1
[ -s "$TMP" ] || exit 1
/data/data/com.termux/files/usr/bin/bash -n "$TMP" || exit 1
/data/data/com.termux/files/usr/bin/bash "$TMP" "$@"
__MP_WRAPPER__
  chmod 700 "$tmp"
  if [ -f "$WRAPPER" ] && cmp -s "$tmp" "$WRAPPER"; then
    rm -f "$tmp"
  else
    [ ! -f "$WRAPPER" ] || cp -p "$WRAPPER" "$WRAPPER.bak-$stamp"
    mv -f "$tmp" "$WRAPPER"
    chmod 700 "$WRAPPER"
  fi
  grep -qxF 'export PATH="$HOME/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null ||
    echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc"
  export PATH="$HOME/bin:$PATH"
  prefix="${PREFIX:-/data/data/com.termux/files/usr}/bin/mprovision"
  if [ "$prefix" != "$WRAPPER" ]; then
    mkdir -p "$(dirname "$prefix")"
    if [ -L "$prefix" ]; then
      rm -f "$prefix"
    elif [ -e "$prefix" ]; then
      cp -p "$prefix" "$prefix.bak-$stamp"
      rm -f "$prefix"
    fi
    ln -s "$WRAPPER" "$prefix"
  fi
  hash -r
}

root_ok() { su -c id 2>/dev/null | grep -q 'uid=0(root)'; }
rclone_ok() {
  command -v rclone >/dev/null 2>&1 &&
    rclone listremotes 2>/dev/null | grep -Fxq 'gdrive:' &&
    rclone lsd gdrive: >/dev/null 2>&1
}

apk_ok() {
  [ -s "$1" ] && unzip -tqq "$1" >/dev/null 2>&1 &&
    unzip -Z1 "$1" 2>/dev/null | tr -d '\r' | grep -Fxq AndroidManifest.xml
}

ensure_gdown() {
  if command -v gdown >/dev/null 2>&1; then
    ok "gdown đã có"
    return 0
  fi

  echo "[*] Đang cài gdown..."

  python -m pip install --upgrade gdown ||
    die "Cài gdown thất bại"

  hash -r

  command -v gdown >/dev/null 2>&1 ||
    die "Đã cài nhưng không tìm thấy gdown"

  ok "Cài gdown thành công"
}

install_swift() {
  local part installed=0

  part="$SWIFT_APK.part.$$"
  mkdir -p "$DL"

  if ! apk_ok "$SWIFT_APK"; then
    rm -f "$part"

    ensure_gdown

    echo "[*] Đang tải Swift Backup..."

    gdown "$SWIFT_FILE_ID" -O "$part" || {
      rm -f "$part"
      die "Tải Swift Backup bằng gdown thất bại"
    }

    apk_ok "$part" || {
      rm -f "$part"
      die "Swift Backup tải về không phải APK hợp lệ"
    }

    mv -f "$part" "$SWIFT_APK"
    chmod 600 "$SWIFT_APK" 2>/dev/null || true

    ok "Đã tải Swift Backup APK hợp lệ"
  else
    ok "Swift Backup APK đã có và hợp lệ"
  fi

  if su -c "pm install -r '$SWIFT_APK'" \
       >/dev/null 2>&1; then
    installed=1
    ok "Đã cài hoặc cập nhật Swift Backup"
  else
    warn "Không tự cài được Swift Backup"
    warn "Hãy cài thủ công APK tại $SWIFT_APK"
  fi

  state_set "swift_install=$installed"
}

zip_shouko() {
  local out="$1" part="$1.part.$$.zip" item
  local sources=(Shouko)
  [ -d "$SHOUKO" ] || return 2
  for item in config-change.json cookie.txt cookie.txt.bak Cookies.txt Cookies.txt.bak shouko.py; do
    [ ! -e "$DL/$item" ] || sources+=("$item")
  done
  rm -f "$part"
  (cd "$DL" && zip -qr "$part" "${sources[@]}") || { rm -f "$part"; return 1; }
  unzip -tqq "$part" >/dev/null 2>&1 || { rm -f "$part"; return 1; }
  mv -f "$part" "$out"
  chmod 600 "$out" 2>/dev/null || true
}

zip_delta() {
  local out="$1" part="$1.part.$$.zip"
  [ -d "$DELTA" ] || return 2
  rm -f "$part"
  (cd "$SD" && zip -qr "$part" Delta) || { rm -f "$part"; return 1; }
  unzip -tqq "$part" >/dev/null 2>&1 || { rm -f "$part"; return 1; }
  mv -f "$part" "$out"
  chmod 600 "$out" 2>/dev/null || true
}

backup_data() {
  local label="$1" required="$2" device stamp dir remote listing file count=0
  local expected=()

  device="$(state_get device_id)"
  stamp="$(date +%Y%m%d-%H%M%S)"
  dir="$BACKUPS/$device/$stamp-$label"
  remote="gdrive:/Aotscript-Backups/$device/$stamp-$label"

  mkdir -p "$dir"

  if [ -d "$SHOUKO" ]; then
    zip_shouko "$dir/Shouko.zip" ||
      die "Tạo Shouko.zip thất bại"

    (
      cd "$dir"
      sha256sum Shouko.zip > Shouko.zip.sha256
    )

    expected+=(Shouko.zip Shouko.zip.sha256)
    count=$((count + 1))
  elif [ "$required" = 1 ]; then
    die "Thiếu Shouko ở backup bắt buộc"
  fi

  if [ -d "$DELTA" ]; then
    zip_delta "$dir/Delta.zip" ||
      die "Tạo Delta.zip thất bại"

    (
      cd "$dir"
      sha256sum Delta.zip > Delta.zip.sha256
    )

    expected+=(Delta.zip Delta.zip.sha256)
    count=$((count + 1))
  elif [ "$required" = 1 ]; then
    die "Thiếu Delta ở backup bắt buộc"
  fi

  if [ "$count" = 0 ]; then
    rmdir "$dir" 2>/dev/null || true
    state_set \
      "backup_$label=SKIPPED_NO_EXISTING_DATA" \
      "backup_${label}_remote="
    ok "Không có Shouko hoặc Delta cũ để backup $label"
    return
  fi

  rclone copy "$dir" "$remote" ||
    die "Upload backup $label thất bại"

  listing="$(
    rclone lsf "$remote" --files-only 2>/dev/null ||
      true
  )"

  for file in "${expected[@]}"; do
    printf '%s\n' "$listing" |
      grep -Fxq "$file" ||
      die "Remote backup thiếu $file"
  done

  state_set \
    "backup_$label=$dir" \
    "backup_${label}_remote=$remote"

  ok "Backup $label đã kiểm tra ZIP, SHA-256 và upload"
}

run_msetup() {
  local tmp
  tmp="$(mktemp)"
  curl -fsSL --retry 3 --connect-timeout 15 \
    "$RAW/setup-m166.sh?t=$(date +%s)" -o "$tmp" || {
      rm -f "$tmp"
      die "Không tải được setup-m166.sh"
    }
  [ -s "$tmp" ] && bash -n "$tmp" || {
    rm -f "$tmp"
    die "setup-m166.sh tải về không hợp lệ"
  }
  bash "$tmp" "$(state_get device_id)" "$(state_get device_group)" || {
    rm -f "$tmp"
    die "msetup không hoàn tất"
  }
  rm -f "$tmp"
}

install_winterhub() {
  local tmp="$WINTERHUB.tmp.$$" stamp="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$(dirname "$WINTERHUB")"
  cat > "$tmp" <<'__MP_WINTERHUB__'
#!/data/data/com.termux/files/usr/bin/bash
su -c '
export PATH="$PATH:/data/data/com.termux/files/usr/bin"
export TERM="xterm-256color"
export TMPDIR="/data/data/com.termux/files/usr/tmp"
export LD_LIBRARY_PATH="/data/data/com.termux/files/usr/lib"
cd "/storage/emulated/0/Download" || exit 1
exec "/data/data/com.termux/files/home/caylapbu/main"
' <<'__MP_WINTERHUB_INPUT__'
2
1000
2
__MP_WINTERHUB_INPUT__
__MP_WINTERHUB__
  bash -n "$tmp" || { rm -f "$tmp"; die "winterhub tạm sai cú pháp"; }
  chmod 700 "$tmp"
  if [ -f "$WINTERHUB" ] && cmp -s "$tmp" "$WINTERHUB"; then
    rm -f "$tmp"
  else
    [ ! -f "$WINTERHUB" ] || cp -p "$WINTERHUB" "$WINTERHUB.bak-$stamp"
    mv -f "$tmp" "$WINTERHUB"
  fi
  ok "winterhub.sh đã sẵn sàng"
}

agent_count() {
  python - <<'__MP_AGENT_COUNT_PY__'
import os, pathlib
pids = []
for entry in os.listdir("/proc"):
    if not entry.isdigit():
        continue
    try:
        args = [
            item.decode("utf-8", errors="replace")
            for item in pathlib.Path(f"/proc/{entry}/cmdline").read_bytes().split(b"\0")
            if item
        ]
    except Exception:
        continue
    if any(arg.endswith("/Download/Agent_Core.py") for arg in args):
        pids.append(int(entry))
print(len(set(pids)))
__MP_AGENT_COUNT_PY__
}

validate_config() {
  python - "$SHOUKO/agent_config.json" <<'__MP_CONFIG_PY__'
import json, pathlib, sys, urllib.parse
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data, dict):
    raise SystemExit(1)
url = data.get("worker_report_url")
secret = data.get("agent_report_secret")
if not isinstance(url, str) or not url.strip():
    raise SystemExit(1)
parsed = urllib.parse.urlparse(url.strip())
if parsed.scheme not in {"http", "https"} or not parsed.netloc:
    raise SystemExit(1)
if not isinstance(secret, str) or not secret.strip():
    raise SystemExit(1)
__MP_CONFIG_PY__
}

final_check() {
  local id group
  id="$(tr -d '\r\n ' < "$SHOUKO/device_id.txt" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)"
  group="$(tr -d '\r\n ' < "$SHOUKO/device_group.txt" 2>/dev/null | tr '[:lower:]' '[:upper:]' || true)"
  [ "$id" = "$(state_get device_id)" ] || die "device_id không đúng"
  [ "$group" = "$(state_get device_group)" ] || die "device_group không đúng"
  [ -d "$SHOUKO" ] && [ -d "$DELTA" ] || die "Thiếu Shouko hoặc Delta"
  [ -s "$AGENT" ] && [ -s "$AGENT_BOOT" ] && [ -s "$WINTERHUB" ] || die "Thiếu Agent hoặc boot script"
  [ -x "$HOME/caylapbu/main" ] || die "Thiếu $HOME/caylapbu/main"
  bash -n "$AGENT_BOOT" && bash -n "$WINTERHUB" || die "Boot script sai cú pháp"
  python - "$AGENT" <<'__MP_AGENT_COMPILE_PY__'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
compile(path.read_text(encoding="utf-8"), str(path), "exec")
__MP_AGENT_COMPILE_PY__
  validate_config || die "agent_config.json không hợp lệ"
  command -v toolcheck >/dev/null 2>&1 || die "Không tìm thấy toolcheck"
  [ "$(agent_count)" = 1 ] || die "Agent không chạy đúng một tiến trình"
  ok "Kiểm tra cuối đạt"
}

utc_now() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

backup_remote_for() {
  local label="$1" stored local_path device leaf

  stored="$(state_get "backup_${label}_remote")"

  if [ -n "$stored" ]; then
    printf '%s\n' "$stored"
    return 0
  fi

  local_path="$(state_get "backup_$label")"

  case "$local_path" in
    ""|SKIPPED_*)
      return 0
      ;;
  esac

  device="$(state_get device_id)"
  leaf="$(basename "$local_path")"

  [ -n "$device" ] && [ -n "$leaf" ] ||
    return 0

  printf 'gdrive:/Aotscript-Backups/%s/%s\n' \
    "$device" \
    "$leaf"
}

show_manual_pre() {
  cat <<'__MP_MANUAL_PRE__'

========== THỦ CÔNG 1 ==========
[ ] Đăng nhập Google trực tiếp trên máy.
[ ] Kiểm tra Play Protect theo quy trình vận hành.
[ ] Swift Backup: backup Termux kèm data.
[ ] Swift Backup: backup các app/data cần thiết.
[ ] Không lưu hoặc gửi mật khẩu, token hay key.

Làm xong chạy:
  mprovision done pre

Chỉ xem lại danh sách:
  mprovision checklist
================================
__MP_MANUAL_PRE__
}

show_manual_post() {
  cat <<'__MP_MANUAL_POST__'

========== THỦ CÔNG 2 ==========
[ ] Khôi phục app/data bằng Swift Backup.
[ ] Mở Termux:Boot một lần.
[ ] Hoàn tất key Shouko, cookie và login cookie.
[ ] Hoàn tất 1.1.1.1, Control, khung tab và auto-exec.
[ ] Chạy toolcheck; đủ user và không trùng account.
[ ] Chạy thử winterhub đúng một lần; không reboot lặp.

Làm xong chạy:
  mprovision done post

Chỉ xem lại danh sách:
  mprovision checklist
================================
__MP_MANUAL_POST__
}

checklist() {
  local phase

  [ -s "$STATE" ] ||
    die "Chưa khởi tạo mprovision"

  phase="$(state_get phase)"

  case "$phase" in
    manual_pre)
      show_manual_pre
      ;;
    manual_post)
      show_manual_post
      ;;
    complete)
      echo "CHECKPOINT=HOÀN_TẤT"
      echo "NEXT=mprovision report"
      ;;
    *)
      echo "CHECKPOINT=KHÔNG_CHỜ_THỦ_CÔNG"
      status
      ;;
  esac
}

write_completion_report() {
  local process_count="${1:-}"
  local generated_at completed_at
  local backup_before_remote backup_after_remote

  [ -n "$process_count" ] ||
    process_count="$(agent_count)"

  generated_at="$(utc_now)"
  completed_at="$(state_get completed_at)"

  [ -n "$completed_at" ] ||
    completed_at="$generated_at"

  backup_before_remote="$(backup_remote_for before)"
  backup_after_remote="$(backup_remote_for after)"

  mkdir -p "$SHOUKO"

  python - \
    "$REPORT_JSON" \
    "$REPORT_TEXT" \
    "$VERSION" \
    "$(state_get device_id)" \
    "$(state_get device_group)" \
    "$(state_get run_id)" \
    "$completed_at" \
    "$(state_get manual_pre_confirmed_at)" \
    "$(state_get manual_post_confirmed_at)" \
    "$(state_get backup_before)" \
    "$backup_before_remote" \
    "$(state_get backup_after)" \
    "$backup_after_remote" \
    "$process_count" \
    "$generated_at" \
    <<'__MP_REPORT_WRITE_PY__'
import json
import os
import pathlib
import sys

(
    json_name,
    text_name,
    version,
    device_id,
    device_group,
    run_id,
    completed_at,
    manual_pre_at,
    manual_post_at,
    backup_before,
    backup_before_remote,
    backup_after,
    backup_after_remote,
    agent_count,
    generated_at,
) = sys.argv[1:]

report = {
    "schema_version": 1,
    "status": "complete",
    "provision_version": version,
    "device_id": device_id,
    "device_group": device_group,
    "run_id": run_id,
    "completed_at": completed_at,
    "generated_at": generated_at,
    "manual_pre_confirmed_at": manual_pre_at,
    "manual_post_confirmed_at": manual_post_at,
    "backup_before_local": backup_before,
    "backup_before_remote": backup_before_remote,
    "backup_after_local": backup_after,
    "backup_after_remote": backup_after_remote,
    "agent_process_count": int(agent_count),
    "final_check": "ok",
}

json_path = pathlib.Path(json_name)
text_path = pathlib.Path(text_name)

json_tmp = json_path.with_name(
    json_path.name + f".tmp-{os.getpid()}"
)
text_tmp = text_path.with_name(
    text_path.name + f".tmp-{os.getpid()}"
)

text_lines = [
    "MPROVISION_REPORT",
    "STATUS=complete",
    f"VERSION={version}",
    f"DEVICE_ID={device_id}",
    f"DEVICE_GROUP={device_group}",
    f"RUN_ID={run_id}",
    f"COMPLETED_AT={completed_at}",
    f"AGENT_PROCESS_COUNT={agent_count}",
    "FINAL_CHECK=ok",
    f"BACKUP_AFTER_REMOTE={backup_after_remote}",
]

try:
    json_tmp.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    text_tmp.write_text(
        "\n".join(text_lines) + "\n",
        encoding="utf-8",
    )

    checked = json.loads(
        json_tmp.read_text(encoding="utf-8")
    )

    if checked.get("status") != "complete":
        raise ValueError("report status invalid")

    os.chmod(json_tmp, 0o600)
    os.chmod(text_tmp, 0o600)
    os.replace(json_tmp, json_path)
    os.replace(text_tmp, text_path)
finally:
    for temporary in (json_tmp, text_tmp):
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
__MP_REPORT_WRITE_PY__

  ok "Đã tạo báo cáo hoàn tất không chứa secret"
}

upload_completion_report() {
  local remote listing

  remote="$(backup_remote_for after)"

  [ -n "$remote" ] || {
    warn "Không xác định được thư mục backup after trên Drive"
    return 1
  }

  [ -s "$REPORT_JSON" ] &&
  [ -s "$REPORT_TEXT" ] || {
    warn "Thiếu báo cáo hoàn tất cục bộ"
    return 1
  }

  rclone copyto \
    "$REPORT_JSON" \
    "$remote/provision_report.json" ||
    return 1

  rclone copyto \
    "$REPORT_TEXT" \
    "$remote/provision_report.txt" ||
    return 1

  listing="$(
    rclone lsf "$remote" --files-only 2>/dev/null ||
      true
  )"

  printf '%s\n' "$listing" |
    grep -Fxq 'provision_report.json' ||
    return 1

  printf '%s\n' "$listing" |
    grep -Fxq 'provision_report.txt' ||
    return 1

  state_set "report_remote=$remote"
  ok "Đã upload và xác nhận báo cáo hoàn tất"
}

done_checkpoint() {
  local checkpoint="$1" phase now

  [ -s "$STATE" ] ||
    die "Chưa khởi tạo mprovision"

  phase="$(state_get phase)"
  now="$(utc_now)"

  case "$checkpoint:$phase" in
    pre:manual_pre)
      state_set \
        "manual_pre_confirmed_at=$now" \
        "phase=automatic"
      ok "Đã xác nhận THỦ CÔNG 1"
      automatic
      ;;
    post:manual_post)
      state_set \
        "manual_post_confirmed_at=$now" \
        "phase=finalize"
      ok "Đã xác nhận THỦ CÔNG 2"
      finalize
      ;;
    pre:*)
      die "Không ở checkpoint THỦ CÔNG 1; phase=$phase"
      ;;
    post:*)
      die "Không ở checkpoint THỦ CÔNG 2; phase=$phase"
      ;;
    *)
      die "Checkpoint không hợp lệ: $checkpoint"
      ;;
  esac
}

show_report() {
  local phase completed_at

  [ -s "$STATE" ] ||
    die "Chưa khởi tạo mprovision"

  phase="$(state_get phase)"

  [ "$phase" = "complete" ] ||
    die "Máy chưa hoàn tất; phase=$phase"

  final_check

  completed_at="$(state_get completed_at)"

  if [ -z "$completed_at" ]; then
    completed_at="$(utc_now)"
    state_set "completed_at=$completed_at"
  fi

  write_completion_report "$(agent_count)"

  if rclone_ok && [ -n "$(state_get backup_after)" ]; then
    upload_completion_report ||
      die "Không upload được báo cáo hoàn tất"
  else
    warn "Báo cáo chỉ được tạo cục bộ; Drive chưa sẵn sàng"
  fi

  cat "$REPORT_TEXT"
}

pause_pre() {
  state_set \
    "phase=manual_pre" \
    "manual_pre_confirmed_at="
  show_manual_pre
}

pause_rclone() {
  state_set "phase=await_rclone_$1"
  cat <<'__MP_RCLONE__'

========== CẦN RCLONE ==========
Cấu hình remote tên chính xác gdrive: trên máy này.
Kết quả đúng: rclone lsd gdrive: chạy thành công.
Không gửi token hoặc rclone.conf vào chat.

Xong chạy: mprovision resume
================================
__MP_RCLONE__
}

pause_post() {
  state_set \
    "phase=manual_post" \
    "manual_post_confirmed_at="
  show_manual_post
}

preflight() {
  local cmd
  if ! root_ok; then
    state_set phase=await_root
    echo "ROOT chưa hoạt động. Bật root, kiểm tra su -c id, rồi chạy mprovision resume."
    return
  fi
  for cmd in bash curl python zip unzip sha256sum; do
    command -v "$cmd" >/dev/null 2>&1 || die "Thiếu lệnh $cmd"
  done
  install_swift
  pause_pre
}

automatic() {
  if ! root_ok; then
    state_set phase=await_root_setup
    echo "ROOT không hoạt động. Bật lại root rồi chạy mprovision resume."
    return
  fi
  rclone_ok || { pause_rclone before; return; }
  state_set phase=automatic

  if [ -n "$(state_get backup_before)" ]; then
    ok "Backup before đã hoàn tất; không tạo lại"
  else
    backup_data before 0
  fi

  run_msetup
  install_winterhub
  pause_post
}

finalize() {
  local completed_at

  rclone_ok || {
    pause_rclone after
    return
  }

  state_set phase=finalize
  final_check

  if [ -n "$(state_get backup_after)" ]; then
    ok "Backup after đã hoàn tất; không tạo lại"
  else
    backup_data after 1
  fi

  completed_at="$(state_get completed_at)"

  if [ -z "$completed_at" ]; then
    completed_at="$(utc_now)"
    state_set "completed_at=$completed_at"
  fi

  write_completion_report "$(agent_count)"

  upload_completion_report ||
    die "Không upload được báo cáo hoàn tất"

  state_set \
    "phase=complete" \
    "report_json=$REPORT_JSON" \
    "report_text=$REPORT_TEXT"

  echo "MPROVISION=HOÀN_TẤT"
  status
}

status() {
  local phase pre_status post_status report_status next

  [ -s "$STATE" ] || {
    echo "MPROVISION_STATUS=CHƯA_KHỞI_TẠO"
    return
  }

  phase="$(state_get phase)"
  pre_status="PENDING"
  post_status="PENDING"
  report_status="MISSING"
  next="mprovision resume"

  [ -z "$(state_get manual_pre_confirmed_at)" ] ||
    pre_status="CONFIRMED"

  [ -z "$(state_get manual_post_confirmed_at)" ] ||
    post_status="CONFIRMED"

  [ ! -s "$REPORT_JSON" ] ||
    report_status="READY"

  case "$phase" in
    manual_pre)
      next="mprovision done pre"
      ;;
    manual_post)
      next="mprovision done post"
      ;;
    complete)
      next="mprovision report"
      ;;
  esac

  echo "VERSION=$VERSION"
  echo "DEVICE_ID=$(state_get device_id)"
  echo "DEVICE_GROUP=$(state_get device_group)"
  echo "PHASE=$phase"
  echo "RUN_ID=$(state_get run_id)"
  echo "MANUAL_PRE=$pre_status"
  echo "MANUAL_POST=$post_status"
  echo "REPORT=$report_status"
  echo "NEXT=$next"
}

resume() {
  local phase

  [ -s "$STATE" ] ||
    die "Chưa khởi tạo mprovision"

  phase="$(state_get phase)"

  case "$phase" in
    preflight|await_root)
      preflight
      ;;
    manual_pre)
      show_manual_pre
      ;;
    await_root_setup|await_rclone_before|automatic)
      automatic
      ;;
    manual_post)
      show_manual_post
      ;;
    await_rclone_after|finalize)
      finalize
      ;;
    complete)
      status
      ;;
    *)
      die "Phase không hợp lệ: ${phase:-EMPTY}"
      ;;
  esac
}

self_test() {
  local tmp
  local old_state old_state_dir old_sd old_dl
  local old_shouko old_delta old_backups old_winter
  local old_report_json old_report_text
  local value expected_remote

  tmp="$(mktemp -d)"

  old_state="$STATE"
  old_state_dir="$STATE_DIR"
  old_sd="$SD"
  old_dl="$DL"
  old_shouko="$SHOUKO"
  old_delta="$DELTA"
  old_backups="$BACKUPS"
  old_winter="$WINTERHUB"
  old_report_json="$REPORT_JSON"
  old_report_text="$REPORT_TEXT"

  STATE_DIR="$tmp/state"
  STATE="$STATE_DIR/state.json"
  SD="$tmp/sd"
  DL="$SD/Download"
  SHOUKO="$DL/Shouko"
  DELTA="$SD/Delta"
  BACKUPS="$SD/backups"
  WINTERHUB="$tmp/winterhub.sh"
  REPORT_JSON="$SHOUKO/provision_report.json"
  REPORT_TEXT="$SHOUKO/provision_report.txt"

  value="$(norm_id 116)"
  [ "$value" = m116 ] ||
    die "self-test id"

  value="$(norm_group NOVA)"
  [ "$value" = NOVA ] ||
    die "self-test group"

  state_set \
    version="$VERSION" \
    device_id=m116 \
    device_group=NOVA \
    phase=complete \
    run_id=test \
    manual_pre_confirmed_at=2026-01-01T00:00:00Z \
    manual_post_confirmed_at=2026-01-01T01:00:00Z \
    backup_before="$BACKUPS/m116/20260101-before" \
    backup_before_remote= \
    backup_after="$BACKUPS/m116/20260101-after" \
    backup_after_remote= \
    completed_at=2026-01-01T02:00:00Z

  [ "$(state_get phase)" = complete ] ||
    die "self-test state"

  expected_remote="gdrive:/Aotscript-Backups/m116/20260101-after"

  [ "$(backup_remote_for after)" = "$expected_remote" ] ||
    die "self-test derive remote"

  mkdir -p "$SHOUKO" "$DELTA" "$tmp/out"

  printf 's\n' > "$SHOUKO/test.txt"
  printf 'd\n' > "$DELTA/test.txt"

  zip_shouko "$tmp/out/Shouko.zip"
  zip_delta "$tmp/out/Delta.zip"
  unzip -tqq "$tmp/out/Shouko.zip"
  unzip -tqq "$tmp/out/Delta.zip"

  install_winterhub
  bash -n "$WINTERHUB"

  write_completion_report 1

  python - \
    "$REPORT_JSON" \
    "$REPORT_TEXT" \
    "$expected_remote" \
    <<'__MP_PHASE2_SELF_TEST_PY__'
import json
import pathlib
import sys

json_path = pathlib.Path(sys.argv[1])
text_path = pathlib.Path(sys.argv[2])
expected_remote = sys.argv[3]

data = json.loads(
    json_path.read_text(encoding="utf-8")
)

expected_keys = {
    "schema_version",
    "status",
    "provision_version",
    "device_id",
    "device_group",
    "run_id",
    "completed_at",
    "generated_at",
    "manual_pre_confirmed_at",
    "manual_post_confirmed_at",
    "backup_before_local",
    "backup_before_remote",
    "backup_after_local",
    "backup_after_remote",
    "agent_process_count",
    "final_check",
}

if set(data) != expected_keys:
    raise SystemExit("report keys invalid")

if data["status"] != "complete":
    raise SystemExit("report status invalid")

if data["backup_after_remote"] != expected_remote:
    raise SystemExit("report remote invalid")

if data["agent_process_count"] != 1:
    raise SystemExit("report agent count invalid")

sensitive_fragments = {
    "password",
    "passwd",
    "token",
    "secret",
    "api_key",
    "agent_report_secret",
    "worker_report_url",
}

serialized = json.dumps(data).lower()

for fragment in sensitive_fragments:
    if fragment in serialized:
        raise SystemExit(
            f"sensitive fragment in report: {fragment}"
        )

text = text_path.read_text(encoding="utf-8")

if "STATUS=complete" not in text:
    raise SystemExit("text report invalid")
__MP_PHASE2_SELF_TEST_PY__

  checklist >/dev/null
  python -m json.tool "$STATE" >/dev/null

  rm -rf "$tmp"

  STATE="$old_state"
  STATE_DIR="$old_state_dir"
  SD="$old_sd"
  DL="$old_dl"
  SHOUKO="$old_shouko"
  DELTA="$old_delta"
  BACKUPS="$old_backups"
  WINTERHUB="$old_winter"
  REPORT_JSON="$old_report_json"
  REPORT_TEXT="$old_report_text"

  echo "MPROVISION_PHASE2_SELF_TEST=OK"
}

main() {
  local id group run

  case "${1:-}" in
    self-test)
      [ "$#" = 1 ] ||
        die "self-test không nhận tham số"
      self_test
      ;;
    status)
      [ "$#" = 1 ] ||
        die "status không nhận tham số"
      install_wrapper
      status
      ;;
    checklist)
      [ "$#" = 1 ] ||
        die "checklist không nhận tham số"
      install_wrapper
      checklist
      ;;
    done)
      [ "$#" = 2 ] ||
        die "Cách dùng: mprovision done pre|post"
      install_wrapper
      done_checkpoint "$2"
      ;;
    resume)
      [ "$#" = 1 ] ||
        die "resume không nhận tham số"
      install_wrapper
      resume
      ;;
    report)
      [ "$#" = 1 ] ||
        die "report không nhận tham số"
      install_wrapper
      show_report
      ;;
    -h|--help|help|"")
      usage
      ;;
    *)
      [ "$#" = 2 ] || {
        usage
        die "Cần device_id và group"
      }

      id="$(norm_id "$1")" ||
        die "Device ID không hợp lệ: $1"

      group="$(norm_group "$2")" ||
        die "Nhóm không hợp lệ: $2"

      run="$(date +%Y%m%d-%H%M%S)"

      install_wrapper
      mkdir -p "$STATE_DIR"

      [ ! -s "$STATE" ] ||
        cp -p "$STATE" "$STATE.bak-$run"

      state_set \
        version="$VERSION" \
        device_id="$id" \
        device_group="$group" \
        phase=preflight \
        run_id="$run" \
        backup_before= \
        backup_before_remote= \
        backup_after= \
        backup_after_remote= \
        swift_install=0 \
        manual_pre_confirmed_at= \
        manual_post_confirmed_at= \
        completed_at= \
        report_remote= \
        report_json= \
        report_text=

      preflight
      ;;
  esac
}

main "$@"
