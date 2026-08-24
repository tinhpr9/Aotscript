#!/data/data/com.termux/files/usr/bin/bash
set -Eeuo pipefail

VERSION="phase21-one-brain-core-v1"
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

PROVISION_REF="$(resolve_canonical_revision "${AOTSCRIPT_PROVISION_REF:-main}")" || exit 1
RAW="https://raw.githubusercontent.com/tinhpr9/Aotscript/$PROVISION_REF"
SWIFT_FILE_ID="1-5O8rQI9zzeVTIZcYoFmgj0gm8LW4nYI"
SD="${MPROVISION_SD:-/storage/emulated/0}"
DL="${MPROVISION_DL:-$SD/Download}"
SHOUKO="$DL/Shouko"
DELTA="$SD/Delta"
STATE_DIR="${MPROVISION_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/aotscript}"
STATE="$STATE_DIR/mprovision.json"
RUN_LOCK_DIR="$STATE_DIR/mprovision.run.lock"
BACKUPS="${MPROVISION_BACKUPS:-$SD/Aotscript-Backups}"
SWIFT_APK="$DL/SwiftBackup.apk"
WINTERHUB="${MPROVISION_WINTERHUB:-$HOME/.termux/boot/winterhub.sh}"
AGENT_BOOT="${MPROVISION_AGENT_BOOT:-$HOME/.termux/boot/01-agent.sh}"
AGENT="$DL/Agent_Core.py"
WRAPPER="$HOME/bin/mprovision"
REPORT_JSON="$SHOUKO/provision_report.json"
REPORT_TEXT="$SHOUKO/provision_report.txt"
WIZARD_SHORTCUT_DIR="${MPROVISION_SHORTCUT_DIR:-$HOME/.shortcuts/tasks}"
WIZARD_SHORTCUT="$WIZARD_SHORTCUT_DIR/AOTSCRIPT_SETUP"
WIZARD_LEGACY_SHORTCUT="$HOME/.shortcuts/AOTSCRIPT_SETUP"
WIZARD_LOG="$STATE_DIR/wizard.log"
WIZARD_SUPERVISOR="$HOME/bin/aotscript-wizard"
WIZARD_SUPERVISOR_URL="$RAW/wizard-supervisor.sh"
TERMUX_WIDGET_PACKAGE="com.termux.widget"
SWIFT_PACKAGE="org.swiftapps.swiftbackup"
SOURCE_SHOUKO_ID="1vDjK3hNCyT0B_rbAcsPlelD-TJJKzwG1"
SOURCE_DELTA_ID="1BkHn3hyDfobTcy5tqhT9LePe01OzEHQ-"
SOURCE_SHOUKO_REMOTE="gdrive:/Shouko.zip"
SOURCE_DELTA_REMOTE="gdrive:/Delta.zip"
SOURCE_HISTORY_ROOT="gdrive:/Aotscript-Source-History"

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
  mprovision intent
  mprovision reconcile
  mprovision checklist
  mprovision audit
  mprovision done pre
  mprovision done post
  mprovision resume
  mprovision report
  mprovision publish-next
  mprovision ui-post
  mprovision wizard

Quy tắc checkpoint:
  - Xong THỦ CÔNG 1: dùng mprovision done pre
  - Xong THỦ CÔNG 2: dùng mprovision done post
  - done post tự chạy audit, backup after và publish nguồn máy kế tiếp.
  - hoàn tất done post thì không cần lệnh bắt buộc nào khác.
  - publish-next chỉ dùng để publish lại từ máy đã complete.
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

run_lock_acquire() {
  local owner=""

  mkdir -p "$STATE_DIR"

  if mkdir "$RUN_LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$RUN_LOCK_DIR/pid"
    return 0
  fi

  owner="$(cat "$RUN_LOCK_DIR/pid" 2>/dev/null || true)"

  if [[ "$owner" =~ ^[0-9]+$ ]] &&
     kill -0 "$owner" 2>/dev/null; then
    die "mprovision đang chạy ở PID=$owner; không chạy đè"
  fi

  rm -f "$RUN_LOCK_DIR/pid" 2>/dev/null || true
  rmdir "$RUN_LOCK_DIR" 2>/dev/null ||
    die "Không dọn được run lock cũ"

  mkdir "$RUN_LOCK_DIR" ||
    die "Không lấy được run lock"

  printf '%s\n' "$$" > "$RUN_LOCK_DIR/pid"
}

run_lock_release() {
  local owner=""

  [ -d "$RUN_LOCK_DIR" ] || return 0
  owner="$(cat "$RUN_LOCK_DIR/pid" 2>/dev/null || true)"
  [ "$owner" = "$$" ] || return 0

  rm -f "$RUN_LOCK_DIR/pid" 2>/dev/null || true
  rmdir "$RUN_LOCK_DIR" 2>/dev/null || true
}

install_wrapper() {
  local tmp stamp prefix
  mkdir -p "$HOME/bin"
  tmp="$WRAPPER.tmp.$$"
  stamp="$(date +%Y%m%d-%H%M%S)"
  cat > "$tmp" <<'__MP_WRAPPER__'
#!/data/data/com.termux/files/usr/bin/bash
set -u

PYTHON="/data/data/com.termux/files/usr/bin/python"
CURL="/data/data/com.termux/files/usr/bin/curl"
BASH="/data/data/com.termux/files/usr/bin/bash"
SHA256SUM="/data/data/com.termux/files/usr/bin/sha256sum"
STATE_DIR="${MPROVISION_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/aotscript}"
STATE="${AOTSCRIPT_STATE_FILE:-$STATE_DIR/mprovision.json}"
CACHE_DIR="$STATE_DIR/setup-driver"
REF="${AOTSCRIPT_PROVISION_REF:-}"

if [ -z "$REF" ] && [ -s "$STATE" ]; then
  REF="$(
    "$PYTHON" - "$STATE" <<'PY_MPROVISION_WRAPPER_REF'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    print("main")
    raise SystemExit(0)

phase = str(data.get("phase", "")).strip()
ref = str(data.get("provision_ref", "")).strip()

if phase != "complete" and re.fullmatch(r"[0-9a-f]{40}", ref):
    print(ref)
else:
    print("main")
PY_MPROVISION_WRAPPER_REF
  )"
fi

[[ "$REF" =~ ^(main|[0-9a-f]{40})$ ]] || REF="main"

CACHE="$CACHE_DIR/provision-device-$REF.sh"
CACHE_SHA_FILE="$CACHE.sha256"
CACHE_SHA="$(cat "$CACHE_SHA_FILE" 2>/dev/null || true)"

if [[ "$REF" =~ ^[0-9a-f]{40}$ ]] &&
   [[ "$CACHE_SHA" =~ ^[0-9a-f]{64}$ ]] &&
   [ -s "$CACHE" ] &&
   [ "$($SHA256SUM "$CACHE" | awk 'NR == 1 {print $1}')" = "$CACHE_SHA" ] &&
   "$BASH" -n "$CACHE"; then
  AOTSCRIPT_PROVISION_REF="$REF" \
    exec "$BASH" "$CACHE" "$@"
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT INT TERM

"$CURL" -fsSL --retry 3 --connect-timeout 15 \
  "https://raw.githubusercontent.com/tinhpr9/Aotscript/$REF/provision-device.sh?t=$(date +%s)" \
  -o "$TMP" || exit 1

[ -s "$TMP" ] || exit 1
"$BASH" -n "$TMP" || exit 1

AOTSCRIPT_PROVISION_REF="$REF" \
  "$BASH" "$TMP" "$@"
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

ensure_termux_prereqs() {
  local cmd missing=0

  for cmd in python zip unzip; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing=1
    fi
  done

  if [ "$missing" = 1 ]; then
    echo "[*] Đang cài dependency Termux bắt buộc: python zip unzip"
    pkg install -y python zip unzip ||
      die "Cài dependency Termux thất bại"
    hash -r
  fi

  for cmd in python zip unzip; do
    command -v "$cmd" >/dev/null 2>&1 ||
      die "Thiếu lệnh bắt buộc sau cài đặt: $cmd"
  done

  ok "Termux prerequisites sẵn sàng"
}
apk_ok() {
  [ -s "$1" ] || return 1

  python - "$1" <<'__MP_APK_VALIDATE_PY_20260806__'
import pathlib
import sys
import zipfile

path = pathlib.Path(sys.argv[1])

try:
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise SystemExit(1)
        names = {
            name.rstrip("/")
            for name in archive.namelist()
        }
except (OSError, RuntimeError, zipfile.BadZipFile):
    raise SystemExit(1)

raise SystemExit(
    0 if "AndroidManifest.xml" in names else 1
)
__MP_APK_VALIDATE_PY_20260806__
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


extract_setup_source_ids() {
  local setup_file="$1"

  python - "$setup_file" <<'__MP_PUBLISH_SETUP_IDS_PY_20260806__'
import pathlib
import re
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
collapsed = re.sub(r"\\\n[ \t]*", " ", text)

patterns = {
    "Shouko.zip": (
        r'download_zip\s+"([^"]+)"\s+"\$SHOUKO_ZIP"'
    ),
    "Delta.zip": (
        r'download_zip\s+"([^"]+)"\s+"\$DELTA_ZIP"'
    ),
}

for label, pattern in patterns.items():
    matches = re.findall(pattern, collapsed)
    if len(matches) != 1:
        raise SystemExit(
            f"{label}: setup source ID match count={len(matches)}"
        )
    value = matches[0].strip()
    if not value:
        raise SystemExit(f"{label}: setup source ID empty")
    print(value)
__MP_PUBLISH_SETUP_IDS_PY_20260806__
}

setup_source_ids_ok() {
  local tmp
  local ids=()

  tmp="$(mktemp)"

  if ! curl -fsSL \
       --retry 3 \
       --connect-timeout 15 \
       "$RAW/setup-m166.sh?t=$(date +%s)" \
       -o "$tmp"; then
    rm -f "$tmp"
    return 1
  fi

  if [ ! -s "$tmp" ] || ! bash -n "$tmp"; then
    rm -f "$tmp"
    return 1
  fi

  mapfile -t ids < <(
    extract_setup_source_ids "$tmp"
  )
  rm -f "$tmp"

  [ "${#ids[@]}" = 2 ] &&
  [ "${ids[0]}" = "$SOURCE_SHOUKO_ID" ] &&
  [ "${ids[1]}" = "$SOURCE_DELTA_ID" ]
}

source_id_from_listing() {
  local listing_file="$1"
  local expected_name="$2"

  python - "$listing_file" "$expected_name" <<'__MP_PUBLISH_LISTING_ID_PY_20260806__'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
expected = sys.argv[2]

data = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data, list):
    raise SystemExit("listing root is not array")

matches = [
    item
    for item in data
    if isinstance(item, dict)
    and item.get("Path") == expected
    and not item.get("IsDir", False)
]

if len(matches) != 1:
    raise SystemExit(
        f"{expected}: remote exact match count={len(matches)}"
    )

file_id = matches[0].get("ID")
if not isinstance(file_id, str) or not file_id.strip():
    raise SystemExit(f"{expected}: Drive file ID missing")

print(file_id.strip())
__MP_PUBLISH_LISTING_ID_PY_20260806__
}

remote_source_id() {
  local expected_name="$1"
  local tmp

  tmp="$(mktemp)"

  if ! rclone lsjson \
       gdrive: \
       --max-depth 1 \
       --files-only \
       --no-modtime \
       --no-mimetype \
       > "$tmp"; then
    rm -f "$tmp"
    return 1
  fi

  local result=0

  if source_id_from_listing "$tmp" "$expected_name"; then
    result=0
  else
    result=$?
  fi

  rm -f "$tmp"
  return "$result"
}

zip_shouko_next() {
  local out="$1"

  [ -d "$SHOUKO" ] || return 2

  python - "$DL" "$SHOUKO" "$out" <<'__MP_PUBLISH_SHOUKO_ZIP_PY_20260806__'
import os
import pathlib
import sys
import zipfile

download = pathlib.Path(sys.argv[1]).resolve()
shouko = pathlib.Path(sys.argv[2]).resolve()
output = pathlib.Path(sys.argv[3]).resolve()
temporary = output.with_name(
    output.name + f".part-{os.getpid()}"
)

blocked = (
    "agent_config.json",
    "device_id.txt",
    "device_group.txt",
    "agent_state.json",
    "provision_report.json",
    "provision_report.txt",
)

extras = (
    "config-change.json",
    "cookie.txt",
    "cookie.txt.bak",
    "Cookies.txt",
    "Cookies.txt.bak",
    "shouko.py",
)


def forbidden(path_name: str) -> bool:
    base = pathlib.PurePosixPath(
        path_name.replace("\\", "/")
    ).name.lower()
    return any(
        base == item or base.startswith(item + ".")
        for item in blocked
    )


try:
    temporary.unlink()
except FileNotFoundError:
    pass

added = 0

try:
    with zipfile.ZipFile(
        temporary,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=True,
    ) as archive:
        for path in sorted(shouko.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(download).as_posix()
            if forbidden(relative):
                continue
            archive.write(path, relative)
            added += 1

        for name in extras:
            path = download / name
            if path.is_symlink() or not path.is_file():
                continue
            archive.write(path, name)
            added += 1

    if added == 0:
        raise RuntimeError("Shouko source archive would be empty")

    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Shouko source archive CRC failed")

    os.chmod(temporary, 0o600)
    os.replace(temporary, output)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
__MP_PUBLISH_SHOUKO_ZIP_PY_20260806__
}

verify_source_zip() {
  local archive="$1"
  local kind="$2"

  python - "$archive" "$kind" <<'__MP_PUBLISH_VERIFY_ZIP_PY_20260806__'
import pathlib
import sys
import zipfile

archive_path = pathlib.Path(sys.argv[1])
kind = sys.argv[2]

blocked = (
    "agent_config.json",
    "device_id.txt",
    "device_group.txt",
    "agent_state.json",
    "provision_report.json",
    "provision_report.txt",
)


def forbidden(path_name: str) -> bool:
    base = pathlib.PurePosixPath(
        path_name.replace("\\", "/")
    ).name.lower()
    return any(
        base == item or base.startswith(item + ".")
        for item in blocked
    )


try:
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise SystemExit("ZIP CRC failed")

        names = archive.namelist()

        if len(names) != len(set(names)):
            raise SystemExit("ZIP has duplicate names")

        normalized = []
        for name in names:
            value = name.replace("\\", "/")
            path = pathlib.PurePosixPath(value)
            if value.startswith("/") or ".." in path.parts:
                raise SystemExit("ZIP has unsafe path")
            normalized.append(value)

        if kind == "shouko":
            files = [
                name
                for name in normalized
                if name.startswith("Shouko/")
                and not name.endswith("/")
            ]
            if not files:
                raise SystemExit("Shouko ZIP has no Shouko files")
            bad = [name for name in normalized if forbidden(name)]
            if bad:
                raise SystemExit(
                    "Shouko ZIP contains forbidden device data"
                )
        elif kind == "delta":
            files = [
                name
                for name in normalized
                if name.startswith("Delta/")
                and not name.endswith("/")
            ]
            if not files:
                raise SystemExit("Delta ZIP has no Delta files")
        else:
            raise SystemExit("unknown ZIP kind")
except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
    raise SystemExit(str(exc))
__MP_PUBLISH_VERIFY_ZIP_PY_20260806__
}

verify_remote_source() {
  local remote="$1"
  local expected_id="$2"
  local expected_name="$3"
  local expected_sha="$4"
  local kind="$5"
  local destination="$6"
  local actual_id actual_sha

  actual_id="$(remote_source_id "$expected_name")" ||
    return 1

  [ "$actual_id" = "$expected_id" ] ||
    return 1

  rm -f "$destination"

  rclone copyto "$remote" "$destination" ||
    return 1

  [ -s "$destination" ] ||
    return 1

  actual_sha="$(
    sha256sum "$destination" |
      awk 'NR == 1 {print $1}'
  )"

  [ "$actual_sha" = "$expected_sha" ] ||
    return 1

  verify_source_zip "$destination" "$kind"
}

