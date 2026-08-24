#!/usr/bin/env bash
DISABLE_OLD_BOOT=0 bash -s -- "$@" <<'BASH'
set -u

ok()   { echo "✅ $*"; }
warn() { echo "⚠️ $*"; }
die()  { echo "❌ $*"; exit 1; }
root() { su -c "$1"; }

resolve_canonical_revision() {
  local input_ref="${1:-${AOTSCRIPT_PROVISION_REF:-main}}"
  local resolved=""
  input_ref="$(printf '%s' "$input_ref" | tr -d '[:space:]')"
  [ -n "$input_ref" ] || input_ref="main"

  if [[ "$input_ref" =~ ^[0-9a-fA-F]{40}$ ]]; then
    printf '%s\n' "$(printf '%s' "$input_ref" | tr '[:upper:]' '[:lower:]')"
    return 0
  fi

  if [ -n "${AOTSCRIPT_RESOLVED_REVISION:-}" ] && [[ "$AOTSCRIPT_RESOLVED_REVISION" =~ ^[0-9a-fA-F]{40}$ ]]; then
    printf '%s\n' "$(printf '%s' "$AOTSCRIPT_RESOLVED_REVISION" | tr '[:upper:]' '[:lower:]')"
    return 0
  fi

  if command -v curl >/dev/null 2>&1; then
    resolved="$(curl -fsSL --retry 3 --connect-timeout 10 \
      -H "User-Agent: Aotscript-Setup" \
      -H "Accept: application/vnd.github.sha" \
      "https://api.github.com/repos/tinhpr9/Aotscript/commits/$input_ref" 2>/dev/null || true)"
    resolved="$(printf '%s' "$resolved" | tr -d '[:space:]')"
    if [[ "$resolved" =~ ^[0-9a-fA-F]{40}$ ]]; then
      printf '%s\n' "$(printf '%s' "$resolved" | tr '[:upper:]' '[:lower:]')"
      return 0
    fi
  fi

  if command -v git >/dev/null 2>&1; then
    resolved="$(git rev-parse --verify "${input_ref}^{commit}" 2>/dev/null || true)"
    resolved="$(printf '%s' "$resolved" | tr -d '[:space:]')"
    if [[ "$resolved" =~ ^[0-9a-fA-F]{40}$ ]]; then
      printf '%s\n' "$(printf '%s' "$resolved" | tr '[:upper:]' '[:lower:]')"
      return 0
    fi
  fi

  if [ "${AOTSCRIPT_SETUP_TEST_MODE:-0}" = 1 ] && [ "$input_ref" = "main" ]; then
    printf '%s\n' "0000000000000000000000000000000000000000"
    return 0
  fi

  printf '[LỖI] provision ref không hợp lệ: %s\n' "$input_ref" >&2
  return 1
}

PROVISION_REF="$(resolve_canonical_revision "${AOTSCRIPT_PROVISION_REF:-main}")" || die "Provision ref không hợp lệ"
RAW="https://raw.githubusercontent.com/tinhpr9/Aotscript/$PROVISION_REF"

aot_launcher_structure_check() {
  local candidate="$1"

  python - "$candidate" <<'__AOTSCRIPT_AOT_STRUCTURE_CHECK_PY__'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])

try:
    payload = path.read_bytes()
    text = payload.decode("utf-8")
except (OSError, UnicodeError):
    raise SystemExit(70)

if not payload or len(payload) > 1024 * 1024 or b"\0" in payload:
    raise SystemExit(1)

lines = text.splitlines()
if not lines or lines[0] != "#!/usr/bin/env bash":
    raise SystemExit(1)

required = (
    'readonly EXPECTED_ORIGIN="https://github.com/tinhpr9/Aotscript.git"',
    'readonly CONTEXT_BRANCH="aotscript-context"',
    'verify_manifest_shape()',
    'materialize_context()',
    'verify_codex_interface()',
    'exec "$codex_bin" -C "$repo_root" --add-dir "$context_dir" "$initial_instruction"',
)

if any(text.count(fragment) != 1 for fragment in required):
    raise SystemExit(1)
