#!/usr/bin/env bash
DISABLE_OLD_BOOT=0 bash -s -- "$@" <<'BASH'
set -u

ok()   { echo "✅ $*"; }
warn() { echo "⚠️ $*"; }
die()  { echo "❌ $*"; exit 1; }
root() { su -c "$1"; }

usage() {
  echo "Cách dùng:"
  echo "  msetup 62 1"
  echo "  msetup 116 2"
  echo "  msetup m166 2"
  echo "Nhóm hợp lệ: 1 hoặc 2"
}

DEVICE_ID_RAW="${1:-}"
DEVICE_GROUP_RAW="${2:-}"

[ -n "$DEVICE_ID_RAW" ] && [ -n "$DEVICE_GROUP_RAW" ] || {
  usage
  die "Thiếu device_id hoặc profile"
}

DEVICE_ID_INPUT="$(
  printf '%s' "$DEVICE_ID_RAW" |
    tr -d '\r\n ' |
    tr '[:upper:]' '[:lower:]'
)"

if [[ "$DEVICE_ID_INPUT" =~ ^[1-9][0-9]{0,5}$ ]]; then
  DEVICE_ID="m$DEVICE_ID_INPUT"
else
  DEVICE_ID="$DEVICE_ID_INPUT"
fi

DEVICE_GROUP_INPUT="$(
  printf '%s' "$DEVICE_GROUP_RAW" |
    tr -d '\r\n _-' |
    tr '[:lower:]' '[:upper:]'
)"

[[ "$DEVICE_ID" =~ ^m[1-9][0-9]{0,5}$ ]] ||
  die "Device ID không hợp lệ: $DEVICE_ID_RAW"

case "$DEVICE_GROUP_INPUT" in
  1|NHOM1|GROUP1|MARMOT)
    DEVICE_GROUP="MARMOT"
    DEVICE_GROUP_LABEL="NHÓM 1"
    ;;
  2|NHOM2|GROUP2|NOVA)
    DEVICE_GROUP="NOVA"
    DEVICE_GROUP_LABEL="NHÓM 2"
    ;;
  *)
    die "Nhóm không hợp lệ: $DEVICE_GROUP_RAW; chỉ dùng 1 hoặc 2"
    ;;
esac

validate_agent_config() {
  python - "$1" <<'PY'
import json
import pathlib
import sys
import urllib.parse

path = pathlib.Path(sys.argv[1])

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)

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
PY
}

STAMP="$(date +%Y%m%d-%H%M%S)"
SD="/storage/emulated/0"
DL="$SD/Download"
SHOUKO_DIR="$DL/Shouko"
AGENT_CONFIG="$SHOUKO_DIR/agent_config.json"
PRIVATE_AGENT_CONFIG_DIR="$HOME/.config/aotscript"
PRIVATE_AGENT_CONFIG="$PRIVATE_AGENT_CONFIG_DIR/agent_config.${DEVICE_ID}.json"
WORKER_ORIGIN="https://billowing-haze-0cafaotscript-control.tinh1020pr.workers.dev"
BACKUP="$DL/msetup_settings_before_${STAMP}.txt"
DISABLED="$HOME/.termux/boot-disabled/$STAMP"

KEYS=(
  development_settings_enabled
  force_allow_on_external
  force_resizable_activities
  enable_freeform_support
  force_desktop_mode_on_external_displays
)

echo "=== MSETUP: $DEVICE_ID / $DEVICE_GROUP_LABEL ==="

root id 2>/dev/null | grep -q 'uid=0(root)' ||
  die "ROOT không hoạt động"
ok "ROOT hoạt động"

mkdir -p "$DL"

{
  echo "time=$STAMP"
  echo "device_id=$DEVICE_ID"
  echo "device_group=$DEVICE_GROUP"
  echo "size=$(root 'wm size' 2>&1)"
  echo "density=$(root 'wm density' 2>&1)"
  for key in "${KEYS[@]}"; do
    echo "$key=$(root "settings get global $key" 2>/dev/null)"
  done
} > "$BACKUP" || die "Không tạo được bản sao lưu cài đặt"

ok "Đã sao lưu cài đặt: $BACKUP"

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
  echo "[*] Giữ nguyên mọi boot script hiện tại"
