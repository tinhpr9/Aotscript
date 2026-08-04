DISABLE_OLD_BOOT=0 bash <<'BASH'
set -u

ok()   { echo "✅ $*"; }
warn() { echo "⚠️ $*"; }
die()  { echo "❌ $*"; exit 1; }
root() { su -c "$1"; }

STAMP="$(date +%Y%m%d-%H%M%S)"
SD="/storage/emulated/0"
DL="$SD/Download"
BACKUP="$DL/m166_settings_before_${STAMP}.txt"
DISABLED="$HOME/.termux/boot-disabled/$STAMP"

KEYS=(
  development_settings_enabled
  force_allow_on_external
  force_resizable_activities
  enable_freeform_support
  force_desktop_mode_on_external_displays
)

echo "=== TỰ ĐỘNG CẤU HÌNH m166 ==="

root id 2>/dev/null | grep -q 'uid=0(root)' ||
  die "ROOT không hoạt động"
ok "ROOT hoạt động"

mkdir -p "$DL"

{
  echo "time=$STAMP"
  echo "size=$(root 'wm size' 2>&1)"
  echo "density=$(root 'wm density' 2>&1)"

  for key in "${KEYS[@]}"; do
    echo "$key=$(root "settings get global $key" 2>/dev/null)"
  done
} > "$BACKUP" || die "Không tạo được bản sao lưu"

ok "Đã sao lưu: $BACKUP"

if [ "$DISABLE_OLD_BOOT" = "1" ]; then
  moved=0

  for name in winterhub.sh winterhub.sh.bak; do
    src="$HOME/.termux/boot/$name"

    if [ -f "$src" ]; then
      mkdir -p "$DISABLED"
      mv "$src" "$DISABLED/$name" ||
        die "Không chuyển được $name"
      ok "Đã giữ an toàn: $name"
      moved=1
    fi
  done

  [ "$moved" = "1" ] &&
    echo "Boot cũ được giữ tại: $DISABLED" ||
    echo "[*] Không có winterhub cũ cần chuyển"
else
  echo "[*] Giữ nguyên boot script hiện tại"
fi

for key in "${KEYS[@]}"; do
  if root "settings put global $key 1"; then
    ok "$key=1"
  else
    warn "Không đặt được $key"
  fi
done

if command -v gdown >/dev/null 2>&1; then
  ok "gdown đã có"
else
  echo "[*] Đang cài gdown..."
  python -m pip install --upgrade gdown ||
    die "Cài gdown thất bại"
  hash -r
  command -v gdown >/dev/null 2>&1 ||
    die "Đã cài nhưng không tìm thấy gdown"
  ok "Cài gdown thành công"
fi

SIZE_OUT="$(root 'wm size' 2>/dev/null)"
SIZE="$(
  printf '%s\n' "$SIZE_OUT" |
    sed -n 's/.*Physical size: \([0-9][0-9]*x[0-9][0-9]*\).*/\1/p' |
    head -n 1
)"

if [[ "$SIZE" =~ ^([0-9]+)x([0-9]+)$ ]]; then
  WIDTH="${BASH_REMATCH[1]}"
  HEIGHT="${BASH_REMATCH[2]}"

  if [ "$WIDTH" -lt "$HEIGHT" ]; then
    SHORT="$WIDTH"
  else
    SHORT="$HEIGHT"
  fi

  DENSITY=$(( (SHORT * 160 + 350) / 700 ))
  [ "$DENSITY" -lt 72 ] && DENSITY=72

  root "wm density $DENSITY" ||
    die "Không đặt được density"

  sleep 2
  ok "Đã đặt density=$DENSITY, gần 700 dp"
else
  warn "Không đọc được Physical size; chưa đổi density"
fi

PKG="com.google.android.inputmethod.latin"
IME="$PKG/com.android.inputmethod.latin.LatinIME"

echo
echo "=== CẤU HÌNH GBOARD ==="