__AOTSCRIPT_AOT_STRUCTURE_CHECK_PY__
}

install_aot_launcher() {
  local install_dir="$HOME/bin"
  local target="$HOME/bin/aot"
  local stage=""
  local backup=""
  local rollback_stage=""
  local stamp=""
  local checker_rc=0
  local expected_sha=""
  local actual_sha=""
  local command_name=""

  for command_name in awk bash chmod cmp cp curl date mkdir mktemp mv python rm sha256sum; do
    command -v "$command_name" >/dev/null 2>&1 || {
      echo "AOT_INSTALL=FAIL"
      echo "ERROR_TYPE=MISSING_COMMAND"
      return 1
    }
  done

  mkdir -p "$install_dir" || {
    echo "AOT_INSTALL=FAIL"
    echo "ERROR_TYPE=INSTALL_DIR_FAILED"
    return 1
  }

  stage="$(mktemp "$install_dir/.aot.download.XXXXXX")" || {
    echo "AOT_INSTALL=FAIL"
    echo "ERROR_TYPE=TEMP_CREATE_FAILED"
    return 1
  }

  if ! curl -fsSL \
       --retry 3 \
       --connect-timeout 15 \
       "$RAW/aot?t=$(date +%s)" \
       -o "$stage"; then
    rm -f "$stage"
    echo "AOT_INSTALL=FAIL"
    echo "ERROR_TYPE=DOWNLOAD_FAILED"
    return 1
  fi

  if [ ! -s "$stage" ]; then
    rm -f "$stage"
    echo "AOT_INSTALL=FAIL"
    echo "ERROR_TYPE=EMPTY_DOWNLOAD"
    return 1
  fi

  if ! bash -n "$stage"; then
    rm -f "$stage"
    echo "AOT_INSTALL=FAIL"
    echo "ERROR_TYPE=SYNTAX_INVALID"
    return 1
  fi

  aot_launcher_structure_check "$stage" || checker_rc=$?
  case "$checker_rc" in
    0)
      ;;
    1)
      rm -f "$stage"
      echo "AOT_INSTALL=FAIL"
      echo "ERROR_TYPE=STRUCTURE_INVALID"
      return 1
      ;;
    *)
      rm -f "$stage"
      echo "AOT_INSTALL=FAIL"
      echo "ERROR_TYPE=CHECKER_ERROR"
      return 1
      ;;
  esac

  chmod 700 "$stage" || {
    rm -f "$stage"
    echo "AOT_INSTALL=FAIL"
    echo "ERROR_TYPE=MODE_FAILED"
    return 1
  }

  expected_sha="$(sha256sum "$stage" | awk 'NR == 1 {print $1}')" || {
    rm -f "$stage"
    echo "AOT_INSTALL=FAIL"
    echo "ERROR_TYPE=CHECKER_ERROR"
    return 1
  }

  if [ -f "$target" ] &&
     [ ! -L "$target" ] &&
     cmp -s "$stage" "$target"; then
    rm -f "$stage"
    [ -x "$target" ] || chmod 700 "$target" || {
      echo "AOT_INSTALL=FAIL"
      echo "ERROR_TYPE=MODE_FAILED"
      return 1
    }
    aot_launcher_structure_check "$target" || checker_rc=$?
    [ "$checker_rc" = 0 ] || {
      echo "AOT_INSTALL=FAIL"
      [ "$checker_rc" = 1 ] &&
        echo "ERROR_TYPE=POSTCONDITION_FAILED" ||
        echo "ERROR_TYPE=CHECKER_ERROR"
      return 1
    }
    echo "AOT_INSTALL=UNCHANGED"
    return 0
  fi

  if [ -e "$target" ] || [ -L "$target" ]; then
    if [ ! -f "$target" ] || [ -L "$target" ]; then
      rm -f "$stage"
      echo "AOT_INSTALL=FAIL"
      echo "ERROR_TYPE=TARGET_CONFLICT"
      return 1
    fi
    stamp="$(date +%Y%m%d-%H%M%S)"
    backup="${target}.bak-${stamp}-$$"
    cp -p "$target" "$backup" || {
      rm -f "$stage"
      echo "AOT_INSTALL=FAIL"
      echo "ERROR_TYPE=BACKUP_FAILED"
      return 1
    }
  fi

  if ! mv -f "$stage" "$target"; then
    rm -f "$stage"
    echo "AOT_INSTALL=FAIL"
    echo "ERROR_TYPE=ATOMIC_REPLACE_FAILED"
    return 1
  fi
  stage=""

  checker_rc=0
  aot_launcher_structure_check "$target" || checker_rc=$?
  actual_sha="$(sha256sum "$target" 2>/dev/null | awk 'NR == 1 {print $1}')"

  if [ "$checker_rc" != 0 ] ||
     [ "$actual_sha" != "$expected_sha" ] ||
     [ ! -x "$target" ]; then
    if [ -n "$backup" ] && [ -f "$backup" ]; then
      rollback_stage="$(mktemp "$install_dir/.aot.rollback.XXXXXX")" || {
        echo "AOT_INSTALL=FAIL"
        echo "ERROR_TYPE=ROLLBACK_FAILED"
        return 1
      }
      cp -p "$backup" "$rollback_stage" &&
        mv -f "$rollback_stage" "$target" || {
          rm -f "$rollback_stage"
          echo "AOT_INSTALL=FAIL"
          echo "ERROR_TYPE=ROLLBACK_FAILED"
          return 1
        }
    else
      rm -f "$target"
    fi
    echo "AOT_INSTALL=FAIL"
    [ "$checker_rc" = 0 ] &&
      echo "ERROR_TYPE=POSTCONDITION_FAILED" ||
      echo "ERROR_TYPE=CHECKER_ERROR"
    return 1
  fi

  echo "AOT_INSTALL=INSTALLED"
  echo "AOT_INSTALL_TARGET=$target"
}