fi

for key in "${KEYS[@]}"; do
  if root "settings put global $key 1"; then
    ok "$key=1"
  else
    warn "Không đặt được $key"
  fi
done

for command in python curl unzip; do
  command -v "$command" >/dev/null 2>&1 ||
    die "Thiếu lệnh bắt buộc: $command"
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

mkdir -p "$HOME/bin" "$HOME/.termux/boot"

case ":$PATH:" in
  *":$HOME/bin:"*) ;;
  *)
    grep -qxF 'export PATH="$HOME/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null ||
      echo 'export PATH="$HOME/bin:$PATH"' >> "$HOME/.bashrc"
    export PATH="$HOME/bin:$PATH"
    ;;
esac

MSETUP_CMD="$HOME/bin/msetup"
MSETUP_TMP="${TMPDIR:-/data/data/com.termux/files/usr/tmp}/msetup.$$"

cat > "$MSETUP_TMP" <<'MSETUP_EOF'
#!/data/data/com.termux/files/usr/bin/bash
set -u

usage() {
  echo "Cách dùng:"
  echo "  msetup 62 1"
  echo "  msetup 116 2"
  echo "  msetup m166 2"
  echo "Nhóm hợp lệ: 1 hoặc 2"
}

[ "$#" -eq 2 ] || {
  usage
  exit 1
}

CURL="/data/data/com.termux/files/usr/bin/curl"
BASH="/data/data/com.termux/files/usr/bin/bash"
TMP="$(mktemp)"

cleanup() {
  rm -f "$TMP"
}
trap cleanup EXIT INT TERM

"$CURL" -fsSL \
  --retry 3 \
  --connect-timeout 15 \
  "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/setup-m166.sh?t=$(date +%s)" \
  -o "$TMP" || {
    echo "[LỖI] Không tải được setup mới nhất."
    exit 1
  }

[ -s "$TMP" ] || {
  echo "[LỖI] Setup tải về bị trống."
  exit 1
}

"$BASH" -n "$TMP" || {
  echo "[LỖI] Setup tải về sai cú pháp."
  exit 1
}

"$BASH" "$TMP" "$@"
MSETUP_EOF

chmod 700 "$MSETUP_TMP"

if [ -f "$MSETUP_CMD" ] &&
   cmp -s "$MSETUP_TMP" "$MSETUP_CMD"; then
  rm -f "$MSETUP_TMP"
  ok "Lệnh msetup đã đúng"
else
  [ ! -f "$MSETUP_CMD" ] ||
    cp -p "$MSETUP_CMD" "${MSETUP_CMD}.bak-${STAMP}"
  mv "$MSETUP_TMP" "$MSETUP_CMD" ||
    die "Không cài được msetup"
  chmod 700 "$MSETUP_CMD"
  ok "Đã cài lệnh: msetup"
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
else
  warn "Gboard chưa được cài"
fi

download_zip() {
  local id="$1"
  local out="$2"
  local part="${out}.part.$$"
  echo
  echo "[*] Đang tải: $(basename "$out")"
  rm -f "$part"
  if ! gdown "$id" -O "$part"; then
    rm -f "$part"
    die "Tải thất bại: $(basename "$out")"
  fi
  [ -s "$part" ] || {
    rm -f "$part"
    die "File tải về trống: $(basename "$out")"
  }
  if ! unzip -t "$part" >/dev/null 2>&1; then
    rm -f "$part"
    die "ZIP bị lỗi: $(basename "$out")"
  fi
  mv -f "$part" "$out" ||
    die "Không lưu được: $(basename "$out")"
  ok "ZIP hợp lệ: $(basename "$out")"
}