if root "pm path $PKG" >/dev/null 2>&1; then
  root "pm enable $PKG" >/dev/null 2>&1 || true
  root "ime enable '$IME'" >/dev/null 2>&1 || true

  if root "ime set '$IME'"; then
    ok "Đã đặt Gboard làm mặc định"
  else
    warn "Không đặt được Gboard làm mặc định"
  fi

  echo "IME: $(root 'settings get secure default_input_method' 2>/dev/null)"

  VERSION="$(
    root "dumpsys package $PKG" 2>/dev/null |
      sed -n 's/^[[:space:]]*versionName=//p' |
      head -n 1
  )"

  echo "Gboard: ${VERSION:-không đọc được phiên bản}"
else
  warn "Gboard chưa được cài"
fi

if root "am start -a android.intent.action.VIEW \
-d 'market://details?id=$PKG' \
-p com.android.vending" >/dev/null 2>&1; then
  ok "Đã mở trang Gboard trong CH Play"
else
  warn "Không mở được trang Gboard trong CH Play"
fi

command -v unzip >/dev/null 2>&1 ||
  die "Thiếu unzip"

download_zip() {
  local id="$1"
  local out="$2"
  local part="${out}.part.$$"
  local list

  echo
  echo "[*] Đang tải: $out"
  rm -f "$part"

  if ! gdown "$id" -O "$part"; then
    rm -f "$part"
    die "Tải thất bại: $out"
  fi

  [ -s "$part" ] || {
    rm -f "$part"
    die "File tải về trống: $out"
  }

  if ! unzip -t "$part" >/dev/null 2>&1; then
    rm -f "$part"
    die "ZIP bị lỗi: $out"
  fi

  mv -f "$part" "$out" ||
    die "Không lưu được: $out"

  ok "ZIP hợp lệ: $out"

  echo "Cấu trúc cấp đầu:"
  list="$(unzip -Z1 "$out")" ||
    die "Không đọc được cấu trúc ZIP"

  printf '%s\n' "$list" |
    awk -F/ 'NF && $1 != "" {print $1}' |
    sort -u |
    sed 's/^/  - /'
}

extract_safe() {
  local zip="$1"
  local dest="$2"
  local expected="$3"
  local target="$dest/$expected"
  local list

  list="$(unzip -Z1 "$zip")" ||
    die "Không đọc được $zip"

  printf '%s\n' "$list" |
    grep -q "^${expected}/" ||
    die "$zip không có thư mục cấp đầu $expected"

  if [ -e "$target" ] && [ ! -d "$target" ]; then
    die "$target tồn tại nhưng không phải thư mục"
  fi

  if [ -d "$target" ] &&
     [ -n "$(find "$target" -mindepth 1 -print -quit 2>/dev/null)" ]; then
    warn "$target đã có dữ liệu; bỏ qua giải nén để tránh ghi đè"
    return 0
  fi

  mkdir -p "$dest"

  unzip -q "$zip" -d "$dest" ||
    die "Giải nén thất bại: $zip"

  [ -d "$target" ] ||
    die "Giải nén xong nhưng không thấy $target"

  ok "Đã giải nén: $target"
}

SHOUKO_ZIP="$DL/Shouko.zip"
DELTA_ZIP="$DL/Delta.zip"

download_zip \
  "1vDjK3hNCyT0B_rbAcsPlelD-TJJKzwG1" \
  "$SHOUKO_ZIP"

download_zip \
  "1BkHn3hyDfobTcy5tqhT9LePe01OzEHQ-" \
  "$DELTA_ZIP"

extract_safe "$SHOUKO_ZIP" "$DL" "Shouko"
extract_safe "$DELTA_ZIP" "$SD" "Delta"

echo
echo "========== KẾT QUẢ =========="
root 'wm size'
root 'wm density'

for key in "${KEYS[@]}"; do
  value="$(root "settings get global $key" 2>/dev/null)"

  if [ "$value" = "1" ]; then
    ok "$key=$value"
  else
    warn "$key=$value"
  fi
done

ok "gdown=$(command -v gdown)"

for path in \
  "$DL/Shouko" \
  "$SD/Delta" \
  "$HOME/caylapbu/main"
do
  if [ -e "$path" ]; then
    ls -ld "$path"
  else
    warn "Chưa có: $path"
  fi
done

echo

# ===== TIỆN ÍCH M166: TOOLCHECK + AGENT BOOT AN TOÀN =====
echo
echo "=== CÀI TOOLCHECK VÀ AGENT BOOT AN TOÀN ==="