if [ "${AOTSCRIPT_INSTALL_AOT_ONLY:-0}" = 1 ]; then
  install_aot_launcher ||
    die "Không cài được launcher aot"
  exit 0
fi

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
AOT_CONFIG="$SHOUKO_DIR/aot_group_config.json"
PRIVATE_AGENT_CONFIG_DIR="$HOME/.config/aotscript"
PRIVATE_AGENT_CONFIG="$PRIVATE_AGENT_CONFIG_DIR/agent_config.${DEVICE_ID}.json"
WORKER_ORIGIN="https://billowing-haze-0cafaotscript-control.tinh1020pr.workers.dev"
BACKUP="$DL/msetup_settings_before_${STAMP}.txt"
DISABLED="$HOME/.termux/boot-disabled/$STAMP"
PREVIOUS_DEVICE_ID="$(
  tr -d '\r\n ' < "$SHOUKO_DIR/device_id.txt" 2>/dev/null |
    tr '[:upper:]' '[:lower:]' || true
)"

KEYS=(
  development_settings_enabled
  force_allow_on_external
  force_resizable_activities
  enable_freeform_support
  force_desktop_mode_on_external_displays
)

echo "=== MSETUP: $DEVICE_ID / $DEVICE_GROUP_LABEL ==="

if root id 2>/dev/null | grep -q 'uid=0(root)'; then
  HAVE_ROOT=1
  ok "ROOT hoạt động — tất cả bước sẽ chạy đầy đủ"
else
  HAVE_ROOT=0
  warn "ROOT không có hoặc không hoạt động trên máy này"
  warn "Các bước cần root (wm density, settings, Gboard, Termux:Boot cài APK) sẽ bị bỏ qua"
  warn "Đăng ký AOT, Agent và relay KHÔNG cần root — tiếp tục"
fi

if [ "${AOTSCRIPT_MSETUP_TEST_MODE:-0}" = "1" ]; then
  echo "AOTSCRIPT_MSETUP_TEST_HAVE_ROOT=$HAVE_ROOT"
  exit 0
fi

mkdir -p "$DL"