extract_safe() {
  local zip="$1"
  local dest="$2"
  local expected="$3"
  local target="$dest/$expected"
  local list
  list="$(unzip -Z1 "$zip")" ||
    die "Không đọc được $(basename "$zip")"
  printf '%s\n' "$list" |
    grep -q "^${expected}/" ||
    die "$(basename "$zip") không có thư mục $expected"
  if [ -e "$target" ] && [ ! -d "$target" ]; then
    die "$target tồn tại nhưng không phải thư mục"
  fi
  if [ -d "$target" ] &&
     [ -n "$(find "$target" -mindepth 1 -print -quit 2>/dev/null)" ]; then
    warn "$target đã có dữ liệu; giữ nguyên để tránh ghi đè"
    return 0
  fi
  mkdir -p "$dest"
  unzip -q "$zip" -d "$dest" ||
    die "Giải nén thất bại: $(basename "$zip")"
  [ -d "$target" ] ||
    die "Giải nén xong nhưng không thấy $target"
  ok "Đã giải nén: $target"
}

pair_agent_config() {
  local output="$1"

  python - \
    "$WORKER_ORIGIN" \
    "$DEVICE_ID" \
    "$DEVICE_GROUP" \
    "$output" <<'PY'
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

worker_origin = sys.argv[1].rstrip("/")
device_id = sys.argv[2]
device_group = sys.argv[3]
output = pathlib.Path(sys.argv[4])

request_url = worker_origin + "/agent/pair/request"
status_url = worker_origin + "/agent/pair/status"

def post_json(url, payload, timeout=20):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Cache-Control": "no-store",
            "User-Agent": "Aotscript-msetup-pair/1",
        },
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
        ) as response:
            status = response.status
            raw = response.read(65536)
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read(65536)
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
    ):
        return None, {}

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        data = {}

    if not isinstance(data, dict):
        data = {}

    return status, data

status, data = post_json(
    request_url,
    {
        "device_id": device_id,
        "device_group": device_group,
    },
)

if status not in {200, 201} or not data.get("ok"):
    error_code = data.get(
        "error",
        "pair_request_failed",
    )

    if status == 429:
        retry_after = data.get("retry_after", 60)
        print(
            "[LỖI] Yêu cầu ghép nối quá nhanh; "
            f"thử lại sau khoảng {retry_after} giây."
        )
    elif status is None:
        print(
            "[LỖI] Không kết nối được Worker "
            "để tạo yêu cầu ghép nối."
        )
    else:
        print(
            "[LỖI] Không tạo được yêu cầu ghép nối: "
            f"HTTP {status}, {error_code}"
        )

    raise SystemExit(1)

pair_id = data.get("pair_id")
pair_token = data.get("pair_token")
verification_code = data.get(
    "verification_code"
)
expires_in = data.get("expires_in", 600)
poll_after = data.get("poll_after", 3)

if (
    not isinstance(pair_id, str)
    or not pair_id
    or not isinstance(pair_token, str)
    or len(pair_token) < 40
    or not isinstance(verification_code, str)
    or len(verification_code) != 6
    or not verification_code.isdigit()
):
    print(
        "[LỖI] Worker trả dữ liệu ghép nối "
        "không hợp lệ."
    )
    raise SystemExit(1)

try:
    expires_in = int(expires_in)
    poll_after = int(poll_after)
except (TypeError, ValueError):
    print(
        "[LỖI] Thời hạn ghép nối không hợp lệ."
    )
    raise SystemExit(1)

expires_in = min(max(expires_in, 60), 600)
poll_after = min(max(poll_after, 2), 10)

print("[*] Đã gửi yêu cầu ghép nối tới Telegram.")
print(
    "[*] Mã xác minh trên máy: "
    f"{verification_code}"
)
print(
    "[*] Mở bot Telegram, đối chiếu đúng mã "
    "rồi bấm CHẤP NHẬN."
)
print(
    "[*] Đang chờ phê duyệt, tối đa 10 phút..."
)

deadline = time.monotonic() + expires_in + 10