rollback_source_files() {
  local old_shouko="$1"
  local old_delta="$2"
  local old_shouko_sha="$3"
  local old_delta_sha="$4"
  local work="$5"
  local current_id

  rclone copyto "$old_delta" "$SOURCE_DELTA_REMOTE" ||
    return 1

  rclone copyto "$old_shouko" "$SOURCE_SHOUKO_REMOTE" ||
    return 1

  current_id="$(remote_source_id Delta.zip)" ||
    return 1
  [ "$current_id" = "$SOURCE_DELTA_ID" ] ||
    return 1

  current_id="$(remote_source_id Shouko.zip)" ||
    return 1
  [ "$current_id" = "$SOURCE_SHOUKO_ID" ] ||
    return 1

  rm -f "$work/rollback-Shouko.zip" "$work/rollback-Delta.zip"

  rclone copyto \
    "$SOURCE_SHOUKO_REMOTE" \
    "$work/rollback-Shouko.zip" ||
    return 1

  rclone copyto \
    "$SOURCE_DELTA_REMOTE" \
    "$work/rollback-Delta.zip" ||
    return 1

  [ "$(
      sha256sum "$work/rollback-Shouko.zip" |
        awk 'NR == 1 {print $1}'
    )" = "$old_shouko_sha" ] ||
    return 1

  [ "$(
      sha256sum "$work/rollback-Delta.zip" |
        awk 'NR == 1 {print $1}'
    )" = "$old_delta_sha" ] ||
    return 1
}

publish_next_sources() {
  local force="${1:-0}"
  local phase current_status device stamp work history_remote
  local old_shouko old_delta new_shouko new_delta
  local old_shouko_sha old_delta_sha new_shouko_sha new_delta_sha
  local current_id failure=""

  [ -s "$STATE" ] ||
    die "Chưa khởi tạo mprovision"

  phase="$(state_get phase)"

  case "$phase" in
    finalize|complete)
      ;;
    *)
      die "Chỉ publish nguồn ở phase finalize hoặc complete; phase=$phase"
      ;;
  esac

  current_status="$(state_get publish_next_status)"

  if [ "$force" != 1 ] && [ "$current_status" = OK ]; then
    ok "Nguồn máy kế tiếp đã publish; không lặp lại"
    return 0
  fi

  rclone_ok ||
    die "gdrive: chưa sẵn sàng để publish nguồn máy kế tiếp"

  setup_source_ids_ok ||
    die "ID nguồn trong setup-m166.sh không khớp cấu hình publish"

  current_id="$(remote_source_id Shouko.zip)" ||
    die "Không xác định duy nhất Shouko.zip trên gdrive:"

  [ "$current_id" = "$SOURCE_SHOUKO_ID" ] ||
    die "Drive ID của Shouko.zip không khớp setup-m166.sh"

  current_id="$(remote_source_id Delta.zip)" ||
    die "Không xác định duy nhất Delta.zip trên gdrive:"

  [ "$current_id" = "$SOURCE_DELTA_ID" ] ||
    die "Drive ID của Delta.zip không khớp setup-m166.sh"

  device="$(state_get device_id)"
  stamp="$(date +%Y%m%d-%H%M%S)"
  work="$(mktemp -d)"

  old_shouko="$work/old/Shouko.zip"
  old_delta="$work/old/Delta.zip"
  new_shouko="$work/new/Shouko.zip"
  new_delta="$work/new/Delta.zip"
  history_remote="$SOURCE_HISTORY_ROOT/$device/$stamp-before"

  mkdir -p "$work/old" "$work/new"

  if ! rclone copyto "$SOURCE_SHOUKO_REMOTE" "$old_shouko"; then
    rm -rf "$work"
    die "Không tải được nguồn Shouko hiện tại để backup"
  fi

  if ! rclone copyto "$SOURCE_DELTA_REMOTE" "$old_delta"; then
    rm -rf "$work"
    die "Không tải được nguồn Delta hiện tại để backup"
  fi

  unzip -tqq "$old_shouko" >/dev/null 2>&1 || {
    rm -rf "$work"
    die "Nguồn Shouko hiện tại không phải ZIP hợp lệ"
  }

  unzip -tqq "$old_delta" >/dev/null 2>&1 || {
    rm -rf "$work"
    die "Nguồn Delta hiện tại không phải ZIP hợp lệ"
  }

  old_shouko_sha="$(
    sha256sum "$old_shouko" |
      awk 'NR == 1 {print $1}'
  )"
  old_delta_sha="$(
    sha256sum "$old_delta" |
      awk 'NR == 1 {print $1}'
  )"

  (
    cd "$work/old"
    sha256sum Shouko.zip > Shouko.zip.sha256
    sha256sum Delta.zip > Delta.zip.sha256
  )

  if ! rclone copy "$work/old" "$history_remote"; then
    rm -rf "$work"
    die "Không lưu được lịch sử nguồn trước publish"
  fi

  zip_shouko_next "$new_shouko" || {
    rm -rf "$work"
    die "Không tạo được Shouko.zip sạch cho máy kế tiếp"
  }

  zip_delta "$new_delta" || {
    rm -rf "$work"
    die "Không tạo được Delta.zip cho máy kế tiếp"
  }

  verify_source_zip "$new_shouko" shouko || {
    rm -rf "$work"
    die "Shouko.zip máy kế tiếp không hợp lệ"
  }

  verify_source_zip "$new_delta" delta || {
    rm -rf "$work"
    die "Delta.zip máy kế tiếp không hợp lệ"
  }

  new_shouko_sha="$(
    sha256sum "$new_shouko" |
      awk 'NR == 1 {print $1}'
  )"
  new_delta_sha="$(
    sha256sum "$new_delta" |
      awk 'NR == 1 {print $1}'
  )"

  state_set \
    "publish_next_status=RUNNING" \
    "publish_next_started_at=$(utc_now)" \
    "publish_next_history_remote=$history_remote"

  if ! rclone copyto "$new_delta" "$SOURCE_DELTA_REMOTE"; then
    failure="upload Delta.zip"
  elif ! verify_remote_source \
       "$SOURCE_DELTA_REMOTE" \
       "$SOURCE_DELTA_ID" \
       Delta.zip \
       "$new_delta_sha" \
       delta \
       "$work/verify-Delta.zip"; then
    failure="verify Delta.zip"
  elif ! rclone copyto "$new_shouko" "$SOURCE_SHOUKO_REMOTE"; then
    failure="upload Shouko.zip"
  elif ! verify_remote_source \
       "$SOURCE_SHOUKO_REMOTE" \
       "$SOURCE_SHOUKO_ID" \
       Shouko.zip \
       "$new_shouko_sha" \
       shouko \
       "$work/verify-Shouko.zip"; then
    failure="verify Shouko.zip"
  fi

  if [ -n "$failure" ]; then
    warn "Publish thất bại tại: $failure; đang rollback hai file nguồn"

    if rollback_source_files \
         "$old_shouko" \
         "$old_delta" \
         "$old_shouko_sha" \
         "$old_delta_sha" \
         "$work"; then
      state_set \
        "publish_next_status=ROLLED_BACK" \
        "publish_next_failed_step=$failure"
      rm -rf "$work"
      die "Publish nguồn thất bại; rollback đã xác minh thành công"
    fi

    state_set \
      "publish_next_status=ROLLBACK_FAILED" \
      "publish_next_failed_step=$failure"
    rm -rf "$work"
    die "Publish nguồn thất bại và rollback chưa xác minh được"
  fi

  state_set \
    "publish_next_status=OK" \
    "publish_next_completed_at=$(utc_now)" \
    "publish_next_failed_step=" \
    "publish_next_history_remote=$history_remote" \
    "publish_next_shouko_sha256=$new_shouko_sha" \
    "publish_next_delta_sha256=$new_delta_sha"

  rm -rf "$work"

  echo "PUBLISH_NEXT=THÀNH_CÔNG"
  echo "SOURCE_HISTORY_REMOTE=$history_remote"
  echo "SHOUKO_SOURCE_SHA256=$new_shouko_sha"
  echo "DELTA_SOURCE_SHA256=$new_delta_sha"
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

wizard_shortcut_content() {
  cat <<'__MP_WIZARD_SHORTCUT__'
#!/data/data/com.termux/files/usr/bin/bash
# Supervisor delegates to /data/data/com.termux/files/usr/bin/mprovision wizard.
# Log: $HOME/.local/state/aotscript/wizard.log
exec /data/data/com.termux/files/home/bin/aotscript-wizard start
__MP_WIZARD_SHORTCUT__
}

wizard_legacy_shortcut_content() {
  cat <<'__MP_WIZARD_LEGACY_SHORTCUT__'
#!/data/data/com.termux/files/usr/bin/bash
exec /data/data/com.termux/files/usr/bin/mprovision wizard
__MP_WIZARD_LEGACY_SHORTCUT__
}

wizard_notify() {
  local code="$1" text

  case "$code" in
    complete)
      text="Máy đã hoàn tất. Không còn bước cần chạy."
      ;;
    root)
      text="Bật root rồi chạm lại AOTSCRIPT_SETUP."
      ;;
    google)
      text="Đăng nhập Google xong chạm lại AOTSCRIPT_SETUP."
      ;;
    swift_before)
      text="Backup Termux và dữ liệu cũ, rồi chạm lại AOTSCRIPT_SETUP."
      ;;
    swift_restore)
      text="Restore nhóm RESTORE_DATA, rồi chạm lại AOTSCRIPT_SETUP."
      ;;
    swift_incomplete)
      text="Restore chưa đủ ứng dụng bắt buộc. Swift Backup đã mở lại."
      ;;
    manual_post)
      text="Còn các bước thủ công sau restore."
      ;;
    *)
      text="Aotscript Setup đã chạy."
      ;;
  esac

  if ! root_ok; then
    echo "WIZARD_NOTIFICATION=SKIPPED_NO_ROOT"
    return 1
  fi

  if su -c "/system/bin/cmd notification post \
       -t 'Aotscript Setup' \
       aotscript_setup \
       '$text'" \
       >/dev/null 2>&1; then
    echo "WIZARD_NOTIFICATION=$code"
    return 0
  fi

  echo "WIZARD_NOTIFICATION=FAILED"
  return 1
}

wizard_package_exists() {
  local package="$1"

  if root_ok; then
    su -c "pm path '$package'" >/dev/null 2>&1
  else
    /system/bin/pm path "$package" >/dev/null 2>&1
  fi
}

install_wizard_supervisor() {
  local tmp stamp command_link

  mkdir -p "$HOME/bin"
  tmp="$WIZARD_SUPERVISOR.tmp.$$"
  stamp="$(date +%Y%m%d-%H%M%S)"
  command_link="${PREFIX:-/data/data/com.termux/files/usr}/bin/aotscript-wizard"

  rm -f "$tmp"

  curl -fsSL \
    --retry 3 \
    --connect-timeout 15 \
    "$WIZARD_SUPERVISOR_URL?t=$(date +%s)" \
    -o "$tmp" || {
      rm -f "$tmp"
      die "Không tải được wizard-supervisor.sh"
    }

  [ -s "$tmp" ] || {
    rm -f "$tmp"
    die "wizard-supervisor.sh tải về rỗng"
  }

  bash -n "$tmp" || {
    rm -f "$tmp"
    die "wizard-supervisor.sh tải về sai cú pháp"
  }

  chmod 700 "$tmp"

  if [ -f "$WIZARD_SUPERVISOR" ] &&
     cmp -s "$tmp" "$WIZARD_SUPERVISOR"; then
    rm -f "$tmp"
  else
    [ ! -f "$WIZARD_SUPERVISOR" ] ||
      cp -p \
        "$WIZARD_SUPERVISOR" \
        "$WIZARD_SUPERVISOR.bak-$stamp"

    mv -f "$tmp" "$WIZARD_SUPERVISOR" || {
      rm -f "$tmp"
      die "Không cài được Aotscript Wizard Supervisor"
    }

    chmod 700 "$WIZARD_SUPERVISOR"
  fi

  if [ -L "$command_link" ]; then
    if [ "$(readlink "$command_link")" != "$WIZARD_SUPERVISOR" ]; then
      warn "Giữ nguyên symlink aotscript-wizard khác nội dung: $command_link"
    fi
  elif [ -e "$command_link" ]; then
    warn "Giữ nguyên file aotscript-wizard hiện có: $command_link"
  else
    ln -s "$WIZARD_SUPERVISOR" "$command_link"
  fi
}

