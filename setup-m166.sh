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
echo "CHƯA REBOOT MÁY."
BASH