while time.monotonic() < deadline:
    time.sleep(poll_after)

    status, data = post_json(
        status_url,
        {
            "pair_id": pair_id,
            "pair_token": pair_token,
        },
    )

    if status is None:
        continue

    if status == 202:
        continue

    if (
        status == 200
        and data.get("ok")
        and data.get("status") == "approved"
    ):
        report_url = data.get(
            "worker_report_url"
        )
        secret = data.get(
            "agent_report_secret"
        )

        expected_report_url = (
            worker_origin + "/agent/report"
        )

        if (
            not isinstance(report_url, str)
            or report_url.strip()
            != expected_report_url
            or not isinstance(secret, str)
            or not secret.strip()
        ):
            print(
                "[LỖI] Cấu hình ghép nối "
                "không hợp lệ."
            )
            raise SystemExit(1)

        parsed = urllib.parse.urlparse(
            report_url.strip()
        )

        if (
            parsed.scheme != "https"
            or not parsed.netloc
        ):
            print(
                "[LỖI] URL Worker ghép nối "
                "không dùng HTTPS hợp lệ."
            )
            raise SystemExit(1)

        temporary = output.with_name(
            output.name + ".pair-write"
        )

        try:
            temporary.unlink()
        except FileNotFoundError:
            pass

        temporary.write_text(
            json.dumps(
                {
                    "worker_report_url":
                        report_url.strip(),
                    "agent_report_secret":
                        secret.strip(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass

        temporary.replace(output)

        print(
            "[OK] Ghép nối Telegram thành công; "
            "không hiển thị secret."
        )
        raise SystemExit(0)

    error_code = data.get(
        "error",
        "pair_status_failed",
    )

    if status == 403:
        print(
            "[LỖI] Yêu cầu ghép nối "
            "đã bị từ chối."
        )
        raise SystemExit(1)

    if status == 410:
        print(
            "[LỖI] Yêu cầu ghép nối "
            "đã hết hạn."
        )
        raise SystemExit(1)

    if status in {404, 409}:
        print(
            "[LỖI] Yêu cầu ghép nối "
            f"không còn dùng được: {error_code}"
        )
        raise SystemExit(1)

    if status >= 500:
        continue

    print(
        "[LỖI] Kiểm tra ghép nối thất bại: "
        f"HTTP {status}, {error_code}"
    )
    raise SystemExit(1)

print(
    "[LỖI] Hết thời gian chờ phê duyệt "
    "ghép nối Telegram."
)
raise SystemExit(1)
PY
}

save_private_agent_config() {
  local source="$1"
  local tmp="${PRIVATE_AGENT_CONFIG}.tmp.$$"

  mkdir -p "$PRIVATE_AGENT_CONFIG_DIR"
  rm -f "$tmp"

  cp -p "$source" "$tmp" ||
    die "Không lưu được backup Agent riêng theo máy"

  chmod 600 "$tmp" 2>/dev/null || true

  validate_agent_config "$tmp" ||
    die "Backup Agent riêng theo máy không hợp lệ"

  mv -f "$tmp" "$PRIVATE_AGENT_CONFIG" ||
    die "Không thay được backup Agent riêng theo máy"

  chmod 600 "$PRIVATE_AGENT_CONFIG" 2>/dev/null || true
}

install_agent_config() {
  local tmp="${TMPDIR:-/data/data/com.termux/files/usr/tmp}/agent_config.$$"
  local source_name=""
  local current_device_id=""

  rm -f "$tmp" "${tmp}.pair-write"

  current_device_id="$(
    tr -d '\r\n ' \
      < "$SHOUKO_DIR/device_id.txt" \
      2>/dev/null |
      tr '[:upper:]' '[:lower:]' ||
      true
  )"

  if [ "$current_device_id" = "$DEVICE_ID" ] &&
     [ -s "$AGENT_CONFIG" ] &&
     validate_agent_config "$AGENT_CONFIG"; then
    chmod 600 "$AGENT_CONFIG" 2>/dev/null || true
    save_private_agent_config "$AGENT_CONFIG"
    ok "Giữ nguyên agent_config.json đúng Device ID"
    return 0
  fi

  if [ -s "$PRIVATE_AGENT_CONFIG" ]; then
    cp -p "$PRIVATE_AGENT_CONFIG" "$tmp" ||
      die "Không đọc được backup Agent riêng của $DEVICE_ID"

    if validate_agent_config "$tmp"; then
      source_name="backup riêng theo Device ID"
    else
      rm -f "$tmp"
      warn "Backup Agent riêng của $DEVICE_ID chưa hợp lệ"
    fi
  fi

  if [ -z "$source_name" ]; then
    echo
    echo "=== GHÉP NỐI AGENT QUA TELEGRAM ==="

    if pair_agent_config "$tmp" &&
       [ -s "$tmp" ] &&
       validate_agent_config "$tmp"; then
      source_name="ghép nối Telegram một lần"
    else
      rm -f "$tmp" "${tmp}.pair-write"
      die "Không hoàn tất được ghép nối Telegram"
    fi
  fi

  mkdir -p "$SHOUKO_DIR"

  if [ -f "$AGENT_CONFIG" ] &&
     cmp -s "$tmp" "$AGENT_CONFIG"; then
    rm -f "$tmp"
    chmod 600 "$AGENT_CONFIG" 2>/dev/null || true
    save_private_agent_config "$AGENT_CONFIG"
    ok "agent_config.json đã đúng cho $DEVICE_ID"
    return 0
  fi

  if [ -f "$AGENT_CONFIG" ]; then
    cp -p \
      "$AGENT_CONFIG" \
      "${AGENT_CONFIG}.bak-${STAMP}" ||
      die "Không backup được agent_config.json cũ"
  fi

  chmod 600 "$tmp" 2>/dev/null || true

  mv -f "$tmp" "$AGENT_CONFIG" ||
    die "Không cài được agent_config.json"

  chmod 600 "$AGENT_CONFIG" 2>/dev/null || true

  validate_agent_config "$AGENT_CONFIG" ||
    die "agent_config.json sau khi cài không hợp lệ"

  save_private_agent_config "$AGENT_CONFIG"

  ok "Đã cài agent_config.json từ $source_name; không hiển thị nội dung"
}

install_termux_boot_app() {
  local package="com.termux.boot"
  local meta="${TMPDIR:-/data/data/com.termux/files/usr/tmp}/termux-boot-meta.$$"
  local apk="$DL/Termux-Boot.apk"
  local apk_part="${apk}.part.$$"
  local apk_url=""
  local archive_entry=""
  local source_name=""
  local install_source=""
  local version_code=""

  if root "pm path $package" >/dev/null 2>&1; then
    ok "Termux:Boot đã được cài"
    return 0
  fi

  rm -f "$meta" "$apk_part"

  archive_entry="$(
    unzip -Z1 "$SHOUKO_ZIP" |
      awk 'tolower($0) ~ /termux[^/]*boot[^/]*\.apk$/ {print; exit}'
  )"

  if [ -n "$archive_entry" ]; then
    unzip -p "$SHOUKO_ZIP" "$archive_entry" > "$apk_part" ||
      die "Không lấy được Termux:Boot từ Shouko.zip"
    source_name="Shouko.zip riêng tư"
  else
    install_source="$(
      root "cmd package get-install-source com.termux" 2>/dev/null || true
    )"

    if printf '%s\n' "$install_source" |
         grep -q 'org\.fdroid\.fdroid'; then
      echo "[*] Đang lấy Termux:Boot từ F-Droid..."
      curl -fsSL \
        --retry 3 \
        --connect-timeout 15 \
        "https://f-droid.org/api/v1/packages/com.termux.boot" \
        -o "$meta" ||
          die "Không lấy được metadata Termux:Boot từ F-Droid"

      version_code="$(
        python - "$meta" <<'PY'
import json
import pathlib
import sys

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
value = data.get("suggestedVersionCode")
if not isinstance(value, int) or value <= 0:
    raise SystemExit(1)
print(value)
PY
      )" || {
        rm -f "$meta"
        die "Metadata F-Droid của Termux:Boot không hợp lệ"
      }

      apk_url="https://f-droid.org/repo/com.termux.boot_${version_code}.apk"
      source_name="F-Droid"
    else
      echo "[*] Đang lấy Termux:Boot từ GitHub chính thức..."
      curl -fsSL \
        --retry 3 \
        --connect-timeout 15 \
        "https://api.github.com/repos/termux/termux-boot/releases/latest" \
        -o "$meta" ||
          die "Không lấy được metadata Termux:Boot từ GitHub"

      apk_url="$(
        python - "$meta" <<'PY'