install_wizard_shortcut() {
  local tmp legacy_tmp stamp legacy_status="NOT_PRESENT"

  install_wizard_supervisor

  mkdir -p "$HOME/.shortcuts" "$WIZARD_SHORTCUT_DIR"
  chmod 700 "$HOME/.shortcuts" "$WIZARD_SHORTCUT_DIR"

  tmp="$WIZARD_SHORTCUT.tmp.$$"
  legacy_tmp="$WIZARD_SHORTCUT_DIR/.legacy-AOTSCRIPT_SETUP.$$"
  stamp="$(date +%Y%m%d-%H%M%S)"

  wizard_shortcut_content > "$tmp"
  wizard_legacy_shortcut_content > "$legacy_tmp"
  bash -n "$tmp" || {
    rm -f "$tmp"
    die "Shortcut AOTSCRIPT SETUP tạm sai cú pháp"
  }
  chmod 700 "$tmp"

  if [ -f "$WIZARD_LEGACY_SHORTCUT" ]; then
    if cmp -s "$legacy_tmp" "$WIZARD_LEGACY_SHORTCUT"; then
      rm -f "$WIZARD_LEGACY_SHORTCUT"
      legacy_status="REMOVED"
    else
      legacy_status="PRESERVED_DIFFERENT_CONTENT"
      warn "Shortcut foreground cũ khác nội dung; không tự xóa"
    fi
  fi
  rm -f "$legacy_tmp"

  if [ -f "$WIZARD_SHORTCUT" ] &&
     cmp -s "$tmp" "$WIZARD_SHORTCUT"; then
    rm -f "$tmp"
  else
    [ ! -f "$WIZARD_SHORTCUT" ] ||
      cp -p \
        "$WIZARD_SHORTCUT" \
        "$WIZARD_SHORTCUT.bak-$stamp"
    mv -f "$tmp" "$WIZARD_SHORTCUT" ||
      die "Không cài được shortcut AOTSCRIPT SETUP chạy nền"
    chmod 700 "$WIZARD_SHORTCUT"
  fi

  echo "WIZARD_LEGACY_SHORTCUT=$legacy_status"

  if wizard_package_exists "$TERMUX_WIDGET_PACKAGE"; then
    if root_ok; then
      su -c "am broadcast \
        -n com.termux.widget/.TermuxWidgetProvider \
        -a com.termux.widget.ACTION_REFRESH_WIDGET \
        --ei appWidgetId 0" \
        >/dev/null 2>&1 || true
    fi
    echo "WIZARD_SHORTCUT=READY_BACKGROUND"
  else
    echo "WIZARD_SHORTCUT=READY_BACKGROUND_WIDGET_MISSING"
    warn "Cần cài Termux:Widget cùng nguồn ký với Termux"
    warn "Sau đó thêm widget và chọn tasks/AOTSCRIPT_SETUP"
  fi
}

google_account_present() {
  local dump

  root_ok || return 1
  dump="$(su -c 'dumpsys account' 2>/dev/null || true)"

  python - 3<<<"$dump" <<'__MP_GOOGLE_ACCOUNT_PRESENT_PY__'
import os
import re

text = os.fdopen(3, "r", encoding="utf-8", errors="replace").read()
accounts = list(
    re.finditer(
        r"Account\s*\{name=([^,}]+),\s*type=com[.]google\}",
        text,
    )
)
raise SystemExit(0 if accounts else 1)
__MP_GOOGLE_ACCOUNT_PRESENT_PY__
}

wizard_open_google_login() {
  root_ok || return 1

  su -c "am start \
    -a android.settings.ADD_ACCOUNT_SETTINGS \
    --esa account_types com.google" \
    >/dev/null 2>&1
}

wizard_open_swift() {
  wizard_package_exists "$SWIFT_PACKAGE" || {
    echo "SWIFT_BACKUP_PACKAGE=MISSING"
    return 1
  }

  ui_launch_package "$SWIFT_PACKAGE"
}

wizard_restore_missing_packages() {
  local item label package
  local required=(
    "1.1.1.1|com.cloudflare.onedotonedotonedotone"
    "Control Screen Orientation|ahapps.controlthescreenorientation"
    "Drive|com.google.android.apps.docs"
    "Taskbar|com.farmerbb.taskbar"
    "ZArchiver|ru.zdevs.zarchiver"
  )

  for item in "${required[@]}"; do
    label="${item%%|*}"
    package="${item#*|}"

    if ! ui_package_exists "$package"; then
      printf '%s\n' "$label"
    fi
  done
}

wizard_open_restore_step() {
  wizard_open_swift ||
    die "Không mở được Swift Backup"

  state_set "wizard_step=await_swift_restore"
  wizard_notify swift_restore || true

  echo "WIZARD_STEP=SWIFT_RESTORE"
  echo "Khôi phục nhóm RESTORE_DATA và các app còn lại."
  echo "Xong chạm lại AOTSCRIPT_SETUP; không cần gõ lệnh."
}

wizard() {
  local phase step missing

  install_wizard_shortcut

  [ -s "$STATE" ] ||
    die "Chưa khởi tạo mprovision"

  phase="$(state_get phase)"

  case "$phase" in
    preflight|await_root)
      if ! root_ok; then
        wizard_notify root || true
        echo "WIZARD_STEP=ROOT"
        echo "Bật root rồi chạm lại AOTSCRIPT_SETUP."
        return
      fi

      resume
      phase="$(state_get phase)"

      if [ "$phase" = manual_pre ]; then
        wizard
      else
        status
      fi
      ;;
    manual_pre)
      if ! root_ok; then
        wizard_notify root || true
        echo "WIZARD_STEP=ROOT"
        echo "Bật root rồi chạm lại AOTSCRIPT_SETUP."
        return
      fi

      if ! google_account_present; then
        state_set "wizard_step=await_google_login"

        wizard_open_google_login ||
          die "Không mở được màn hình thêm tài khoản Google"

        wizard_notify google || true
        echo "WIZARD_STEP=GOOGLE_LOGIN"
        echo "Đăng nhập Google xong chạm lại AOTSCRIPT_SETUP."
        return
      fi

      step="$(state_get wizard_step)"

      if [ "$step" != await_swift_backup_before ]; then
        wizard_open_swift ||
          die "Không mở được Swift Backup"

        state_set "wizard_step=await_swift_backup_before"
        wizard_notify swift_before || true

        echo "WIZARD_STEP=SWIFT_BACKUP_BEFORE"
        echo "Backup Termux kèm data và dữ liệu cũ cần giữ."
        echo "Xong chạm lại AOTSCRIPT_SETUP."
        return
      fi

      state_set "wizard_step="
      done_checkpoint pre
      phase="$(state_get phase)"

      if [ "$phase" = manual_post ]; then
        wizard_open_restore_step
      else
        status
      fi
      ;;
    await_root_setup|await_rclone_before|automatic)
      resume
      phase="$(state_get phase)"

      if [ "$phase" = manual_post ]; then
        wizard_open_restore_step
      else
        status
      fi
      ;;
    manual_post)
      step="$(state_get wizard_step)"

      case "$step" in
        await_swift_restore)
          missing="$(wizard_restore_missing_packages)"

          if [ -n "$missing" ]; then
            wizard_notify swift_incomplete || true
            echo "WIZARD_STEP=SWIFT_RESTORE_INCOMPLETE"
            printf '%s\n' "$missing" |
              sed 's/^/MISSING_APP=/'
            wizard_open_swift ||
              die "Không mở lại được Swift Backup"
            return
          fi

          state_set "wizard_step=manual_post_remaining"
          wizard_notify manual_post || true
          echo "WIZARD_STEP=MANUAL_POST_REMAINING"
          echo "REQUIRED_RESTORE_PACKAGES=OK"
          echo "App data vẫn cần xác nhận trực tiếp trong Swift Backup."
          show_manual_post
          ;;
        manual_post_remaining)
          wizard_notify manual_post || true
          show_manual_post
          ;;
        *)
          wizard_open_restore_step
          ;;
      esac
      ;;
    await_rclone_after|finalize)
      resume
      ;;
    complete)
      wizard_notify complete || true
      echo "WIZARD_STEP=COMPLETE"
      status
      ;;
    *)
      die "Phase wizard không hợp lệ: ${phase:-EMPTY}"
      ;;
  esac
}

show_manual_pre() {
  cat <<'__MP_MANUAL_PRE__'

========== THỦ CÔNG 1 ==========
[AUTO] Google Login Assistant ưu tiên root bootstrap private; rclone chỉ là fallback sau khi Termux đã có cấu hình.
[ ] Kiểm tra Play Protect theo quy trình vận hành.
[ ] Swift Backup: backup Termux kèm data.
[ ] Swift Backup: backup các app và data cũ cần giữ.
[ ] Không chạy lại lệnh ZIP/rclone cũ: done pre sẽ tự backup Shouko và Delta.
[ ] Không gửi mật khẩu, key, cookie hoặc file cấu hình riêng tư vào chat.

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
[ ] Swift Backup có data: Drive, Control, 1.1.1.1, ZArchiver và Taskbar.
[ ] Swift Backup các ứng dụng còn lại không data theo quy trình vận hành.
[AUTO] done post tự mở Termux:Boot.
[ ] Hoàn tất key Shouko trực tiếp trên máy; không gửi key vào chat.
[AUTO] done post tự bật Control; chỉ kiểm tra màn hình ngang nếu cần.
[AUTO] done post tự bật Taskbar, đóng rồi mở lại tất cả package Roblox com.tinh.vv.* ở freeform.
[ ] Setup cookie, check cookie và login cookie.
[THỦ CÔNG] Bật 1.1.1.1/WARP và xác nhận Connected khi cần.
[ ] Chỉnh auto-exec.
[ ] Chạy toolcheck; xác nhận đủ user và không trùng account.
[ ] Chạy thử winterhub đúng một lần.
[ ] Khởi động lại khi cần để xác nhận Termux:Boot; không lặp vô hạn.
[ ] Mã hoặc link riêng theo máy vẫn làm thủ công cho đến khi xác định đúng file và định dạng.

Xem audit tự động trước khi hoàn tất, không bắt buộc:
  mprovision audit

Làm xong chạy:
  mprovision done post

Lệnh done post sẽ tự chạy audit chỉ đọc, sau đó mới kiểm tra cuối và backup after.
================================
__MP_MANUAL_POST__
}

audit_setting() {
  local key="$1" value label

  label="${key^^}"
  value="$(su -c "settings get global $key" 2>/dev/null || true)"

  if [ "$value" = 1 ]; then
    echo "SETTING_${label}=OK"
    return 0
  fi

  echo "SETTING_${label}=NEEDS_ATTENTION"
  return 1
}

audit_display_values() {
  python - "$1" "$2" <<'__MP_PHASE3A_DISPLAY_AUDIT_PY_20260806__'
import re
import sys

size_text = sys.argv[1]
density_text = sys.argv[2]


def preferred_value(text, value_pattern):
    matches = re.findall(value_pattern, text)
    for preferred in ("Override", "Physical"):
        for match in reversed(matches):
            if match[0] == preferred:
                return match[1:]
    return None


size = preferred_value(
    size_text,
    r"(Physical|Override) size:\s*(\d+)x(\d+)",
)
density = preferred_value(
    density_text,
    r"(Physical|Override) density:\s*(\d+)",
)

if size is None or density is None:
    print("DISPLAY_SMALLEST_WIDTH_DP=UNKNOWN")
    print("DISPLAY_TARGET_700DP=NEEDS_ATTENTION")
    raise SystemExit(1)

width = int(size[0])
height = int(size[1])
density_value = int(density[0])

if width <= 0 or height <= 0 or density_value <= 0:
    print("DISPLAY_SMALLEST_WIDTH_DP=UNKNOWN")
    print("DISPLAY_TARGET_700DP=NEEDS_ATTENTION")
    raise SystemExit(1)

smallest_dp = round(min(width, height) * 160 / density_value)
within_target = 680 <= smallest_dp <= 720

print(f"DISPLAY_SMALLEST_WIDTH_DP={smallest_dp}")
print(
    "DISPLAY_TARGET_700DP="
    + ("OK" if within_target else "NEEDS_ATTENTION")
)

raise SystemExit(0 if within_target else 1)
__MP_PHASE3A_DISPLAY_AUDIT_PY_20260806__
}

audit_display() {
  local size_output density_output

  size_output="$(su -c 'wm size' 2>/dev/null || true)"
  density_output="$(su -c 'wm density' 2>/dev/null || true)"
  audit_display_values "$size_output" "$density_output"
}