mkdir -p "$HOME/bin" "$HOME/.termux/boot"

# ---------------------------------------------------------
# Lệnh tắt: toolcheck
# ---------------------------------------------------------
TOOLCHECK_CMD="$HOME/bin/toolcheck"
TOOLCHECK_TMP="${TMPDIR:-/data/data/com.termux/files/usr/tmp}/toolcheck.$$"

cat > "$TOOLCHECK_TMP" <<'TOOLCHECK_EOF'
#!/data/data/com.termux/files/usr/bin/bash
set -e

PYTHON="/data/data/com.termux/files/usr/bin/python"
CURL="/data/data/com.termux/files/usr/bin/curl"
TMP="$(mktemp)"

cleanup() {
  rm -f "$TMP"
}
trap cleanup EXIT

if ! "$PYTHON" -c 'import requests' >/dev/null 2>&1; then
  echo "[*] Đang cài requests..."
  "$PYTHON" -m pip install --upgrade requests
fi

echo "[*] Đang tải Toolcheck mới nhất..."

"$CURL" -fsSL \
  --retry 3 \
  --connect-timeout 15 \
  "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/Toolcheck?t=$(date +%s)" \
  -o "$TMP"

[ -s "$TMP" ] || {
  echo "[LỖI] Toolcheck tải về bị trống."
  exit 1
}

"$PYTHON" -c '
import pathlib
import sys

file = pathlib.Path(sys.argv[1])
source = file.read_text(encoding="utf-8")
compile(source, str(file), "exec")
' "$TMP"

"$PYTHON" "$TMP"
TOOLCHECK_EOF

chmod 700 "$TOOLCHECK_TMP"

if [ -f "$TOOLCHECK_CMD" ] &&
   cmp -s "$TOOLCHECK_TMP" "$TOOLCHECK_CMD"; then
  rm -f "$TOOLCHECK_TMP"
  ok "Lệnh toolcheck đã đúng"
else
  if [ -f "$TOOLCHECK_CMD" ]; then
    cp -p "$TOOLCHECK_CMD" "${TOOLCHECK_CMD}.bak-${STAMP}"
  fi

  mv "$TOOLCHECK_TMP" "$TOOLCHECK_CMD"
  chmod 700 "$TOOLCHECK_CMD"
  ok "Đã cài lệnh: toolcheck"
fi

case ":$PATH:" in
  *":$HOME/bin:"*) ;;
  *)
    grep -qxF 'export PATH="$HOME/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null ||
      echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc"
    export PATH="$HOME/bin:$PATH"
    ;;
esac


# ---------------------------------------------------------
# Lệnh tắt: setdevice
# Chạy một lần cho mỗi máy:
#   setdevice m62 MARMOT
#   setdevice m166 NOVA
# ---------------------------------------------------------
SETDEVICE_CMD="$HOME/bin/setdevice"
SETDEVICE_TMP="${TMPDIR:-/data/data/com.termux/files/usr/tmp}/setdevice.$$"

cat > "$SETDEVICE_TMP" <<'SETDEVICE_EOF'
#!/data/data/com.termux/files/usr/bin/bash
set -e

NAME_RAW="${1:-}"
GROUP_RAW="${2:-}"

DIR="/sdcard/Download/Shouko"
ID_FILE="$DIR/device_id.txt"
GROUP_FILE="$DIR/device_group.txt"
STATE_FILE="$DIR/agent_state.json"

usage() {
  echo "Cách dùng:"
  echo "  setdevice m62 MARMOT"
  echo "  setdevice m166 NOVA"
}

if [ -z "$NAME_RAW" ]; then
  usage
  exit 1
fi

NAME="$(
  printf '%s' "$NAME_RAW" |
    tr '[:upper:]' '[:lower:]'
)"

if [[ ! "$NAME" =~ ^m[1-9][0-9]{0,5}$ ]]; then
  echo "[LỖI] Tên máy không hợp lệ: $NAME_RAW"
  echo "Ví dụ: m62, m166, m1000"
  exit 1
fi

mkdir -p "$DIR"