import json
import pathlib
import sys

data = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assets = data.get("assets") or []
apks = [
    item for item in assets
    if str(item.get("name", "")).lower().endswith(".apk")
    and item.get("browser_download_url")
]
if not apks:
    raise SystemExit(1)
preferred = [
    item for item in apks
    if "universal" in str(item.get("name", "")).lower()
]
print((preferred or apks)[0]["browser_download_url"])
PY
      )" || {
        rm -f "$meta"
        die "Không tìm thấy APK Termux:Boot trên GitHub"
      }

      source_name="GitHub chính thức"
    fi

    rm -f "$meta"

    curl -fsSL \
      --retry 3 \
      --connect-timeout 15 \
      "$apk_url" \
      -o "$apk_part" ||
        die "Tải Termux:Boot thất bại"
  fi

  [ -s "$apk_part" ] ||
    die "APK Termux:Boot bị trống"

  unzip -t "$apk_part" >/dev/null 2>&1 ||
    die "APK Termux:Boot không hợp lệ"

  mv -f "$apk_part" "$apk"

  root "pm install -r '$apk'" >/dev/null ||
    die "Cài Termux:Boot thất bại; hãy dùng APK cùng nguồn ký với ứng dụng Termux"

  root "pm path $package" >/dev/null 2>&1 ||
    die "Không xác nhận được Termux:Boot sau khi cài"

  root "monkey -p $package -c android.intent.category.LAUNCHER 1" \
    >/dev/null 2>&1 || true

  ok "Đã cài và mở Termux:Boot từ $source_name"
}
install_termux_boot_app