audit() {
  local failures=0 setting current_id current_group
  local default_ime agent_processes

  [ -s "$STATE" ] ||
    die "Chưa khởi tạo mprovision"

  echo "MPROVISION_AUDIT"
  echo "AUDIT_MODE=READ_ONLY"
  echo "VERSION=$VERSION"
  echo "DEVICE_ID=$(state_get device_id)"
  echo "DEVICE_GROUP=$(state_get device_group)"
  echo "PHASE=$(state_get phase)"
  echo "RUN_ID=$(state_get run_id)"

  if root_ok; then
    echo "ROOT=OK"

    for setting in \
      development_settings_enabled \
      force_allow_on_external \
      force_resizable_activities \
      enable_freeform_support \
      force_desktop_mode_on_external_displays; do
      if audit_setting "$setting"; then
        :
      else
        failures=$((failures + 1))
      fi
    done

    if audit_display; then
      :
    else
      failures=$((failures + 1))
    fi

    if su -c 'pm path com.termux.boot' >/dev/null 2>&1; then
      echo "TERMUX_BOOT_APP=OK"
    else
      echo "TERMUX_BOOT_APP=NEEDS_ATTENTION"
      failures=$((failures + 1))
    fi

    if su -c 'pm path com.google.android.inputmethod.latin' \
         >/dev/null 2>&1; then
      echo "GBOARD_INSTALLED=OK"
    else
      echo "GBOARD_INSTALLED=NEEDS_ATTENTION"
      failures=$((failures + 1))
    fi

    default_ime="$(
      su -c 'settings get secure default_input_method' \
        2>/dev/null || true
    )"

    case "$default_ime" in
      com.google.android.inputmethod.latin/*)
        echo "GBOARD_DEFAULT=OK"
        ;;
      *)
        echo "GBOARD_DEFAULT=NEEDS_ATTENTION"
        failures=$((failures + 1))
        ;;
    esac
  else
    echo "ROOT=NEEDS_ATTENTION"
    echo "ANDROID_SETTINGS_AUDIT=SKIPPED_NO_ROOT"
    failures=$((failures + 1))
  fi

  current_id="$(
    tr -d '\r\n ' < "$SHOUKO/device_id.txt" \
      2>/dev/null |
      tr '[:upper:]' '[:lower:]' || true
  )"
  current_group="$(
    tr -d '\r\n ' < "$SHOUKO/device_group.txt" \
      2>/dev/null |
      tr '[:lower:]' '[:upper:]' || true
  )"

  if [ "$current_id" = "$(state_get device_id)" ]; then
    echo "DEVICE_ID_FILE=OK"
  else
    echo "DEVICE_ID_FILE=NEEDS_ATTENTION"
    failures=$((failures + 1))
  fi

  if [ "$current_group" = "$(state_get device_group)" ]; then
    echo "DEVICE_GROUP_FILE=OK"
  else
    echo "DEVICE_GROUP_FILE=NEEDS_ATTENTION"
    failures=$((failures + 1))
  fi

  if [ -s "$WINTERHUB" ] && bash -n "$WINTERHUB"; then
    echo "WINTERHUB_SCRIPT=OK"
  else
    echo "WINTERHUB_SCRIPT=NEEDS_ATTENTION"
    failures=$((failures + 1))
  fi

  if [ -s "$AGENT_BOOT" ] && bash -n "$AGENT_BOOT"; then
    echo "AGENT_BOOT_SCRIPT=OK"
  else
    echo "AGENT_BOOT_SCRIPT=NEEDS_ATTENTION"
    failures=$((failures + 1))
  fi

  if [ -s "$AGENT" ]; then
    if python - "$AGENT" <<'__MP_PHASE3A_AGENT_AUDIT_PY_20260806__'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
compile(path.read_text(encoding="utf-8"), str(path), "exec")
__MP_PHASE3A_AGENT_AUDIT_PY_20260806__
    then
      echo "AGENT_SYNTAX=OK"
    else
      echo "AGENT_SYNTAX=NEEDS_ATTENTION"
      failures=$((failures + 1))
    fi
  else
    echo "AGENT_SYNTAX=NEEDS_ATTENTION"
    failures=$((failures + 1))
  fi

  if validate_config >/dev/null 2>&1; then
    echo "AGENT_CONFIG=OK"
  else
    echo "AGENT_CONFIG=NEEDS_ATTENTION"
    failures=$((failures + 1))
  fi

  if command -v toolcheck >/dev/null 2>&1; then
    echo "TOOLCHECK_COMMAND=OK"
  else
    echo "TOOLCHECK_COMMAND=NEEDS_ATTENTION"
    failures=$((failures + 1))
  fi

  agent_processes="$(agent_count)"
  if [ "$agent_processes" = 1 ]; then
    echo "AGENT_PROCESS_COUNT=1"
  else
    echo "AGENT_PROCESS_COUNT=$agent_processes"
    failures=$((failures + 1))
  fi

  if rclone_ok; then
    echo "GDRIVE_ACCESS=OK"
  else
    echo "GDRIVE_ACCESS=NEEDS_ATTENTION"
    failures=$((failures + 1))
  fi

  echo "MANUAL_GOOGLE_LOGIN=VISUAL_CONFIRMATION_REQUIRED"
  echo "MANUAL_PLAY_PROTECT=VISUAL_CONFIRMATION_REQUIRED"
  echo "MANUAL_SWIFT_RESTORE=VISUAL_CONFIRMATION_REQUIRED"
  echo "MANUAL_SHOUKO_KEY=VISUAL_CONFIRMATION_REQUIRED"
  echo "MANUAL_COOKIE_LOGIN=VISUAL_CONFIRMATION_REQUIRED"
  echo "MANUAL_CONTROL_AND_TAB=VISUAL_CONFIRMATION_REQUIRED"
  echo "MANUAL_1_1_1_1=VISUAL_CONFIRMATION_REQUIRED"
  echo "MANUAL_AUTO_EXEC=VISUAL_CONFIRMATION_REQUIRED"
  echo "MANUAL_TOOLCHECK_USERS=VISUAL_CONFIRMATION_REQUIRED"

  if [ "$failures" = 0 ]; then
    echo "AUDIT_AUTOMATIC=OK"
  else
    echo "AUDIT_AUTOMATIC=NEEDS_ATTENTION"
    echo "AUDIT_AUTOMATIC_FAILURES=$failures"
  fi

  return 0
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
      echo "PUBLISH_NEXT=$(state_get publish_next_status)"
      echo "NEXT=KHÔNG_CẦN_LỆNH_THÊM"
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
      audit
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

Xong chạy: mprovision reconcile
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
  install_wizard_shortcut
  if ! root_ok; then
    state_set phase=await_root
    echo "ROOT chưa hoạt động. Bật root, kiểm tra su -c id, rồi chạy mprovision reconcile."
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
    echo "ROOT không hoạt động. Bật lại root rồi chạy mprovision reconcile."
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

ui_package_exists() {
  su -c "pm path '$1'" >/dev/null 2>&1
}

ui_service_running() {
  su -c 'dumpsys activity services' 2>/dev/null |
    grep -Fq "$1"
}

ui_launch_package() {
  su -c "monkey -p '$1' \
    -c android.intent.category.LAUNCHER 1" \
    >/dev/null 2>&1
}

ui_try_start_service() {
  local component="$1"

  su -c "am start-foreground-service -n '$component'" \
    >/dev/null 2>&1 ||
  su -c "am startservice -n '$component'" \
    >/dev/null 2>&1
}

ui_launcher_component() {
  local package="$1"

  su -c "cmd package resolve-activity --brief \
    -a android.intent.action.MAIN \
    -c android.intent.category.LAUNCHER \
    '$package'" 2>/dev/null |
    awk 'NF { line = $0 } END { print line }'
}

ui_installed_roblox_packages() {
  su -c 'pm list packages' 2>/dev/null |
    sed -n 's/^package://p' |
    grep -E '^com\.tinh\.vv\.[A-Za-z0-9._-]+$' |
    sort -u ||
    true
}

ui_dump_xml() {
  local label="$1"
  local safe path

  safe="$(
    printf '%s' "$label" |
      tr -c 'A-Za-z0-9._-' '_'
  )"
  path="$DL/.aotscript-ui-${safe}-$$.xml"

  rm -f "$path"

  su -c "/system/bin/uiautomator dump \
    --compressed '$path'" \
    >/dev/null 2>&1 ||
    return 1

  [ -s "$path" ] || return 1
  printf '%s\n' "$path"
}

ui_node_center() {
  local xml="$1"
  local mode="$2"
  local package="${3:-}"

  python - "$xml" "$mode" "$package" <<'__MP_UI_NODE_CENTER_PY__'
import re
import sys
import xml.etree.ElementTree as ET

xml_name, mode, package = sys.argv[1:4]
root = ET.parse(xml_name).getroot()
bounds_re = re.compile(
    r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$"
)


def parse_bounds(node):
    match = bounds_re.match(
        node.attrib.get("bounds", "")
    )
    if not match:
        return None

    left, top, right, bottom = map(
        int,
        match.groups(),
    )

    if right <= left or bottom <= top:
        return None

    return left, top, right, bottom


def center(box):
    left, top, right, bottom = box
    return (
        (left + right) // 2,
        (top + bottom) // 2,
    )


candidates = []

for node in root.iter("node"):
    box = parse_bounds(node)
    if box is None:
        continue

    node_package = node.attrib.get("package", "")
    node_class = node.attrib.get("class", "")
    resource = node.attrib.get("resource-id", "")
    text = node.attrib.get("text", "")
    desc = node.attrib.get("content-desc", "")
    clickable = (
        node.attrib.get("clickable", "") == "true"
    )
    checked = node.attrib.get("checked", "")
    words = " ".join(
        (text, desc, resource)
    ).strip().casefold()

    if mode == "switch":
        if package and node_package != package:
            continue

        is_switch = (
            "switch" in node_class.casefold()
            or "switch" in resource.casefold()
            or "toggle" in resource.casefold()
            or checked in {"true", "false"}
        )

        if not is_switch or checked == "true":
            continue

        score = 0

        if node_package == package:
            score += 120
        if "switch" in node_class.casefold():
            score += 80
        if (
            "switch" in resource.casefold()
            or "toggle" in resource.casefold()
        ):
            score += 60
        if checked == "false":
            score += 40
        if clickable:
            score += 20

        if package == "com.farmerbb.taskbar":
            score += max(0, 80 - box[1] // 8)

        candidates.append((score, box))

    elif mode == "warp_switch":
        expected_resource = (
            "com.cloudflare.onedotonedotonedotone:"
            "id/launchSwitch"
        )

        if (
            node_package != package
            or resource != expected_resource
            or node_class != "android.widget.Switch"
            or not clickable
            or checked == "true"
        ):
            continue

        candidates.append((1000, box))

    elif mode == "warp_update_close":
        if node_package != "com.android.vending":
            continue

        close_desc = desc.casefold()

        if (
            "đóng hộp thoại cập nhật" not in close_desc
            and "close update dialog" not in close_desc
        ):
            continue

        candidates.append((1000, box))

    elif mode == "permission":
        allowed = (
            "allow",
            "ok",
            "turn on",
            "cho phép",
            "đồng ý",
            "bật",
        )

        if not any(word in words for word in allowed):
            continue

        score = 0

        if clickable:
            score += 100
        if node_class.endswith("Button"):
            score += 60

        candidates.append((score, box))

    elif mode == "close":
        close_words = (
            "close",
            "dismiss",
            "cancel",
            "đóng",
        )

        if not any(word in words for word in close_words):
            continue

        score = 0

        if clickable:
            score += 100
        if "imagebutton" in node_class.casefold():
            score += 60

        score += min(box[0] // 10, 80)
        score += max(0, 80 - box[1] // 10)

        candidates.append((score, box))

if not candidates:
    raise SystemExit(1)

candidates.sort(
    key=lambda item: item[0],
    reverse=True,
)

x, y = center(candidates[0][1])
print(f"{x} {y}")
__MP_UI_NODE_CENTER_PY__
}

ui_click_node() {
  local mode="$1"
  local package="$2"
  local label="$3"
  local xml point x y

  xml="$(ui_dump_xml "$label")" || {
    echo "${label}_UI_DUMP=FAILED"
    return 1
  }

  point="$(
    ui_node_center "$xml" "$mode" "$package"
  )" || {
    rm -f "$xml"
    echo "${label}_NODE=NOT_FOUND"
    return 1
  }

  rm -f "$xml"
  read -r x y <<<"$point"

  [[ "$x" =~ ^[0-9]+$ ]] &&
  [[ "$y" =~ ^[0-9]+$ ]] || {
    echo "${label}_BOUNDS=INVALID"
    return 1
  }

  su -c "input tap '$x' '$y'" \
    >/dev/null 2>&1 || {
    echo "${label}_CLICK=FAILED"
    return 1
  }

  echo "${label}_CLICK=DYNAMIC"
}

ui_click_unchecked_switch() {
  ui_click_node switch "$1" "$2"
}

ui_click_permission_dialog() {
  local attempt

  for attempt in 1 2 3; do
    if ui_click_node \
      permission \
      "" \
      "VPN_PERMISSION_$attempt"; then
      return 0
    fi

    sleep 1
  done

  return 1
}

ui_dismiss_warp_overlay() {
  if ui_click_node \
       warp_update_close \
       com.android.vending \
       WARP_UPDATE_CLOSE; then
    echo "WARP_UPDATE_DIALOG=CLOSED_DYNAMIC"
    sleep 2
    return 0
  fi

  echo "WARP_UPDATE_DIALOG=NOT_PRESENT"
  return 1
}

ui_start_taskbar() {
  local package="com.farmerbb.taskbar"
  local service="${package}/.service.TaskbarService"
  local start_service="${package}/.service.StartMenuService"

  if ! ui_package_exists "$package"; then
    echo "TASKBAR_PACKAGE=MISSING"
    return 1
  fi

  if ui_service_running "$service"; then
    echo "TASKBAR_SERVICE=RUNNING_BEFORE"
    return 0
  fi

  ui_try_start_service "$service" || true
  ui_try_start_service "$start_service" || true
  sleep 2

  if ui_service_running "$service"; then
    echo "TASKBAR_SERVICE=RUNNING_AFTER_SERVICE_START"
    return 0
  fi

  ui_launch_package "$package" || true
  sleep 2
  ui_click_unchecked_switch "$package" TASKBAR_SWITCH ||
    true
  sleep 2

  if ui_service_running "$service"; then
    echo "TASKBAR_SERVICE=RUNNING_AFTER_DYNAMIC_SWITCH"
    return 0
  fi

  echo "TASKBAR_SERVICE=NEEDS_ATTENTION"
  return 1
}

ui_taskbar_point() {
  local mode="$1"
  local expected="${2:-1}"
  local index="${3:-0}"
  local label="${4:-TASKBAR_POINT}"
  local xml point x y

  xml="$(ui_dump_xml "$label")" || {
    echo "${label}_UI_DUMP=FAILED"
    return 1
  }

  if ! point="$(
    python - "$xml" "$mode" "$expected" "$index" <<'__MP_TASKBAR_POINT_PY_20260807__'
import itertools
import math
import re
import sys
import xml.etree.ElementTree as ET

xml_name, mode, expected_raw, index_raw = sys.argv[1:5]
expected = int(expected_raw)
index = int(index_raw)
root = ET.parse(xml_name).getroot()
bounds_re = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")


def parse_bounds(node):
    match = bounds_re.match(node.attrib.get("bounds", ""))
    if not match:
        return None
    left, top, right, bottom = map(int, match.groups())
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def center(box):
    left, top, right, bottom = box
    return (left + right) // 2, (top + bottom) // 2


def words_for(node):
    return " ".join(
        (
            node.attrib.get("text", ""),
            node.attrib.get("content-desc", ""),
            node.attrib.get("resource-id", ""),
        )
    ).casefold()


def dedupe(items):
    by_box = {}
    for score, box in items:
        current = by_box.get(box)
        if current is None or score > current:
            by_box[box] = score
    return [(score, box) for box, score in by_box.items()]


root_box = parse_bounds(root)
if root_box is None:
    root_box = (0, 0, 0, 0)
_, _, root_right, root_bottom = root_box

nodes = []
for node in root.iter("node"):
    box = parse_bounds(node)
    if box is None:
        continue
    package = node.attrib.get("package", "")
    clickable = node.attrib.get("clickable", "") == "true"
    enabled = node.attrib.get("enabled", "") != "false"
    klass = node.attrib.get("class", "").casefold()
    resource = node.attrib.get("resource-id", "").casefold()
    text = node.attrib.get("text", "").strip()
    desc = node.attrib.get("content-desc", "").strip()
    words = words_for(node)
    nodes.append(
        {
            "box": box,
            "package": package,
            "clickable": clickable,
            "enabled": enabled,
            "klass": klass,
            "resource": resource,
            "text": text,
            "desc": desc,
            "words": words,
        }
    )

if root_bottom <= 0 and nodes:
    root_bottom = max(item["box"][3] for item in nodes)

if mode in {"taskbar_expand", "taskbar_start"}:
    candidates = []
    for item in nodes:
        if item["package"] != "com.farmerbb.taskbar" or not item["enabled"]:
            continue
        words = item["words"]
        resource = item["resource"]
        score = 0
        if item["clickable"]:
            score += 40
        if "button" in item["klass"] or "imageview" in item["klass"]:
            score += 15

        if mode == "taskbar_expand":
            positive = (
                "collapse_button",
                "expand_button",
                "show_taskbar",
                "hide_taskbar",
                "toggle_taskbar",
                "taskbar_toggle",
                "collapse",
                "expand",
                "chevron",
                "arrow",
                "show taskbar",
                "hide taskbar",
            )
            negative = ("start", "menu", "drawer", "apps")
            if any(token in words for token in positive):
                score += 120
            if any(token in resource for token in positive):
                score += 80
            if any(token in words for token in negative):
                score -= 120
        else:
            positive = (
                "start_button",
                "start_menu",
                "start menu",
                "all_apps",
                "app_drawer",
                "drawer",
                "apps_button",
            )
            negative = ("collapse", "expand", "chevron", "arrow")
            if any(token in words for token in positive):
                score += 140
            if any(token in resource for token in positive):
                score += 100
            if any(token in words for token in negative):
                score -= 120

        if score >= 100:
            candidates.append((score, item["box"]))

    candidates = dedupe(candidates)
    if not candidates:
        raise SystemExit(11)
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score = candidates[0][0]
    best = [item for item in candidates if item[0] == best_score]
    if len(best) != 1:
        raise SystemExit(12)
    x, y = center(best[0][1])
    print(f"{x} {y}")
    raise SystemExit(0)

if mode == "taskbar_roblox":
    raw = []
    for item in nodes:
        if item["package"] != "com.farmerbb.taskbar" or not item["enabled"]:
            continue
        text = item["text"].casefold()
        desc = item["desc"].casefold()
        if text == "roblox" or desc == "roblox":
            score = 100 + (30 if item["clickable"] else 0)
            raw.append((score, item["box"]))

    raw = dedupe(raw)
    if root_bottom > 0 and len(raw) > expected:
        cutoff = int(root_bottom * 0.86)
        above_taskbar = [item for item in raw if center(item[1])[1] < cutoff]
        if len(above_taskbar) >= expected:
            raw = above_taskbar

    if len(raw) != expected:
        print(f"TASKBAR_ROBLOX_NODE_COUNT={len(raw)}", file=sys.stderr)
        print(f"TASKBAR_ROBLOX_EXPECTED={expected}", file=sys.stderr)
        raise SystemExit(21)
    if index < 0 or index >= expected:
        raise SystemExit(22)

    raw.sort(key=lambda item: (center(item[1])[1], center(item[1])[0]))
    x, y = center(raw[index][1])
    print(f"{x} {y}")
    raise SystemExit(0)

raise SystemExit(30)
__MP_TASKBAR_POINT_PY_20260807__
  )"; then
    rm -f "$xml"
    echo "${label}_NODE=NOT_UNIQUE_OR_NOT_FOUND"
    return 1
  fi

  rm -f "$xml"
  read -r x y <<<"$point"

  [[ "$x" =~ ^[0-9]+$ ]] &&
  [[ "$y" =~ ^[0-9]+$ ]] || {
    echo "${label}_BOUNDS=INVALID"
    return 1
  }

  su -c "input tap '$x' '$y'" >/dev/null 2>&1 || {
    echo "${label}_CLICK=FAILED"
    return 1
  }

  echo "${label}_CLICK=DYNAMIC"
}

ui_taskbar_open_start_menu() {
  local attempt

  for attempt in 1 2 3; do
    if ui_taskbar_point taskbar_start 1 0 "TASKBAR_START_$attempt"; then
      sleep 2
      return 0
    fi

    ui_taskbar_point taskbar_expand 1 0 "TASKBAR_EXPAND_$attempt" || true
    sleep 1

    if ui_taskbar_point taskbar_start 1 0 "TASKBAR_START_RETRY_$attempt"; then
      sleep 2
      return 0
    fi
  done

  echo "TASKBAR_START_MENU=NEEDS_ATTENTION"
  return 1
}

ui_roblox_freeform_packages() {
  local dump

  dump="$(
    su -c 'dumpsys activity activities; dumpsys activity recents' \
      2>/dev/null || true
  )"

  python - 3<<<"$dump" <<'__MP_ROBLOX_FREEFORM_PY_20260807__'
import os
import re

text = os.fdopen(3, "r", encoding="utf-8", errors="replace").read()
packages = sorted(
    set(
        re.findall(
            r"com\.tinh\.vv\.[A-Za-z0-9._-]+",
            text,
        )
    )
)

freeform_tokens = (
    "mode=freeform",
    "windowingmode=freeform",
    "mwindowingmode=freeform",
    "windowingmode=5",
    "mwindowingmode=5",
    "windowing_mode_freeform",
)

lines = text.splitlines()
blocks = []
current = []
for line in lines:
    if re.search(r"(?:^|\s)(?:Task|TaskRecord)\{", line):
        if current:
            blocks.append("\n".join(current))
        current = [line]
    elif current:
        current.append(line)
if current:
    blocks.append("\n".join(current))

for package in packages:
    proven = False
    for block in blocks:
        if package not in block:
            continue
        context = block.casefold()
        if any(token in context for token in freeform_tokens):
            proven = True
            break
    if proven:
        print(package)
__MP_ROBLOX_FREEFORM_PY_20260807__
}

ui_assert_all_roblox_freeform() {
  local package freeform
  local installed=("$@")
  local count=0

  freeform="$(ui_roblox_freeform_packages)"

  for package in "${installed[@]}"; do
    if printf '%s\n' "$freeform" | grep -Fxq "$package"; then
      echo "ROBLOX_FREEFORM_OK=$package"
      count=$((count + 1))
    else
      echo "ROBLOX_FREEFORM_MISSING=$package"
    fi
  done

  echo "ROBLOX_FREEFORM_COUNT=$count"
  echo "ROBLOX_EXPECTED_COUNT=${#installed[@]}"

  [ "$count" = "${#installed[@]}" ]
}

ui_dev_settings_apply() {
  local key before after
  local changed=0 failures=0
  local keys=(
    force_allow_on_external
    force_resizable_activities
    enable_freeform_support
    force_desktop_mode_on_external_displays
  )

  UI_DEV_SETTINGS_CHANGED=0

  for key in "${keys[@]}"; do
    before="$(
      su -c "settings get global '$key'" 2>/dev/null |
        tr -d '\r\n ' ||
        true
    )"

    if [ "$before" != 1 ]; then
      if su -c "settings put global '$key' 1" >/dev/null 2>&1; then
        changed=$((changed + 1))
      else
        echo "DEV_SETTING_${key}=WRITE_FAILED"
        failures=$((failures + 1))
        continue
      fi
    fi

    after="$(
      su -c "settings get global '$key'" 2>/dev/null |
        tr -d '\r\n ' ||
        true
    )"

    if [ "$after" = 1 ]; then
      echo "DEV_SETTING_${key}=ON"
    else
      echo "DEV_SETTING_${key}=VERIFY_FAILED"
      failures=$((failures + 1))
    fi
  done

  UI_DEV_SETTINGS_CHANGED="$changed"
  echo "DEV_SETTINGS_CHANGED=$changed"
  echo "DEV_SETTINGS_FAILURES=$failures"

  [ "$failures" = 0 ]
}

ui_current_boot_id() {
  local value

  value="$(
    cat /proc/sys/kernel/random/boot_id 2>/dev/null ||
      su -c 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null ||
      true
  )"

  value="$(
    printf '%s' "$value" |
      tr -d '\r\n '
  )"

  [[ "$value" =~ ^[0-9A-Fa-f-]{16,64}$ ]] ||
    return 1

  printf '%s\n' "$value"
}

ui_freeform_runtime_probe() {
  local package component freeform_out roblox_out
  local installed=()

  mapfile -t installed < <(
    ui_installed_roblox_packages
  )

  if [ "${#installed[@]}" = 0 ]; then
    echo "UI_FREEFORM_RUNTIME=NO_ROBLOX_FOR_PROBE"
    return 1
  fi

  package="${installed[0]}"
  component="$(ui_launcher_component "$package")"

  case "$component" in
    "$package"/*)
      ;;
    *)
      echo "UI_FREEFORM_RUNTIME=LAUNCHER_NOT_FOUND"
      return 1
      ;;
  esac

  ui_start_taskbar || {
    echo "UI_FREEFORM_RUNTIME=TASKBAR_NOT_READY"
    return 1
  }

  su -c "am force-stop '$package'" >/dev/null 2>&1 ||
    true

  if ! freeform_out="$(
    su -c "am start --windowingMode 5 \
      -n com.farmerbb.taskbar/.activity.InvisibleActivityFreeform" \
      2>&1
  )"; then
    echo "UI_FREEFORM_RUNTIME=TASKBAR_ACTIVITY_FAILED"
    return 1
  fi

  if printf '%s\n' "$freeform_out" |
     grep -qiE 'Error|Exception|Security'; then
    echo "UI_FREEFORM_RUNTIME=TASKBAR_ACTIVITY_REJECTED"
    return 1
  fi

  sleep 1

  if ! roblox_out="$(
    su -c "am start --windowingMode 5 -n '$component'" 2>&1
  )"; then
    su -c "am force-stop '$package'" >/dev/null 2>&1 ||
      true
    echo "UI_FREEFORM_RUNTIME=ROBLOX_LAUNCH_FAILED"
    return 1
  fi

  if printf '%s\n' "$roblox_out" |
     grep -qiE 'Error|Exception|Security'; then
    su -c "am force-stop '$package'" >/dev/null 2>&1 ||
      true
    echo "UI_FREEFORM_RUNTIME=ROBLOX_LAUNCH_REJECTED"
    return 1
  fi

  sleep 4

  if ui_roblox_freeform_packages |
     grep -Fxq "$package"; then
    su -c "am force-stop '$package'" >/dev/null 2>&1 ||
      true
    echo "UI_FREEFORM_RUNTIME=READY"
    return 0
  fi

  su -c "am force-stop '$package'" >/dev/null 2>&1 ||
    true
  echo "UI_FREEFORM_RUNTIME=NOT_READY"
  return 1
}

ui_desktop_reboot_gate() {
  local current recorded verified

  ui_dev_settings_apply || {
    echo "UI_DESKTOP_SETTINGS=NEEDS_ATTENTION"
    return 1
  }

  current="$(ui_current_boot_id)" || {
    echo "UI_DESKTOP_BOOT_ID=UNAVAILABLE"
    return 1
  }

  recorded="$(state_get ui_desktop_reboot_from_boot_id)"
  verified="$(state_get ui_desktop_reboot_verified_boot_id)"

  if [ -n "$recorded" ]; then
    if [ "$recorded" = "$current" ]; then
      echo "UI_DESKTOP_REBOOT=REQUIRED"
      echo "UI_DESKTOP_NEXT=REBOOT_ONCE_THEN_MPROVISION_UI_POST"
      return 2
    fi

    state_set \
      "ui_desktop_reboot_from_boot_id=" \
      "ui_desktop_reboot_verified_boot_id=$current" \
      "ui_desktop_reboot_status=VERIFIED_AFTER_BOOT_CHANGE"

    echo "UI_DESKTOP_REBOOT=VERIFIED_AFTER_BOOT_CHANGE"
    return 0
  fi

  if [ "$UI_DEV_SETTINGS_CHANGED" = 0 ] &&
     [ "$verified" = "$current" ]; then
    echo "UI_DESKTOP_REBOOT=VERIFIED_CURRENT_BOOT"
    return 0
  fi

  if ui_freeform_runtime_probe; then
    state_set \
      "ui_desktop_reboot_from_boot_id=" \
      "ui_desktop_reboot_verified_boot_id=$current" \
      "ui_desktop_reboot_status=ALREADY_EFFECTIVE"

    echo "UI_DESKTOP_REBOOT=ALREADY_EFFECTIVE"
    return 0
  fi

  state_set \
    "ui_desktop_reboot_from_boot_id=$current" \
    "ui_desktop_reboot_verified_boot_id=" \
    "ui_desktop_reboot_status=REQUIRED"

  echo "UI_DESKTOP_REBOOT=REQUIRED"
  echo "UI_DESKTOP_NEXT=REBOOT_ONCE_THEN_MPROVISION_UI_POST"
  return 2
}

ui_roblox_task_geometry() {
  local package="$1"
  local dump_file rc

  dump_file="$(mktemp)"
  if ! su -c 'dumpsys activity activities; dumpsys activity recents' \
       >"$dump_file" 2>/dev/null; then
    rm -f "$dump_file"
    echo "ROBLOX_TASK_GEOMETRY_DUMP_FAILED=$package"
    return 1
  fi

  set +e
  python - "$package" "$dump_file" <<'__MP_ROBLOX_TASK_GEOMETRY_PY_20260807__'
import pathlib
import re
import sys

package = sys.argv[1]
text = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")
lines = text.splitlines()

header_re = re.compile(r"^\s*(?:\*\s*)?(?:Task|TaskRecord)\{")
indices = [i for i, line in enumerate(lines) if header_re.search(line)]
blocks = []
for pos, start in enumerate(indices):
    end = indices[pos + 1] if pos + 1 < len(indices) else len(lines)
    blocks.append("\n".join(lines[start:end]))

if not blocks:
    for i, line in enumerate(lines):
        if package not in line:
            continue
        start = max(0, i - 30)
        end = min(len(lines), i + 35)
        blocks.append("\n".join(lines[start:end]))

freeform_re = re.compile(
    r"(?:windowingMode|mWindowingMode)=(?:5|freeform)|"
    r"(?:^|\s)mode=freeform(?:\s|$)|WINDOWING_MODE_FREEFORM",
    re.IGNORECASE,
)

task_patterns = (
    re.compile(r"\btaskId=(\d+)\b"),
    re.compile(r"(?:Task|TaskRecord)\{[^\n#]*#(\d+)\b"),
    re.compile(r"\bid=(\d+)\b"),
)

bounds_patterns = (
    re.compile(r"(?:mBounds|bounds)=\[(\d+),(\d+)\]\[(\d+),(\d+)\]"),
    re.compile(r"(?:mBounds|bounds)=Rect\(\s*(\d+)\s*,\s*(\d+)\s*-\s*(\d+)\s*,\s*(\d+)\s*\)"),
    re.compile(r"(?:mBounds|bounds)=\[?\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\]?"),
)

candidates = []
for block_index, block in enumerate(blocks):
    if package not in block:
        continue

    task_id = None
    for pattern in task_patterns:
        match = pattern.search(block)
        if match:
            task_id = int(match.group(1))
            break

    valid_bounds = []
    for pattern in bounds_patterns:
        for match in pattern.finditer(block):
            box = tuple(map(int, match.groups()))
            left, top, right, bottom = box
            if right > left and bottom > top:
                valid_bounds.append(box)

    bounds = max(
        valid_bounds,
        key=lambda box: (box[2] - box[0]) * (box[3] - box[1]),
        default=None,
    )

    freeform = bool(freeform_re.search(block))
    lowered = block.casefold()
    resumed = "topresumedactivity" in lowered or "mresumedactivity" in lowered

    score = (
        1 if freeform else 0,
        1 if bounds is not None else 0,
        1 if resumed else 0,
        task_id if task_id is not None else -1,
        block_index,
    )
    candidates.append((score, task_id, bounds, freeform))

candidates.sort(key=lambda item: item[0], reverse=True)
for _, task_id, bounds, freeform in candidates:
    if task_id is None:
        continue
    if bounds is None:
        print(f"{task_id} {1 if freeform else 0} -1 -1 -1 -1")
    else:
        left, top, right, bottom = bounds
        print(f"{task_id} {1 if freeform else 0} {left} {top} {right} {bottom}")
    raise SystemExit(0)

raise SystemExit(3)
__MP_ROBLOX_TASK_GEOMETRY_PY_20260807__
  rc=$?
  set -e
  rm -f "$dump_file"
  return "$rc"
}

ui_roblox_window_bounds() {
  local package="$1"
  local dump_file rc

  dump_file="$(mktemp)"
  if ! su -c 'dumpsys window windows' >"$dump_file" 2>/dev/null; then
    rm -f "$dump_file"
    echo "ROBLOX_WINDOW_BOUNDS_DUMP_FAILED=$package"
    return 1
  fi

  set +e
  python - "$package" "$dump_file" <<'__MP_ROBLOX_WINDOW_BOUNDS_PY_20260807__'
import pathlib
import re
import sys

package = sys.argv[1]
text = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace")
lines = text.splitlines()

window_header = re.compile(r"^\s*Window #\d+ Window\{|^\s*Window\{", re.IGNORECASE)
indices = [i for i, line in enumerate(lines) if window_header.search(line)]
blocks = []
for pos, start in enumerate(indices):
    end = indices[pos + 1] if pos + 1 < len(indices) else len(lines)
    blocks.append("\n".join(lines[start:end]))

if not blocks:
    for i, line in enumerate(lines):
        if package not in line:
            continue
        start = max(0, i - 25)
        end = min(len(lines), i + 45)
        blocks.append("\n".join(lines[start:end]))

frame_patterns = (
    re.compile(r"\bmFrame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]"),
    re.compile(r"\bframe=\[(\d+),(\d+)\]\[(\d+),(\d+)\]", re.IGNORECASE),
    re.compile(r"\bmFrame=Rect\(\s*(\d+)\s*,\s*(\d+)\s*-\s*(\d+)\s*,\s*(\d+)\s*\)"),
)

candidates = []
for block_index, block in enumerate(blocks):
    if package not in block:
        continue
    lowered = block.casefold()
    frames = []
    for pattern_index, pattern in enumerate(frame_patterns):
        for match in pattern.finditer(block):
            box = tuple(map(int, match.groups()))
            left, top, right, bottom = box
            if right <= left or bottom <= top:
                continue
            area = (right - left) * (bottom - top)
            frames.append((pattern_index, area, box))

    if not frames:
        continue

    frames.sort(key=lambda item: (1 if item[0] == 0 else 0, item[1]), reverse=True)
    pattern_index, area, box = frames[0]
    visible = any(token in lowered for token in (
        "mhasurface=true",
        "isonscreen=true",
        "isvisible=true",
        "mviewvisibility=0x0",
    ))
    score = (
        1 if visible else 0,
        1 if pattern_index == 0 else 0,
        area,
        block_index,
    )
    candidates.append((score, box))

if not candidates:
    raise SystemExit(3)

candidates.sort(key=lambda item: item[0], reverse=True)
left, top, right, bottom = candidates[0][1]
print(left, top, right, bottom)
__MP_ROBLOX_WINDOW_BOUNDS_PY_20260807__
  rc=$?
  set -e
  rm -f "$dump_file"
  return "$rc"
}

ui_landscape_target_bounds() {
  local raw width height target_width target_height left top right bottom

  raw="$(su -c 'dumpsys window displays; wm size' 2>/dev/null || true)"

  read -r width height < <(
    python - 3<<<"$raw" <<'__MP_LANDSCAPE_BOUNDS_PY_20260807__'
import os
import re

text = os.fdopen(3, "r", encoding="utf-8", errors="replace").read()
current = re.findall(r"\b(?:cur|app)=(\d+)x(\d+)\b", text)
if current:
    width, height = map(int, current[-1])
else:
    matches = re.findall(r"(?:Override|Physical) size:\s*(\d+)x(\d+)", text)
    if not matches:
        matches = re.findall(r"\b(\d{3,5})x(\d{3,5})\b", text)
    if not matches:
        raise SystemExit(1)
    width, height = map(int, matches[-1])
if width < 400 or height < 300:
    raise SystemExit(2)
print(width, height)
__MP_LANDSCAPE_BOUNDS_PY_20260807__
  ) || {
    echo "ROBLOX_LANDSCAPE_SCREEN_SIZE=UNAVAILABLE"
    return 1
  }

  target_width=$((width * 74 / 100))
  target_height=$((height * 68 / 100))

  if [ "$target_width" -le "$target_height" ]; then
    target_height=$((target_width * 9 / 16))
  fi

  [ "$target_width" -gt "$target_height" ] || {
    echo "ROBLOX_LANDSCAPE_TARGET=INVALID"
    return 1
  }

  left=$(((width - target_width) / 2))
  top=$(((height - target_height) / 2))
  right=$((left + target_width))
  bottom=$((top + target_height))

  [ "$left" -ge 0 ] &&
  [ "$top" -ge 0 ] &&
  [ "$right" -le "$width" ] &&
  [ "$bottom" -le "$height" ] || {
    echo "ROBLOX_LANDSCAPE_TARGET=OUT_OF_RANGE"
    return 1
  }

  echo "$left $top $right $bottom"
}

ui_resize_roblox_task_landscape() {
  local package="$1"
  local attempt geometry task_id freeform left top right bottom
  local target_left target_top target_right target_bottom
  local target_width target_height actual_width actual_height
  local resize_out rc last_task_id="" last_bounds="UNAVAILABLE"

  read -r target_left target_top target_right target_bottom < <(
    ui_landscape_target_bounds
  ) || return 1

  target_width=$((target_right - target_left))
  target_height=$((target_bottom - target_top))

  for attempt in 1 2 3 4 5 6 7 8; do
    if geometry="$(ui_roblox_task_geometry "$package")"; then
      read -r task_id freeform left top right bottom <<<"$geometry"
      if [[ "$task_id" =~ ^[0-9]+$ ]]; then
        last_task_id="$task_id"
        break
      fi
    fi
    sleep 1
  done

  [[ "$last_task_id" =~ ^[0-9]+$ ]] || {
    echo "ROBLOX_TASK_ID=NOT_FOUND:$package"
    return 1
  }

  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if geometry="$(ui_roblox_task_geometry "$package")"; then
      read -r task_id freeform left top right bottom <<<"$geometry"
      if [[ "$task_id" =~ ^[0-9]+$ ]]; then
        last_task_id="$task_id"
      fi
    else
      freeform=0
    fi

    if [ "$attempt" = 1 ] || [ "$attempt" = 4 ] || [ "$attempt" = 7 ]; then
      set +e
      resize_out="$(
        su -c "am task resize '$last_task_id' \
          '$target_left' '$target_top' '$target_right' '$target_bottom'" \
          2>&1
      )"
      rc=$?
      set -e

      if [ "$rc" -ne 0 ] ||
         printf '%s\n' "$resize_out" |
           grep -qiE 'Error|Exception|Security|Unknown'; then
        echo "ROBLOX_LANDSCAPE_RESIZE=FAILED:$package"
        return 1
      fi
    fi

    sleep 1

    if read -r left top right bottom < <(
      ui_roblox_window_bounds "$package"
    ); then
      if [[ "$left" =~ ^[0-9]+$ ]] &&
         [[ "$top" =~ ^[0-9]+$ ]] &&
         [[ "$right" =~ ^[0-9]+$ ]] &&
         [[ "$bottom" =~ ^[0-9]+$ ]]; then
        actual_width=$((right - left))
        actual_height=$((bottom - top))
        last_bounds="${left},${top},${right},${bottom}"

        if [ "$freeform" = 1 ] &&
           [ "$actual_width" -gt "$actual_height" ] &&
           [ "$actual_width" -ge $((target_width * 70 / 100)) ] &&
           [ "$actual_width" -le $((target_width * 125 / 100)) ] &&
           [ "$actual_height" -ge $((target_height * 65 / 100)) ] &&
           [ "$actual_height" -le $((target_height * 130 / 100)) ]; then
          echo "ROBLOX_LANDSCAPE_RESIZE_OK=$package"
          echo "ROBLOX_LANDSCAPE_TASK_ID=$last_task_id"
          echo "ROBLOX_LANDSCAPE_BOUNDS=$last_bounds"
          return 0
        fi
      fi
    fi
  done

  echo "ROBLOX_LANDSCAPE_VERIFY=FAILED:$package"
  echo "ROBLOX_LANDSCAPE_FREEFORM=${freeform:-0}"
  echo "ROBLOX_LANDSCAPE_BOUNDS=$last_bounds"
  return 1
}

ui_assert_all_roblox_landscape_freeform() {
  local package geometry task_id freeform left top right bottom
  local target_left target_top target_right target_bottom
  local target_width target_height actual_width actual_height
  local installed=("$@")
  local count=0

  read -r target_left target_top target_right target_bottom < <(
    ui_landscape_target_bounds
  ) || return 1
  target_width=$((target_right - target_left))
  target_height=$((target_bottom - target_top))

  for package in "${installed[@]}"; do
    freeform=0
    if geometry="$(ui_roblox_task_geometry "$package")"; then
      read -r task_id freeform left top right bottom <<<"$geometry"
    fi

    if read -r left top right bottom < <(
      ui_roblox_window_bounds "$package"
    ); then
      if [[ "$left" =~ ^[0-9]+$ ]] &&
         [[ "$top" =~ ^[0-9]+$ ]] &&
         [[ "$right" =~ ^[0-9]+$ ]] &&
         [[ "$bottom" =~ ^[0-9]+$ ]]; then
        actual_width=$((right - left))
        actual_height=$((bottom - top))
        if [ "$freeform" = 1 ] &&
           [ "$actual_width" -gt "$actual_height" ] &&
           [ "$actual_width" -ge $((target_width * 70 / 100)) ] &&
           [ "$actual_width" -le $((target_width * 125 / 100)) ] &&
           [ "$actual_height" -ge $((target_height * 65 / 100)) ] &&
           [ "$actual_height" -le $((target_height * 130 / 100)) ]; then
          echo "ROBLOX_LANDSCAPE_FREEFORM_OK=$package"
          count=$((count + 1))
          continue
        fi
      fi
    fi

    echo "ROBLOX_LANDSCAPE_FREEFORM_MISSING=$package"
  done

  echo "ROBLOX_LANDSCAPE_FREEFORM_COUNT=$count"
  echo "ROBLOX_EXPECTED_COUNT=${#installed[@]}"
  [ "$count" = "${#installed[@]}" ]
}

ui_launch_roblox_freeform() {
  local package="$1"
  local component freeform_out roblox_out

  component="$(ui_launcher_component "$package")"

  case "$component" in
    "$package"/*)
      echo "ROBLOX_LAUNCHER_FOUND=$package"
      ;;
    *)
      echo "ROBLOX_LAUNCHER_NOT_FOUND=$package"
      return 1
      ;;
  esac

  if ! freeform_out="$(
    su -c "am start --windowingMode 5 \
      -n com.farmerbb.taskbar/.activity.InvisibleActivityFreeform" \
      2>&1
  )"; then
    echo "TASKBAR_FREEFORM_ACTIVITY_FAILED=$package"
    return 1
  fi

  if printf '%s\n' "$freeform_out" |
     grep -qiE 'Error|Exception|Security'; then
    echo "TASKBAR_FREEFORM_ACTIVITY_REJECTED=$package"
    return 1
  fi

  echo "TASKBAR_FREEFORM_ACTIVITY_STARTED=$package"
  sleep 1

  if ! roblox_out="$(
    su -c "am start --windowingMode 5 -n '$component'" 2>&1
  )"; then
    echo "ROBLOX_FREEFORM_LAUNCH_FAILED=$package"
    return 1
  fi

  if printf '%s\n' "$roblox_out" |
     grep -qiE 'Error|Exception|Security'; then
    echo "ROBLOX_FREEFORM_LAUNCH_REJECTED=$package"
    return 1
  fi

  echo "ROBLOX_FREEFORM_LAUNCH_STARTED=$package"
  sleep 2

  ui_resize_roblox_task_landscape "$package" || {
    echo "ROBLOX_FREEFORM_LANDSCAPE_FAILED=$package"
    return 1
  }

  return 0
}

ui_current_screen_size() {
  local raw

  raw="$(su -c 'dumpsys window displays; wm size' 2>/dev/null || true)"

  python - 3<<<"$raw" <<'__MP_CURRENT_SCREEN_SIZE_PY_20260807__'
import os
import re

text = os.fdopen(3, "r", encoding="utf-8", errors="replace").read()

current = re.findall(r"\b(?:cur|app)=(\d+)x(\d+)\b", text)
if current:
    width, height = map(int, current[-1])
else:
    matches = re.findall(r"(?:Override|Physical) size:\s*(\d+)x(\d+)", text)
    if not matches:
        matches = re.findall(r"\b(\d{3,5})x(\d{3,5})\b", text)
    if not matches:
        raise SystemExit(1)
    width, height = map(int, matches[-1])

if width < 400 or height < 300:
    raise SystemExit(2)

print(width, height)
__MP_CURRENT_SCREEN_SIZE_PY_20260807__
}

ui_assert_all_roblox_not_maximized() {
  local package geometry task_id freeform left top right bottom
  local screen_width screen_height actual_width actual_height
  local max_width_limit max_height_limit min_width min_height
  local maximized_like
  local installed=("$@")
  local count=0

  read -r screen_width screen_height < <(
    ui_current_screen_size
  ) || {
    echo "ROBLOX_NOT_MAXIMIZED_SCREEN_SIZE=UNAVAILABLE"
    return 1
  }

  max_width_limit=$((screen_width * 92 / 100))
  max_height_limit=$((screen_height * 88 / 100))
  min_width=$((screen_width * 38 / 100))
  min_height=$((screen_height * 28 / 100))

  for package in "${installed[@]}"; do
    freeform=0
    maximized_like=0

    if geometry="$(ui_roblox_task_geometry "$package")"; then
      read -r task_id freeform left top right bottom <<<"$geometry"
    fi

    if read -r left top right bottom < <(
      ui_roblox_window_bounds "$package"
    ); then
      if [[ "$left" =~ ^[0-9]+$ ]] &&
         [[ "$top" =~ ^[0-9]+$ ]] &&
         [[ "$right" =~ ^[0-9]+$ ]] &&
         [[ "$bottom" =~ ^[0-9]+$ ]]; then
        actual_width=$((right - left))
        actual_height=$((bottom - top))

        if [ "$actual_width" -ge "$max_width_limit" ] &&
           [ "$actual_height" -ge "$max_height_limit" ]; then
          maximized_like=1
          echo "ROBLOX_MAXIMIZED_LIKE=$package"
        fi

        if [ "$freeform" = 1 ] &&
           [ "$actual_width" -gt "$actual_height" ] &&
           [ "$maximized_like" = 0 ] &&
           [ "$actual_width" -ge "$min_width" ] &&
           [ "$actual_height" -ge "$min_height" ]; then
          echo "ROBLOX_NOT_MAXIMIZED_OK=$package"
          count=$((count + 1))
          continue
        fi
      fi
    fi

    echo "ROBLOX_NOT_MAXIMIZED_MISSING=$package"
  done

  echo "ROBLOX_NOT_MAXIMIZED_COUNT=$count"
  echo "ROBLOX_EXPECTED_COUNT=${#installed[@]}"
  [ "$count" = "${#installed[@]}" ]
}

ui_roblox_maximized_like() {
  local package="$1"
  local screen_width screen_height left top right bottom
  local actual_width actual_height max_width_limit max_height_limit

  read -r screen_width screen_height < <(
    ui_current_screen_size
  ) || {
    echo "ROBLOX_MAXIMIZED_CHECK_SCREEN=UNAVAILABLE:$package"
    return 2
  }

  if ! read -r left top right bottom < <(
    ui_roblox_window_bounds "$package"
  ); then
    echo "ROBLOX_MAXIMIZED_CHECK_BOUNDS=UNAVAILABLE:$package"
    return 1
  fi

  [[ "$left" =~ ^[0-9]+$ ]] &&
  [[ "$top" =~ ^[0-9]+$ ]] &&
  [[ "$right" =~ ^[0-9]+$ ]] &&
  [[ "$bottom" =~ ^[0-9]+$ ]] || {
    echo "ROBLOX_MAXIMIZED_CHECK_BOUNDS=INVALID:$package"
    return 1
  }

  actual_width=$((right - left))
  actual_height=$((bottom - top))
  max_width_limit=$((screen_width * 92 / 100))
  max_height_limit=$((screen_height * 88 / 100))

  if [ "$actual_width" -ge "$max_width_limit" ] &&
     [ "$actual_height" -ge "$max_height_limit" ]; then
    echo "ROBLOX_MAXIMIZED_DETECTED=$package"
    echo "ROBLOX_MAXIMIZED_BOUNDS=${left},${top},${right},${bottom}"
    return 0
  fi

  return 1
}

ui_open_all_roblox() {
  local package
  local installed=()
  local intermediate_failures=0
  local maximized_count=0
  local manual_pending

  mapfile -t installed < <(
    ui_installed_roblox_packages
  )

  echo "ROBLOX_INSTALLED_COUNT=${#installed[@]}"

  [ "${#installed[@]}" -gt 0 ] || {
    echo "ROBLOX_OPEN_ALL=NO_PACKAGES"
    return 1
  }

  manual_pending="$(state_get ui_roblox_manual_taskbar 2>/dev/null || true)"

  if [ "$manual_pending" = "REQUIRED" ]; then
    echo "ROBLOX_MANUAL_TASKBAR=VERIFY_ONLY"

    if ui_assert_all_roblox_landscape_freeform "${installed[@]}" &&
       ui_assert_all_roblox_not_maximized "${installed[@]}"; then
      state_set "ui_roblox_manual_taskbar="
      echo "ROBLOX_MANUAL_TASKBAR=CONFIRMED"
      echo "ROBLOX_OPEN_ALL=OK_AFTER_MANUAL_TASKBAR"
      return 0
    fi

    echo "ROBLOX_MANUAL_TASKBAR=REQUIRED"
    echo "ROBLOX_MANUAL_ACTION=NHAN_ICON_ROBLOX_DANG_PHONG_TO_MOT_LAN_ROI_CHAY_LAI_MPROVISION_UI_POST"
    return 2
  fi

  for package in "${installed[@]}"; do
    if ! ui_launch_roblox_freeform "$package"; then
      echo "ROBLOX_PACKAGE_NEEDS_ATTENTION=$package"
      intermediate_failures=$((intermediate_failures + 1))
    fi
    sleep 2
  done

  echo "ROBLOX_INTERMEDIATE_FAILURES=$intermediate_failures"
  sleep 3

  if ui_assert_all_roblox_landscape_freeform "${installed[@]}" &&
     ui_assert_all_roblox_not_maximized "${installed[@]}"; then
    state_set "ui_roblox_manual_taskbar="
    echo "ROBLOX_OPEN_ALL=OK_NO_MANUAL_TASKBAR"
    return 0
  fi

  for package in "${installed[@]}"; do
    if ui_roblox_maximized_like "$package"; then
      maximized_count=$((maximized_count + 1))
    fi
  done

  state_set "ui_roblox_manual_taskbar=REQUIRED"

  echo "ROBLOX_MANUAL_TASKBAR=REQUIRED"
  echo "ROBLOX_MANUAL_MAXIMIZED_COUNT=$maximized_count"
  echo "ROBLOX_MANUAL_ACTION=NHAN_ICON_ROBLOX_DANG_PHONG_TO_MOT_LAN_ROI_CHAY_LAI_MPROVISION_UI_POST"
  echo "ROBLOX_OPEN_ALL=WAITING_MANUAL_TASKBAR"
  return 2
}

ui_start_vpn() {
  local package="com.cloudflare.onedotonedotonedotone"
  local service="${package}/com.cloudflare.app.vpnservice.CloudflareVpnService"
  local popup_closed=0

  if ! ui_package_exists "$package"; then
    echo "VPN_1_1_1_1_PACKAGE=MISSING"
    return 1
  fi

  if ui_service_running "$service"; then
    echo "VPN_1_1_1_1=RUNNING_BEFORE"
    return 0
  fi

  ui_launch_package "$package" || true
  sleep 4

  if ui_service_running "$service"; then
    echo "VPN_1_1_1_1=RUNNING_AFTER_OPEN"
    return 0
  fi

  if ui_dismiss_warp_overlay; then
    popup_closed=1
    ui_launch_package "$package" || true
    sleep 3
  fi

  if ! ui_click_node \
       warp_switch \
       "$package" \
       WARP_SWITCH; then
    if [ "$popup_closed" = 0 ]; then
      ui_launch_package "$package" || true
      sleep 2
    fi

    ui_click_node \
      warp_switch \
      "$package" \
      WARP_SWITCH_RETRY || {
        echo "VPN_1_1_1_1_SWITCH=NOT_FOUND"
        return 1
      }
  fi

  sleep 2
  ui_click_permission_dialog || true
  sleep 5

  if ui_service_running "$service"; then
    echo "VPN_1_1_1_1=RUNNING_AFTER_DYNAMIC_SWITCH"
    return 0
  fi

  echo "VPN_1_1_1_1=NEEDS_ATTENTION"
  return 1
}

ui_post_prepare() {
  local boot_package="com.termux.boot"
  local control_package="ahapps.controlthescreenorientation"
  local control_service="${control_package}/.Control_service"
  local failures=0 gate_rc=0

  echo "UI_POST_AUTOMATION=START"
  echo "UI_POST_AUTOMATION_VERSION=10"

  if ! root_ok; then
    echo "UI_ROOT=NEEDS_ATTENTION"
    state_set "ui_post_status=NEEDS_ATTENTION"
    return 1
  fi

  ui_desktop_reboot_gate || gate_rc=$?

  if [ "$gate_rc" = 2 ]; then
    state_set "ui_post_status=REBOOT_REQUIRED"
    echo "UI_POST_AUTOMATION=REBOOT_REQUIRED"
    echo "UI_POST_AUTOMATION_FAILURES=0"
    return 1
  fi

  if [ "$gate_rc" -ne 0 ]; then
    state_set "ui_post_status=NEEDS_ATTENTION"
    echo "UI_POST_AUTOMATION=NEEDS_ATTENTION"
    echo "UI_POST_AUTOMATION_FAILURES=1"
    return 1
  fi

  if ui_package_exists "$boot_package" &&
     ui_launch_package "$boot_package"; then
    echo "TERMUX_BOOT_OPEN=OK"
  else
    echo "TERMUX_BOOT_OPEN=NEEDS_ATTENTION"
    failures=$((failures + 1))
  fi

  if ui_package_exists "$control_package"; then
    if ! ui_service_running "$control_service"; then
      ui_try_start_service "$control_service" ||
        ui_launch_package "$control_package" ||
        true
      sleep 2
    fi

    if ui_service_running "$control_service"; then
      echo "CONTROL_SERVICE=RUNNING"
    else
      echo "CONTROL_SERVICE=NEEDS_ATTENTION"
      failures=$((failures + 1))
    fi
  else
    echo "CONTROL_PACKAGE=MISSING"
    failures=$((failures + 1))
  fi

  ui_open_all_roblox ||
    failures=$((failures + 1))

  echo "VPN_1_1_1_1=MANUAL"

  if [ "$failures" -ne 0 ]; then
    state_set "ui_post_status=NEEDS_ATTENTION"
    echo "UI_POST_AUTOMATION=NEEDS_ATTENTION"
    echo "UI_POST_AUTOMATION_FAILURES=$failures"
    return 1
  fi

  state_set "ui_post_status=OK"
  echo "UI_POST_AUTOMATION=OK"
}

finalize() {
  local completed_at

  if ! ui_post_prepare; then
    state_set \
      "phase=manual_post" \
      "manual_post_confirmed_at="
    warn "Tự động hóa giao diện chưa đạt; xử lý đúng mục NEEDS_ATTENTION rồi chạy lại mprovision done post"
    show_manual_post
    return
  fi

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

  publish_next_sources 0

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
  local phase pre_status post_status report_status publish_status next

  [ -s "$STATE" ] || {
    echo "MPROVISION_STATUS=CHƯA_KHỞI_TẠO"
    return
  }

  phase="$(state_get phase)"
  pre_status="PENDING"
  post_status="PENDING"
  report_status="MISSING"
  publish_status="$(state_get publish_next_status)"
  [ -n "$publish_status" ] || publish_status="PENDING"
  next="mprovision reconcile"

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
      next="KHÔNG_CẦN_LỆNH_THÊM"
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
  echo "PUBLISH_NEXT=$publish_status"
  echo "WIZARD_STEP=$(state_get wizard_step)"
  echo "NEXT=$next"
}

intent() {
  local phase step action manual message ref run action_id=""

  [ -s "$STATE" ] || {
    echo "MPROVISION_INTENT=UNINITIALIZED"
    return 1
  }

  phase="$(state_get phase)"
  step="$(state_get wizard_step)"
  ref="$(state_get provision_ref)"
  run="$(state_get run_id)"

  [ -n "$ref" ] || ref="$PROVISION_REF"

  action="AUTO"
  manual="NO"
  message="Tự động tiếp tục"

  case "$phase" in
    preflight|await_root)
      if root_ok; then
        action="AUTO"
        message="Root đã sẵn sàng"
      else
        action="ENABLE_ROOT"
        manual="YES"
        message="Bật root"
      fi
      ;;
    manual_pre)
      if ! root_ok; then
        action="ENABLE_ROOT"
        manual="YES"
        message="Bật root"
      elif ! google_account_present; then
        action="OPEN_GOOGLE"
        manual="YES"
        message="Đăng nhập Google"
      elif [ "$step" = "await_swift_backup_before" ]; then
        action="CONFIRM_SWIFT_BACKUP"
        manual="YES"
        message="Xác nhận Swift backup trước"
      else
        action="OPEN_SWIFT_BACKUP"
        manual="YES"
        message="Backup Termux và dữ liệu cũ bằng Swift"
      fi
      ;;
    await_root_setup)
      if root_ok; then
        action="AUTO"
        message="Tiếp tục setup"
      else
        action="ENABLE_ROOT"
        manual="YES"
        message="Bật lại root"
      fi
      ;;
    await_rclone_before|await_rclone_after)
      action="RCLONE_REQUIRED"
      manual="YES"
      message="Cấu hình gdrive:"
      ;;
    automatic|finalize)
      action="AUTO"
      message="Tự động xử lý"
      ;;
    manual_post)
      case "$step" in
        manual_post_remaining)
          action="MANUAL_POST"
          manual="YES"
          message="Hoàn tất các bước thủ công còn lại"
          ;;
        *)
          action="OPEN_SWIFT_RESTORE"
          manual="YES"
          message="Khôi phục RESTORE_DATA bằng Swift"
          ;;
      esac
      ;;
    complete)
      action="COMPLETE"
      manual="NO"
      message="Hoàn tất"
      ;;
    *)
      action="INVALID_STATE"
      manual="YES"
      message="State không hợp lệ"
      ;;
  esac

  if [ "$manual" = "YES" ]; then
    action_id="$(
      printf '%s|%s|%s|%s\n' \
        "$(state_get device_id)" \
        "$run" \
        "$phase" \
        "${step:-none}" |
        sha256sum |
        awk '{print substr($1,1,20)}'
    )"
  fi

  echo "MPROVISION_INTENT=OK"
  echo "DEVICE_ID=$(state_get device_id)"
  echo "DEVICE_GROUP=$(state_get device_group)"
  echo "PROVISION_REF=$ref"
  echo "PHASE=$phase"
  echo "STEP=$step"
  echo "ACTION=$action"
  echo "MANUAL_REQUIRED=$manual"
  echo "ACTION_ID=$action_id"
  echo "MESSAGE=$message"
}

reconcile() {
  resume
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
  local value expected_remote phase3a_checklist

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

  phase3a_checklist="$(show_manual_post)"
  printf '%s
' "$phase3a_checklist" |
    grep -Fq 'Swift Backup có data: Drive, Control, 1.1.1.1, ZArchiver và Taskbar.' ||
    die "self-test checklist post"

  declare -F wizard >/dev/null ||
    die "self-test wizard function"
  declare -F install_wizard_shortcut >/dev/null ||
    die "self-test wizard shortcut installer"
  declare -F install_wizard_supervisor >/dev/null ||
    die "self-test wizard supervisor installer"
  declare -F wizard_notify >/dev/null ||
    die "self-test wizard notification function"
  declare -F google_account_present >/dev/null ||
    die "self-test Google account detector"

  case "$WIZARD_SHORTCUT" in
    */.shortcuts/tasks/AOTSCRIPT_SETUP)
      ;;
    *)
      die "self-test wizard background path"
      ;;
  esac

  wizard_shortcut_content > "$tmp/aotscript-setup-shortcut"
  bash -n "$tmp/aotscript-setup-shortcut"

  grep -Fq \
    '/data/data/com.termux/files/usr/bin/mprovision wizard' \
    "$tmp/aotscript-setup-shortcut" ||
    die "self-test wizard shortcut command"

  grep -Fq 'wizard.log' \
    "$tmp/aotscript-setup-shortcut" ||
    die "self-test wizard shortcut log"
  declare -F audit >/dev/null ||
    die "self-test audit function"
  declare -F audit_display_values >/dev/null ||
    die "self-test audit display function"
  declare -F ui_post_prepare >/dev/null ||
    die "self-test UI post function"
  declare -F ui_start_taskbar >/dev/null ||
    die "self-test Taskbar UI function"
  declare -F ui_open_all_roblox >/dev/null ||
    die "self-test all Roblox function"
  declare -F ui_start_vpn >/dev/null ||
    die "self-test VPN UI function"
  declare -F ui_node_center >/dev/null ||
    die "self-test UI parser function"

  cat > "$tmp/ui-switch.xml" <<'__MP_UI_SELF_TEST_SWITCH_XML__'