if [ "$HAVE_ROOT" = 1 ]; then
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
else
  {
    echo "time=$STAMP"
    echo "device_id=$DEVICE_ID"
    echo "device_group=$DEVICE_GROUP"
    echo "root=unavailable"
  } > "$BACKUP" || die "Không tạo được bản sao lưu cài đặt"
fi

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

if [ "$HAVE_ROOT" = 1 ]; then
  for key in "${KEYS[@]}"; do
    if root "settings put global $key 1"; then
      ok "$key=1"
    else
      warn "Không đặt được $key"
    fi
  done
else
  warn "Bỏ qua developer settings (cần root): ${KEYS[*]}"
fi

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

install_aot_launcher ||
  die "Không cài được launcher aot"
hash -r
command -v aot >/dev/null 2>&1 ||
  die "Đã cài nhưng terminal không tìm thấy aot"
ok "Có thể dùng ngay bằng lệnh: aot"

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

if [ "$HAVE_ROOT" = 1 ]; then
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
    if root "wm density $DENSITY"; then
      sleep 2
      ok "Đã đặt density=$DENSITY, gần 700 dp"
    else
      warn "Không đặt được density (bỏ qua)"
    fi
  else
    warn "Không đọc được Physical size; chưa đổi density"
  fi
else
  warn "Bỏ qua điều chỉnh wm density (cần root)"
fi

PKG="com.google.android.inputmethod.latin"
IME="$PKG/com.android.inputmethod.latin.LatinIME"

echo
echo "=== CẤU HÌNH GBOARD ==="

if [ "$HAVE_ROOT" = 1 ]; then
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
else
  warn "Bỏ qua cấu hình Gboard (cần root)"
fi

download_zip() {
  local id="$1"
  local out="$2"
  local part="${out}.part.$$"
  local id_file="${out}.driveid"
  local sha_file="${out}.sha256"
  # Validate cache: Drive ID match + content digest + structural integrity.
  # NOTE: This guards against on-disk corruption and foreign file substitution.
  # It cannot detect a legitimate update of the file behind the same Drive ID;
  # such Drive-side updates require a new Drive ID or an out-of-band hash manifest.
  local cached_id="" stored_sha="" current_sha=""
  [ -f "$id_file" ] && cached_id="$(cat "$id_file" 2>/dev/null | tr -d '\r\n ' || true)"
  [ -f "$sha_file" ] && stored_sha="$(cat "$sha_file" 2>/dev/null | awk '{print $1}' || true)"
  if [ -f "$out" ] && [ "$cached_id" = "$id" ] && [ -n "$stored_sha" ]; then
    current_sha="$(sha256sum "$out" 2>/dev/null | awk '{print $1}' || true)"
    if [ "$stored_sha" = "$current_sha" ] && unzip -t "$out" >/dev/null 2>&1; then
      ok "ZIP đã có sẵn, hợp lệ và nguyên vẹn: $(basename "$out")"
      return 0
    fi
  fi
  # Cache invalid: wrong Drive ID, digest mismatch, or structurally corrupt
  [ -f "$out" ] && rm -f "$out" "$id_file" "$sha_file"
  echo
  echo "[*] Đang tải: $(basename "$out")"
  rm -f "$part"
  local try_count=0
  while [ "$try_count" -lt 3 ]; do
    if gdown "$id" -O "$part" && [ -s "$part" ] && unzip -t "$part" >/dev/null 2>&1; then
      break
    fi
    try_count=$((try_count + 1))
    rm -f "$part"
    [ "$try_count" -lt 3 ] && sleep 2
  done
  if [ ! -s "$part" ] || ! unzip -t "$part" >/dev/null 2>&1; then
    rm -f "$part"
    die "Tải thất bại hoặc ZIP bị lỗi: $(basename "$out")"
  fi
  mv -f "$part" "$out" ||
    die "Không lưu được: $(basename "$out")"
  # Record Drive ID and content digest for future cache validation
  printf '%s\n' "$id" > "$id_file"
  sha256sum "$out" > "$sha_file"
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
install_agent_config

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
source = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
compile(source, sys.argv[1], "exec")
' "$TMP"

"$PYTHON" "$TMP"
TOOLCHECK_EOF

chmod 700 "$TOOLCHECK_TMP"

if [ -f "$TOOLCHECK_CMD" ] &&
   cmp -s "$TOOLCHECK_TMP" "$TOOLCHECK_CMD"; then
  rm -f "$TOOLCHECK_TMP"
  ok "Lệnh toolcheck đã đúng"
else
  [ ! -f "$TOOLCHECK_CMD" ] ||
    cp -p "$TOOLCHECK_CMD" "${TOOLCHECK_CMD}.bak-${STAMP}"
  mv "$TOOLCHECK_TMP" "$TOOLCHECK_CMD" ||
    die "Không cài được toolcheck"
  chmod 700 "$TOOLCHECK_CMD"
  ok "Đã cài lệnh: toolcheck"
fi

TOOLCHECK_PATH="${PREFIX:-/data/data/com.termux/files/usr}/bin/toolcheck"
mkdir -p "$(dirname "$TOOLCHECK_PATH")" ||
  die "Không tạo được thư mục lệnh Termux"

if [ "$TOOLCHECK_PATH" != "$TOOLCHECK_CMD" ]; then
  if [ -e "$TOOLCHECK_PATH" ] || [ -L "$TOOLCHECK_PATH" ]; then
    if ! cmp -s "$TOOLCHECK_CMD" "$TOOLCHECK_PATH" 2>/dev/null; then
      cp -p         "$TOOLCHECK_PATH"         "${TOOLCHECK_PATH}.bak-${STAMP}" 2>/dev/null || true
    fi
    rm -f "$TOOLCHECK_PATH" ||
      die "Không thay được đường dẫn toolcheck cũ"
  fi

  ln -s "$TOOLCHECK_CMD" "$TOOLCHECK_PATH" ||
    die "Không tạo được lệnh toolcheck trong PATH"
fi

hash -r
command -v toolcheck >/dev/null 2>&1 ||
  die "Đã cài nhưng terminal vẫn không tìm thấy toolcheck"
ok "Có thể dùng ngay bằng lệnh: toolcheck"

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
  echo "  setdevice 62 1"
  echo "  setdevice m62 1"
  echo "  setdevice 116 2"
  echo "  setdevice m166 2"
}