AGENT_BOOT="$HOME/.termux/boot/01-agent.sh"
AGENT_BOOT_TMP="${TMPDIR:-/data/data/com.termux/files/usr/tmp}/01-agent.sh.$$"

curl -fsSL \
  --retry 3 \
  --connect-timeout 15 \
  "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/Termuxboot?t=$(date +%s)" \
  -o "$AGENT_BOOT_TMP" ||
    die "Không tải được Termuxboot mới nhất"

[ -s "$AGENT_BOOT_TMP" ] ||
  die "Termuxboot tải về bị trống"

bash -n "$AGENT_BOOT_TMP" ||
  die "Termuxboot tải về sai cú pháp"

chmod 700 "$AGENT_BOOT_TMP"

if [ -f "$AGENT_BOOT" ] &&
   cmp -s "$AGENT_BOOT_TMP" "$AGENT_BOOT"; then
  rm -f "$AGENT_BOOT_TMP"
  ok "01-agent.sh đã đúng"
else
  [ ! -f "$AGENT_BOOT" ] ||
    cp -p "$AGENT_BOOT" "${AGENT_BOOT}.bak-${STAMP}"
  mv "$AGENT_BOOT_TMP" "$AGENT_BOOT" ||
    die "Không cài được 01-agent.sh"
  chmod 700 "$AGENT_BOOT"
  ok "Đã cài 01-agent.sh an toàn"
fi

list_agent_pids() {
  python - <<'__AOTSCRIPT_AGENT_PID_SCAN_V2_PY__'
import os
import pathlib

pids = []

for entry in os.listdir("/proc"):
    if not entry.isdigit():
        continue

    try:
        arguments = [
            part.decode(
                "utf-8",
                errors="replace",
            )
            for part in pathlib.Path(
                f"/proc/{entry}/cmdline"
            ).read_bytes().split(b"\0")
            if part
        ]
    except Exception:
        continue

    if any(
        argument.endswith(
            "/Download/Agent_Core.py"
        )
        for argument in arguments
    ):
        pids.append(int(entry))

for pid in sorted(set(pids)):
    print(pid)
__AOTSCRIPT_AGENT_PID_SCAN_V2_PY__
}

AGENT_TARGET="$DL/Agent_Core.py"
AGENT_STAGE="${AGENT_TARGET}.msetup.tmp"
AGENT_LOG="$DL/Agent_Log.txt"
AGENT_BACKUP=""

rm -f "$AGENT_STAGE"

curl -fsSL \
  --retry 3 \
  --connect-timeout 15 \
  "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/agent?t=$(date +%s)" \
  -o "$AGENT_STAGE" ||
    die "Không tải được Agent mới nhất"

[ -s "$AGENT_STAGE" ] ||
  die "Agent tải về bị trống"

python -c '
import pathlib
import sys
source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
compile(source, sys.argv[1], "exec")
' "$AGENT_STAGE" ||
  die "Agent tải về sai cú pháp"