if [ -z "$GROUP_RAW" ] &&
   [ -s "$GROUP_FILE" ]; then
  GROUP_RAW="$(cat "$GROUP_FILE")"
fi

if [ -z "$GROUP_RAW" ]; then
  read -r -p \
    "Nhập profile MARMOT hoặc NOVA: " \
    GROUP_RAW
fi

GROUP="$(
  printf '%s' "$GROUP_RAW" |
    tr -d '\r\n ' |
    tr '[:lower:]' '[:upper:]'
)"

case "$GROUP" in
  MARMOT|NOVA) ;;
  *)
    echo "[LỖI] Profile không hợp lệ: $GROUP_RAW"
    usage
    exit 1
    ;;
esac

OLD_ID="$(
  cat "$ID_FILE" 2>/dev/null |
    tr -d '\r\n ' || true
)"

OLD_GROUP="$(
  cat "$GROUP_FILE" 2>/dev/null |
    tr -d '\r\n ' |
    tr '[:lower:]' '[:upper:]' || true
)"

if [ "$OLD_ID" = "$NAME" ] &&
   [ "$OLD_GROUP" = "$GROUP" ]; then
  echo "[OK] Máy đã được cấu hình:"
  echo "device_id=$NAME"
  echo "device_group=$GROUP"
  exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)"

for file in \
  "$ID_FILE" \
  "$GROUP_FILE" \
  "$STATE_FILE"
do
  if [ -f "$file" ]; then
    cp -p \
      "$file" \
      "${file}.bak-${STAMP}"
  fi
done

printf '%s\n' "$NAME" \
  > "${ID_FILE}.tmp"

printf '%s\n' "$GROUP" \
  > "${GROUP_FILE}.tmp"

mv "${ID_FILE}.tmp" "$ID_FILE"
mv "${GROUP_FILE}.tmp" "$GROUP_FILE"

cat > "${STATE_FILE}.tmp" <<'STATE_EOF'
{
  "device_group": "",
  "common_command_hash": "",
  "group_command_hash": "",
  "last_processed_at": "",
  "last_command_id": "",
  "last_result": ""
}
STATE_EOF

mv "${STATE_FILE}.tmp" "$STATE_FILE"

echo "[OK] Đã cấu hình máy:"
echo "device_id=$NAME"
echo "device_group=$GROUP"
echo "[OK] Chỉ cần đặt tên một lần."
echo "[OK] State cũ đã được sao lưu và làm sạch."
SETDEVICE_EOF

chmod 700 "$SETDEVICE_TMP"

if [ -f "$SETDEVICE_CMD" ] &&
   cmp -s \
     "$SETDEVICE_TMP" \
     "$SETDEVICE_CMD"
then
  rm -f "$SETDEVICE_TMP"
  ok "Lệnh setdevice đã đúng"
else
  if [ -f "$SETDEVICE_CMD" ]; then
    cp -p \
      "$SETDEVICE_CMD" \
      "${SETDEVICE_CMD}.bak-${STAMP}"
  fi

  mv \
    "$SETDEVICE_TMP" \
    "$SETDEVICE_CMD"

  chmod 700 "$SETDEVICE_CMD"
  ok "Đã cài lệnh: setdevice"
fi

# ---------------------------------------------------------
# Agent boot an toàn
# ---------------------------------------------------------
AGENT_BOOT="$HOME/.termux/boot/01-agent.sh"
AGENT_BOOT_TMP="${TMPDIR:-/data/data/com.termux/files/usr/tmp}/01-agent.sh.$$"

cat > "$AGENT_BOOT_TMP" <<'AGENT_BOOT_EOF'
#!/data/data/com.termux/files/usr/bin/bash

export PATH="/data/data/com.termux/files/usr/bin:$PATH"

PYTHON="/data/data/com.termux/files/usr/bin/python"
CURL="/data/data/com.termux/files/usr/bin/curl"

AGENT="/sdcard/Download/Agent_Core.py"
TEMP="/sdcard/Download/Agent_Core.py.tmp"
LOG="/sdcard/Download/Agent_Log.txt"

ID_FILE="/sdcard/Download/Shouko/device_id.txt"
GROUP_FILE="/sdcard/Download/Shouko/device_group.txt"