[ -n "$NAME_RAW" ] && [ -n "$GROUP_RAW" ] || {
  usage
  exit 1
}

NAME_INPUT="$(
  printf '%s' "$NAME_RAW" |
    tr -d '\r\n ' |
    tr '[:upper:]' '[:lower:]'
)"

if [[ "$NAME_INPUT" =~ ^[1-9][0-9]{0,5}$ ]]; then
  NAME="m$NAME_INPUT"
else
  NAME="$NAME_INPUT"
fi

GROUP_INPUT="$(
  printf '%s' "$GROUP_RAW" |
    tr -d '\r\n _-' |
    tr '[:lower:]' '[:upper:]'
)"

[[ "$NAME" =~ ^m[1-9][0-9]{0,5}$ ]] || {
  echo "[LỖI] Tên máy không hợp lệ: $NAME_RAW"
  exit 1
}

case "$GROUP_INPUT" in
  1|NHOM1|GROUP1|MARMOT)
    GROUP="MARMOT"
    GROUP_LABEL="NHÓM 1"
    ;;
  2|NHOM2|GROUP2|NOVA)
    GROUP="NOVA"
    GROUP_LABEL="NHÓM 2"
    ;;
  *)
    echo "[LỖI] Nhóm không hợp lệ: $GROUP_RAW; chỉ dùng 1 hoặc 2"
    exit 1
    ;;
esac

mkdir -p "$DIR"

OLD_ID="$(
  tr -d '\r\n ' < "$ID_FILE" 2>/dev/null |
    tr '[:upper:]' '[:lower:]' || true
)"

OLD_GROUP="$(
  tr -d '\r\n ' < "$GROUP_FILE" 2>/dev/null |
    tr '[:lower:]' '[:upper:]' || true
)"