if [ -f "$AGENT_TARGET" ] &&
   cmp -s "$AGENT_STAGE" "$AGENT_TARGET"; then
  rm -f "$AGENT_STAGE"
  ok "Agent hiện tại đã là bản mới nhất"
else
  if [ -f "$AGENT_TARGET" ]; then
    AGENT_BACKUP="${AGENT_TARGET}.bak-${STAMP}"
    cp -p "$AGENT_TARGET" "$AGENT_BACKUP" ||
      die "Không backup được Agent hiện tại"
  fi
fi

mapfile -t OLD_AGENT_PIDS < <(list_agent_pids)

if [ "${#OLD_AGENT_PIDS[@]}" -gt 0 ]; then
  echo "[*] Đang dừng Agent cũ an toàn..."
  for pid in "${OLD_AGENT_PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    mapfile -t REMAINING_PIDS < <(list_agent_pids)
    [ "${#REMAINING_PIDS[@]}" -eq 0 ] && break
  done
  mapfile -t REMAINING_PIDS < <(list_agent_pids)
  [ "${#REMAINING_PIDS[@]}" -eq 0 ] ||
    die "Agent cũ chưa dừng; không khởi động thêm tiến trình"
fi

if [ -f "$AGENT_STAGE" ]; then
  mv -f "$AGENT_STAGE" "$AGENT_TARGET" ||
    die "Không thay được Agent sau khi đã kiểm tra"
  chmod 600 "$AGENT_TARGET"
  ok "Đã thay Agent bằng bản hợp lệ mới nhất"
fi

nohup python -u "$AGENT_TARGET" >> "$AGENT_LOG" 2>&1 &
NEW_AGENT_PID=$!
sleep 2

if ! kill -0 "$NEW_AGENT_PID" 2>/dev/null; then
  if [ -n "$AGENT_BACKUP" ] &&
     [ -f "$AGENT_BACKUP" ]; then
    cp -p "$AGENT_BACKUP" "$AGENT_TARGET" || true
    nohup python -u "$AGENT_TARGET" >> "$AGENT_LOG" 2>&1 &
  fi
  die "Agent mới thoát ngay sau khi khởi động"
fi

mapfile -t NEW_AGENT_PIDS < <(list_agent_pids)

[ "${#NEW_AGENT_PIDS[@]}" -eq 1 ] ||
  die "Số tiến trình Agent không hợp lệ: ${#NEW_AGENT_PIDS[@]}"

python - "$AGENT_CONFIG" "$DEVICE_ID" "$DEVICE_GROUP" <<'PY' ||
import datetime
import json
import pathlib
import sys
import urllib.error
import urllib.request

config_path = pathlib.Path(sys.argv[1])
device_id = sys.argv[2]
device_group = sys.argv[3]

try:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    url = config["worker_report_url"].strip()
    secret = config["agent_report_secret"].strip()
    payload = {
        "device_id": device_id,
        "device_group": device_group,
        "timestamp": datetime.datetime.now(
            datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "heartbeat",
        "command_id": "",
        "last_result": "msetup",
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Aotscript-msetup/1",
            "X-Agent-Secret": secret,
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        status = response.status
        response.read()
except urllib.error.HTTPError as exc:
    print(f"[LỖI] Worker trả HTTP {exc.code}")
    raise SystemExit(1)
except Exception as exc:
    print(f"[LỖI] Heartbeat thất bại: {type(exc).__name__}")
    raise SystemExit(1)

if status != 200:
    print(f"[LỖI] Worker trả HTTP {status}, yêu cầu HTTP 200")
    raise SystemExit(1)

print("[OK] Worker heartbeat HTTP 200")
PY
  die "Không xác nhận được heartbeat HTTP 200"

echo
echo "========== HOÀN TẤT =========="
ok "device_id=$DEVICE_ID"
ok "device_group=$DEVICE_GROUP"
ok "agent_config.json hợp lệ; secret không được hiển thị"
ok "Agent đang chạy đúng 1 tiến trình"
ok "Worker heartbeat HTTP 200"
ok "Lần sau dùng: msetup $DEVICE_ID $DEVICE_GROUP"
echo "Không reboot máy."
BASH