<hierarchy>
  <node package="com.farmerbb.taskbar" class="android.widget.Switch" clickable="true" checked="false" bounds="[100,100][200,150]" />
</hierarchy>
__MP_UI_SELF_TEST_SWITCH_XML__

  value="$(
    ui_node_center \
      "$tmp/ui-switch.xml" \
      switch \
      com.farmerbb.taskbar
  )" || die "self-test UI switch parser"

  [ "$value" = "150 125" ] ||
    die "self-test UI switch center"


  cat > "$tmp/ui-warp-switch.xml" <<'__MP_UI_SELF_TEST_WARP_SWITCH_XML__'
<hierarchy>
  <node package="com.cloudflare.onedotonedotonedotone" class="android.widget.Switch" resource-id="com.cloudflare.onedotonedotonedotone:id/launchSwitch" text="TẮT" clickable="true" checked="false" bounds="[564,187][716,263]" />
</hierarchy>
__MP_UI_SELF_TEST_WARP_SWITCH_XML__

  value="$(
    ui_node_center \
      "$tmp/ui-warp-switch.xml" \
      warp_switch \
      com.cloudflare.onedotonedotonedotone
  )" || die "self-test WARP switch parser"

  [ "$value" = "640 225" ] ||
    die "self-test WARP switch center"

  cat > "$tmp/ui-warp-popup.xml" <<'__MP_UI_SELF_TEST_WARP_POPUP_XML__'