mkdir -p "/sdcard/Download/Shouko"

{
  echo
  echo "===== AGENT BOOT $(date '+%Y-%m-%d %H:%M:%S') ====="

  termux-wake-lock 2>/dev/null || true
  sleep 15

  # Không tắt Agent đang hoạt động.
  if pgrep -af '[/]sdcard/Download/Agent_Core.py' >/dev/null 2>&1; then
    echo "[SKIP] Agent đang chạy, không khởi động thêm:"
    pgrep -af '[/]sdcard/Download/Agent_Core.py'
    exit 0
  fi

  if [ ! -s "$ID_FILE" ]; then
    echo "[SKIP] Chưa có hoặc file trống: $ID_FILE"
    exit 0
  fi

  if [ ! -s "$GROUP_FILE" ]; then
    echo "[SKIP] Chưa có hoặc file trống: $GROUP_FILE"
    exit 0
  fi

  DEVICE_ID="$(
    tr -d '\r\n ' < "$ID_FILE" |
      tr '[:lower:]' '[:upper:]'
  )"

  DEVICE_GROUP="$(
    tr -d '\r\n ' < "$GROUP_FILE" |
      tr '[:lower:]' '[:upper:]'
  )"


  if [[ "$DEVICE_ID" =~ ^M[1-9][0-9]{0,5}$ ]]; then
    echo "[OK] device_id động=$DEVICE_ID"
  else
    case "$DEVICE_ID" in
      MARMOT-0[1-9]|MARMOT-10|NOVA-0[1-9]|NOVA-10)
        echo "[OK] device_id cũ=$DEVICE_ID"
        ;;
      *)
        echo "[SKIP] device_id không hợp lệ: $DEVICE_ID"
        echo "[SKIP] Dùng: setdevice m62 MARMOT"
        exit 0
        ;;
    esac
  fi

  case "$DEVICE_GROUP" in
    MARMOT|NOVA)
      echo "[OK] device_group=$DEVICE_GROUP"
      ;;
    *)
      echo "[SKIP] device_group không hợp lệ: $DEVICE_GROUP"
      exit 0
      ;;
  esac

  rm -f "$TEMP"

  if ! "$CURL" -fsSL \
    --retry 3 \
    --connect-timeout 15 \
    "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/agent?t=$(date +%s)" \
    -o "$TEMP"
  then
    echo "[LỖI] Không tải được Agent từ GitHub."
    rm -f "$TEMP"
    exit 1
  fi

  if ! "$PYTHON" -c '
import pathlib
import sys

file = pathlib.Path(sys.argv[1])
source = file.read_text(encoding="utf-8")
compile(source, str(file), "exec")
' "$TEMP"
  then
    echo "[LỖI] Agent tải về sai cú pháp."
    rm -f "$TEMP"
    exit 1
  fi

  mv -f "$TEMP" "$AGENT"
  chmod 600 "$AGENT"

  nohup "$PYTHON" -u "$AGENT" >> "$LOG" 2>&1 &
  PID=$!

  echo "[OK] Đã khởi động Agent PID=$PID"
} >> "$LOG" 2>&1
AGENT_BOOT_EOF

chmod 700 "$AGENT_BOOT_TMP"

if [ -f "$AGENT_BOOT" ] &&
   cmp -s "$AGENT_BOOT_TMP" "$AGENT_BOOT"; then
  rm -f "$AGENT_BOOT_TMP"
  ok "01-agent.sh đã đúng"
else
  if [ -f "$AGENT_BOOT" ]; then
    cp -p "$AGENT_BOOT" "${AGENT_BOOT}.bak-${STAMP}"
  fi

  mv "$AGENT_BOOT_TMP" "$AGENT_BOOT"
  chmod 700 "$AGENT_BOOT"
  ok "Đã tạo 01-agent.sh an toàn"
fi

echo "[+] Dùng Toolcheck bằng lệnh: toolcheck"
echo "[+] Agent đang chạy sẽ được giữ nguyên."
echo "[+] Agent hỗ trợ tên động như m62, m166."
echo "[+] Đặt tên một lần bằng: setdevice m62 MARMOT"
echo
echo "CHƯA REBOOT MÁY."
BASH