if [ "$OLD_ID" = "$NAME" ] &&
   [ "$OLD_GROUP" = "$GROUP" ]; then
  echo "[OK] Máy đã được cấu hình: $NAME / $GROUP_LABEL"
  exit 0
fi

STAMP="$(date +%Y%m%d-%H%M%S)"

for file in "$ID_FILE" "$GROUP_FILE" "$STATE_FILE"; do
  [ ! -f "$file" ] ||
    cp -p "$file" "${file}.bak-${STAMP}"
done

printf '%s\n' "$NAME" > "${ID_FILE}.tmp"
printf '%s\n' "$GROUP" > "${GROUP_FILE}.tmp"

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
echo "[OK] Đã cấu hình máy: $NAME / $GROUP_LABEL"
SETDEVICE_EOF

chmod 700 "$SETDEVICE_TMP"

if [ -f "$SETDEVICE_CMD" ] &&
   cmp -s "$SETDEVICE_TMP" "$SETDEVICE_CMD"; then
  rm -f "$SETDEVICE_TMP"
  ok "Lệnh setdevice đã đúng"
else
  [ ! -f "$SETDEVICE_CMD" ] ||
    cp -p "$SETDEVICE_CMD" "${SETDEVICE_CMD}.bak-${STAMP}"
  mv "$SETDEVICE_TMP" "$SETDEVICE_CMD" ||
    die "Không cài được setdevice"
  chmod 700 "$SETDEVICE_CMD"
  ok "Đã cài lệnh: setdevice"
fi

"$SETDEVICE_CMD" "$DEVICE_ID" "$DEVICE_GROUP" ||
  die "setdevice thất bại"

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

  # Non-root check: pm list packages does not require root
  if pm list packages 2>/dev/null | grep -Fqx "package:$package"; then
    ok "Termux:Boot đã được cài"
    return 0
  fi

  if [ "$HAVE_ROOT" != 1 ]; then
    warn "Termux:Boot chưa được cài và không có root để cài APK tự động"
    warn "Relay vẫn có thể khởi động bằng tay hoặc qua Termux session"
    warn "Để tự động khởi động khi boot: cài Termux:Boot thủ công từ F-Droid hoặc nguồn tương thích"
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

  if ! root "pm install -r '$apk'" >/dev/null 2>&1; then
    warn "Cài Termux:Boot tự động thất bại; relay vẫn hoạt động bình thường qua Termux session"
    return 0
  fi

  if ! root "pm path $package" >/dev/null 2>&1; then
    warn "Không xác nhận được Termux:Boot sau khi cài; bỏ qua"
    return 0
  fi

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
  "$RAW/Termuxboot?t=$(date +%s)" \
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
  "$RAW/agent?t=$(date +%s)" \
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

AOT_REGISTER_HELPER="${TMPDIR:-/data/data/com.termux/files/usr/tmp}/aot-msetup-registration.$$"
rm -f "$AOT_REGISTER_HELPER"
curl -fsSL \
  --retry 3 \
  --connect-timeout 15 \
  "$RAW/aot-group-control/msetup_registration.py?t=$(date +%s)" \
  -o "$AOT_REGISTER_HELPER" ||
    die "Không tải được AOT registration helper"

python -m py_compile "$AOT_REGISTER_HELPER" || {
  rm -f "$AOT_REGISTER_HELPER"
  die "AOT registration helper sai cú pháp"
}

python "$AOT_REGISTER_HELPER" configure \
  --origin "$WORKER_ORIGIN" \
  --agent-config "$AGENT_CONFIG" \
  --aot-config "$AOT_CONFIG" \
  --device-id "$DEVICE_ID" \
  --previous-device-id "$PREVIOUS_DEVICE_ID" || {
    rm -f "$AOT_REGISTER_HELPER"
    die "Không xác định duy nhất được phiên AOT đang hoạt động"
  }