<hierarchy>
  <node package="com.android.vending" class="android.widget.ImageView" content-desc="Đóng hộp thoại cập nhật" clickable="true" bounds="[800,110][856,166]" />
</hierarchy>
__MP_UI_SELF_TEST_WARP_POPUP_XML__

  value="$(
    ui_node_center \
      "$tmp/ui-warp-popup.xml" \
      warp_update_close \
      com.android.vending
  )" || die "self-test WARP popup parser"

  [ "$value" = "828 138" ] ||
    die "self-test WARP popup center"
  cat > "$tmp/ui-permission.xml" <<'__MP_UI_SELF_TEST_PERMISSION_XML__'
<hierarchy>
  <node text="Cho phép" class="android.widget.Button" clickable="true" bounds="[300,400][500,500]" />
</hierarchy>
__MP_UI_SELF_TEST_PERMISSION_XML__

  value="$(
    ui_node_center \
      "$tmp/ui-permission.xml" \
      permission \
      ""
  )" || die "self-test UI permission parser"

  [ "$value" = "400 450" ] ||
    die "self-test UI permission center"

  value="$(
    audit_display_values \
      'Physical size: 1600x2560' \
      'Override density: 366'
  )" || die "self-test display audit"

  printf '%s
' "$value" |
    grep -Fxq 'DISPLAY_TARGET_700DP=OK' ||
    die "self-test display target"


  printf 'sensitive-placeholder\n' > "$SHOUKO/agent_config.json"
  printf 'm116\n' > "$SHOUKO/device_id.txt"
  printf 'NOVA\n' > "$SHOUKO/device_group.txt"
  printf '{}\n' > "$SHOUKO/agent_state.json"
  printf '{}\n' > "$SHOUKO/provision_report.json"

  zip_shouko_next "$tmp/out/Shouko-next.zip"
  verify_source_zip "$tmp/out/Shouko-next.zip" shouko
  verify_source_zip "$tmp/out/Delta.zip" delta

  python - "$tmp/out/Shouko-next.zip" <<'__MP_PUBLISH_SELF_TEST_ZIP_PY_20260806__'
import pathlib
import zipfile
import sys

blocked = (
    "agent_config.json",
    "device_id.txt",
    "device_group.txt",
    "agent_state.json",
    "provision_report.json",
    "provision_report.txt",
)

with zipfile.ZipFile(pathlib.Path(sys.argv[1])) as archive:
    names = archive.namelist()

for name in names:
    base = pathlib.PurePosixPath(name).name.lower()
    if any(base == item or base.startswith(item + ".") for item in blocked):
        raise SystemExit(f"forbidden published path: {name}")
__MP_PUBLISH_SELF_TEST_ZIP_PY_20260806__

  cat > "$tmp/setup-fixture.sh" <<'__MP_PUBLISH_SELF_TEST_SETUP__'
download_zip \
  "1vDjK3hNCyT0B_rbAcsPlelD-TJJKzwG1" \
  "$SHOUKO_ZIP"

download_zip \
  "1BkHn3hyDfobTcy5tqhT9LePe01OzEHQ-" \
  "$DELTA_ZIP"
__MP_PUBLISH_SELF_TEST_SETUP__

  mapfile -t publish_ids < <(
    extract_setup_source_ids "$tmp/setup-fixture.sh"
  )

  [ "${#publish_ids[@]}" = 2 ] ||
    die "self-test publish setup ID count"

  [ "${publish_ids[0]}" = "$SOURCE_SHOUKO_ID" ] ||
    die "self-test Shouko source ID"

  [ "${publish_ids[1]}" = "$SOURCE_DELTA_ID" ] ||
    die "self-test Delta source ID"

  cat > "$tmp/listing.json" <<'__MP_PUBLISH_SELF_TEST_LISTING__'