if [ -n "$PREVIOUS_DEVICE_ID" ] &&
   [ "$PREVIOUS_DEVICE_ID" != "$DEVICE_ID" ]; then
  python "$AOT_REGISTER_HELPER" reset-identity \
    --origin "$WORKER_ORIGIN" \
    --agent-config "$AGENT_CONFIG" \
    --aot-config "$AOT_CONFIG" \
    --old-device-id "$PREVIOUS_DEVICE_ID" \
    --new-device-id "$DEVICE_ID" \
    --state-root "$SHOUKO_DIR" \
    --runtime-root "$HOME/.aot-group-control" || {
      rm -f "$AOT_REGISTER_HELPER"
      die "Không dọn sạch AOT identity cũ"
    }
fi

AOTSCRIPT_PROVISION_REF="$PROVISION_REF" AOTSCRIPT_AOT_REF="$PROVISION_REF" bash "$AGENT_BOOT" || {
  rm -f "$AOT_REGISTER_HELPER"
  die "Termuxboot không cài/start được AOT Bootstrap v2"
}

BOOTSTRAP_STATUS="$(
  python "$HOME/.aot-group-control/bootstrap_launcher.py" self-test 2>&1
)" || {
  rm -f "$AOT_REGISTER_HELPER"
  die "AOT Bootstrap self-test thất bại"
}
printf '%s\n' "$BOOTSTRAP_STATUS" |
  grep -qx 'AOT_BOOTSTRAP_VERSION=2' || {
    rm -f "$AOT_REGISTER_HELPER"
    die "AOT Bootstrap không phải version 2"
  }

RUNTIME_STATUS=""
runtime_ready=false
poll_i=1
while [ "$poll_i" -le 15 ]; do
  if [ -f "$HOME/.aot-group-control/current/runtime.py" ]; then
    RUNTIME_STATUS="$(
      python "$HOME/.aot-group-control/current/runtime.py" status 2>&1
    )" || true
    if printf '%s\n' "$RUNTIME_STATUS" | grep -qx 'AOT_CONFIG=OK' &&
       printf '%s\n' "$RUNTIME_STATUS" | grep -Eq '^PIDS=[0-9]+(,[0-9]+)*$'; then
      runtime_ready=true
      break
    fi
  fi
  poll_i=$((poll_i + 1))
  sleep 1
done

[ "$runtime_ready" = true ] || {
  [ -n "$RUNTIME_STATUS" ] && printf '%s\n' "$RUNTIME_STATUS"
  rm -f "$AOT_REGISTER_HELPER"
  die "AOT runtime status thất bại"
}
printf '%s\n' "$RUNTIME_STATUS"

AOT_SERVER_STATUS="$(
  python "$AOT_REGISTER_HELPER" verify \
    --origin "$WORKER_ORIGIN" \
    --agent-config "$AGENT_CONFIG" \
    --aot-config "$AOT_CONFIG" \
    --device-id "$DEVICE_ID" \
    --timeout 30
)" || {
    rm -f "$AOT_REGISTER_HELPER"
    die "Server chưa thấy máy ONLINE trong đúng AOT Hub"
  }
printf '%s\n' "$AOT_SERVER_STATUS"
printf '%s\n' "$AOT_SERVER_STATUS" | grep -qx 'AOT_SERVER_ONLINE=YES' ||
  die "Relay WebSocket chưa ONLINE trên server"
printf '%s\n' "$AOT_SERVER_STATUS" | grep -qx 'AOT_HUB_VISIBLE=YES' ||
  die "Máy chưa xuất hiện trong đúng AOT Hub"
rm -f "$AOT_REGISTER_HELPER"

echo
echo "========== HOÀN TẤT =========="
ok "device_id=$DEVICE_ID"
ok "device_group=$DEVICE_GROUP"
ok "agent_config.json hợp lệ; secret không được hiển thị"
ok "Agent đang chạy đúng 1 tiến trình"
ok "Worker heartbeat HTTP 200"
ok "AOT_CONFIG=OK và relay có PID"
ok "Relay WebSocket ONLINE và máy đã xuất hiện trong đúng AOT Hub"
ok "Lần sau dùng: msetup $DEVICE_ID $DEVICE_GROUP"
echo "Không reboot máy."
BASH