[
  {
    "Path": "Shouko.zip",
    "Name": "Shouko.zip",
    "Size": 123,
    "IsDir": false,
    "ID": "1vDjK3hNCyT0B_rbAcsPlelD-TJJKzwG1"
  },
  {
    "Path": "Delta.zip",
    "Name": "Delta.zip",
    "Size": 456,
    "IsDir": false,
    "ID": "1BkHn3hyDfobTcy5tqhT9LePe01OzEHQ-"
  }
]
__MP_PUBLISH_SELF_TEST_LISTING__

  [ "$(
      source_id_from_listing "$tmp/listing.json" Shouko.zip
    )" = "$SOURCE_SHOUKO_ID" ] ||
    die "self-test remote Shouko ID"

  [ "$(
      source_id_from_listing "$tmp/listing.json" Delta.zip
    )" = "$SOURCE_DELTA_ID" ] ||
    die "self-test remote Delta ID"

  state_set publish_next_status=OK

  value="$(status)"

  printf '%s\n' "$value" |
    grep -Fxq 'PUBLISH_NEXT=OK' ||
    die "self-test publish status"

  printf '%s\n' "$value" |
    grep -Fxq 'NEXT=KHÔNG_CẦN_LỆNH_THÊM' ||
    die "self-test completed next"

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

  echo "MPROVISION_PHASE3A_SELF_TEST=OK"
  echo "MPROVISION_PUBLISH_NEXT_SELF_TEST=OK"
}

main() {
  local id group run

  case "${1:-}" in
    self-test|status|checklist|audit|intent|-h|--help|help|"")
      ;;
    *)
      run_lock_acquire
      trap run_lock_release EXIT
      ;;
  esac

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
    intent)
      [ "$#" = 1 ] ||
        die "intent không nhận tham số"
      intent
      ;;
    reconcile)
      [ "$#" = 1 ] ||
        die "reconcile không nhận tham số"
      install_wrapper
      reconcile
      ;;
    resume)
      [ "$#" = 1 ] ||
        die "resume không nhận tham số"
      install_wrapper
      resume
      ;;
    audit)
      [ "$#" = 1 ] ||
        die "audit không nhận tham số"
      install_wrapper
      audit
      ;;
    report)
      [ "$#" = 1 ] ||
        die "report không nhận tham số"
      install_wrapper
      show_report
      ;;
    ui-post)
      [ "$#" = 1 ] ||
        die "Cách dùng: mprovision ui-post"
      install_wrapper
      [ -s "$STATE" ] ||
        die "Chưa khởi tạo mprovision"
      ui_post_prepare
      ;;
    wizard)
      [ "$#" = 1 ] ||
        die "wizard không nhận tham số"
      install_wrapper
      wizard
      ;;
    publish-next)
      [ "$#" = 1 ] ||
        die "publish-next không nhận tham số"
      install_wrapper
      [ -s "$STATE" ] ||
        die "Chưa khởi tạo mprovision"
      [ "$(state_get phase)" = complete ] ||
        die "Chỉ publish lại khi máy đã complete"
      final_check
      publish_next_sources 1
      write_completion_report "$(agent_count)"
      upload_completion_report ||
        die "Không upload lại được báo cáo hoàn tất"
      status
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
      ensure_termux_prereqs
      mkdir -p "$STATE_DIR"

      [ ! -s "$STATE" ] ||
        cp -p "$STATE" "$STATE.bak-$run"

      state_set \
        version="$VERSION" \
        provision_ref="$PROVISION_REF" \
        device_id="$id" \
        device_group="$group" \
        phase=preflight \
        run_id="$run" \
        backup_before= \
        backup_before_remote= \
        backup_after= \
        backup_after_remote= \
        swift_install=0 \
        wizard_step= \
        manual_pre_confirmed_at= \
        manual_post_confirmed_at= \
        completed_at= \
        report_remote= \
        report_json= \
        report_text= \
        publish_next_status= \
        publish_next_started_at= \
        publish_next_completed_at= \
        publish_next_failed_step= \
        publish_next_history_remote= \
        publish_next_shouko_sha256= \
        publish_next_delta_sha256=

      preflight
      ;;
  esac
}

main "$@"
