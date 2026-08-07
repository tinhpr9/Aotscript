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
GOOGLE_LOGIN_DROP="${AOTSCRIPT_GOOGLE_LOGIN_DROP:-/storage/emulated/0/Download/Shouko/google_login.json}"
GOOGLE_LOGIN_CONFIG="${AOTSCRIPT_GOOGLE_LOGIN_CONFIG:-$HOME/.config/aotscript/google_login.json}"
GOOGLE_LOGIN_STATUS="${AOTSCRIPT_GOOGLE_LOGIN_STATUS:-$STATE_DIR/google-login-assistant.state}"
GOOGLE_LOGIN_XML="${AOTSCRIPT_GOOGLE_LOGIN_XML:-$STATE_DIR/google-login-ui.xml}"
GOOGLE_LOGIN_TIMEOUT="${AOTSCRIPT_GOOGLE_LOGIN_TIMEOUT:-120}"

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

google_status_set() {
  local value="$1"
  local tmp="$GOOGLE_LOGIN_STATUS.tmp.$$"

  printf '%s\n' "$value" > "$tmp"
  chmod 600 "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$GOOGLE_LOGIN_STATUS"
}

google_status_get() {
  if [ -s "$GOOGLE_LOGIN_STATUS" ]; then
    tr -d '\r\n ' < "$GOOGLE_LOGIN_STATUS"
  else
    printf 'NOT_CONFIGURED\n'
  fi
}

google_config_validate() {
  local path="$1"

  python - "$path" <<'PY_GOOGLE_CONFIG_VALIDATE'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
if not path.is_file():
    raise SystemExit(2)

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(3)

if not isinstance(data, dict):
    raise SystemExit(4)

email = data.get("email")
password = data.get("password")
enabled = data.get("enabled", True)
delete_after_success = data.get("delete_after_success", True)

if not isinstance(email, str) or not email.strip():
    raise SystemExit(5)
if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
    raise SystemExit(6)
if not isinstance(password, str) or not password:
    raise SystemExit(7)
if len(password) > 256:
    raise SystemExit(8)
if not isinstance(enabled, bool):
    raise SystemExit(9)
if not isinstance(delete_after_success, bool):
    raise SystemExit(10)

print("GOOGLE_LOGIN_CONFIG=VALID")
PY_GOOGLE_CONFIG_VALIDATE
}

google_config_read() {
  local key="$1"

  python - "$GOOGLE_LOGIN_CONFIG" "$key" <<'PY_GOOGLE_CONFIG_READ'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
value = data.get(sys.argv[2], "")
if isinstance(value, bool):
    print("true" if value else "false")
elif isinstance(value, str):
    print(value)
else:
    print("")
PY_GOOGLE_CONFIG_READ
}

google_drop_remove() {
  [ -e "$GOOGLE_LOGIN_DROP" ] || return 0

  : > "$GOOGLE_LOGIN_DROP" 2>/dev/null || return 1
  rm -f "$GOOGLE_LOGIN_DROP" 2>/dev/null || return 1
}

google_config_import() {
  local dir tmp

  dir="$(dirname "$GOOGLE_LOGIN_CONFIG")"
  mkdir -p "$dir"
  chmod 700 "$dir" 2>/dev/null || true

  if [ -s "$GOOGLE_LOGIN_DROP" ]; then
    tmp="$GOOGLE_LOGIN_CONFIG.tmp.$$"
    rm -f "$tmp"
    umask 077

    cp "$GOOGLE_LOGIN_DROP" "$tmp" || {
      rm -f "$tmp"
      google_status_set DROP_READ_FAILED
      return 1
    }

    if ! google_config_validate "$tmp" >/dev/null 2>&1; then
      rm -f "$tmp"
      google_status_set CONFIG_INVALID
      return 1
    fi

    chmod 600 "$tmp"
    mv -f "$tmp" "$GOOGLE_LOGIN_CONFIG"

    if ! google_drop_remove; then
      rm -f "$GOOGLE_LOGIN_CONFIG"
      google_status_set DROP_DELETE_FAILED
      return 1
    fi

    google_status_set CONFIG_READY
    return 0
  fi

  if google_config_validate "$GOOGLE_LOGIN_CONFIG" >/dev/null 2>&1; then
    chmod 600 "$GOOGLE_LOGIN_CONFIG" 2>/dev/null || true
    case "$(google_status_get)" in
      SUCCESS|MANUAL_REQUIRED|AUTH_REJECTED|TIMEOUT|UI_*) ;;
      *) google_status_set CONFIG_READY ;;
    esac
    return 0
  fi

  google_status_set CONFIG_MISSING
  return 1
}

google_text_supported() {
  local value="$1"
  [[ "$value" =~ ^[A-Za-z0-9@._+-]+$ ]]
}

google_configure_interactive() {
  local email password dir tmp

  printf 'Email Google: '
  IFS= read -r email
  printf 'Mật khẩu Google: '
  IFS= read -r -s password
  printf '\n'

  if ! google_text_supported "$email" ||
     ! google_text_supported "$password"; then
    unset password
    printf 'GOOGLE_LOGIN_CONFIG=UNSUPPORTED_CHARACTERS\n' >&2
    return 1
  fi

  dir="$(dirname "$GOOGLE_LOGIN_CONFIG")"
  mkdir -p "$dir"
  chmod 700 "$dir" 2>/dev/null || true
  tmp="$GOOGLE_LOGIN_CONFIG.tmp.$$"
  rm -f "$tmp"
  umask 077

  python - "$tmp" 3<<<"$email" 4<<<"$password" <<'PY_GOOGLE_CONFIG_WRITE'
import json
import os
import pathlib
import sys

email = os.fdopen(3, "r", encoding="utf-8").read().rstrip("\n")
password = os.fdopen(4, "r", encoding="utf-8").read().rstrip("\n")
path = pathlib.Path(sys.argv[1])
path.write_text(
    json.dumps(
        {
            "enabled": True,
            "email": email,
            "password": password,
            "delete_after_success": True,
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n",
    encoding="utf-8",
)
PY_GOOGLE_CONFIG_WRITE

  unset password

  google_config_validate "$tmp" >/dev/null || {
    rm -f "$tmp"
    google_status_set CONFIG_INVALID
    return 1
  }

  chmod 600 "$tmp"
  mv -f "$tmp" "$GOOGLE_LOGIN_CONFIG"
  google_status_set CONFIG_READY

  printf 'GOOGLE_LOGIN_CONFIG=READY\n'
  printf 'EMAIL=SET\n'
  printf 'PASSWORD=SET\n'
  printf 'DELETE_AFTER_SUCCESS=YES\n'
}

google_config_status() {
  local status
  status="$(google_status_get)"

  printf 'GOOGLE_LOGIN_ASSISTANT=%s\n' "$status"

  if [ -s "$GOOGLE_LOGIN_DROP" ]; then
    printf 'DROP_FILE=PRESENT\n'
  else
    printf 'DROP_FILE=ABSENT\n'
  fi

  if google_config_validate "$GOOGLE_LOGIN_CONFIG" >/dev/null 2>&1; then
    printf 'PRIVATE_CONFIG=VALID\n'
    printf 'EMAIL=SET\n'
    printf 'PASSWORD=SET\n'
  elif [ -e "$GOOGLE_LOGIN_CONFIG" ]; then
    printf 'PRIVATE_CONFIG=INVALID_OR_SCRUBBED\n'
  else
    printf 'PRIVATE_CONFIG=ABSENT\n'
  fi
}

google_config_clear() {
  rm -f "$GOOGLE_LOGIN_CONFIG"
  google_drop_remove || true
  google_status_set CLEARED
  printf 'GOOGLE_LOGIN_CONFIG=CLEARED\n'
}

google_config_scrub_success() {
  [ -e "$GOOGLE_LOGIN_CONFIG" ] || return 0

  python - "$GOOGLE_LOGIN_CONFIG" <<'PY_GOOGLE_CONFIG_SCRUB'
import json
import os
import pathlib
import sys
import tempfile

path = pathlib.Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    path.unlink(missing_ok=True)
    raise SystemExit(0)

if data.get("delete_after_success", True):
    path.unlink(missing_ok=True)
    raise SystemExit(0)

data.pop("password", None)
data["enabled"] = False
fd, name = tempfile.mkstemp(prefix=path.name + ".tmp.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(name, 0o600)
    os.replace(name, path)
finally:
    if os.path.exists(name):
        os.unlink(name)
PY_GOOGLE_CONFIG_SCRUB
}

google_ui_dump() {
  local remote tmp
  remote="/data/local/tmp/aotscript-google-ui-$$.xml"
  tmp="$GOOGLE_LOGIN_XML.tmp.$$"
  rm -f "$tmp"

  root_ok || return 1

  if ! su -c "uiautomator dump --compressed '$remote' >/dev/null 2>&1"; then
    su -c "uiautomator dump '$remote' >/dev/null 2>&1" || return 1
  fi

  su -c "cat '$remote'" > "$tmp" 2>/dev/null || {
    su -c "rm -f '$remote'" >/dev/null 2>&1 || true
    rm -f "$tmp"
    return 1
  }

  su -c "rm -f '$remote'" >/dev/null 2>&1 || true
  [ -s "$tmp" ] || {
    rm -f "$tmp"
    return 1
  }

  chmod 600 "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$GOOGLE_LOGIN_XML"
}

google_ui_probe() {
  local mode="$1"

  python - "$GOOGLE_LOGIN_XML" "$mode" <<'PY_GOOGLE_UI_PROBE'
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET

path, mode = sys.argv[1], sys.argv[2]
try:
    root = ET.parse(path).getroot()
except (OSError, ET.ParseError):
    raise SystemExit(2)

allowed_packages = {
    "com.google.android.gms",
    "com.google.android.gsf.login",
    "com.google.android.setupwizard",
    "com.android.settings",
    "com.android.chrome",
}

def normalize(value):
    value = (value or "").replace("Đ", "D").replace("đ", "d")
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", value).strip().lower()

def bounds(node):
    match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds", ""))
    if not match:
        return None
    x1, y1, x2, y2 = map(int, match.groups())
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)

def visible(node):
    package = node.get("package", "")
    return (
        package in allowed_packages
        and node.get("enabled", "true") != "false"
        and node.get("visible-to-user", "true") != "false"
        and bounds(node) is not None
    )

def label(node):
    return normalize(" ".join((
        node.get("text", ""),
        node.get("content-desc", ""),
        node.get("resource-id", ""),
    )))

nodes = [node for node in root.iter("node") if visible(node)]
all_text = " ".join(label(node) for node in nodes)

challenge_words = (
    "captcha",
    "verify its you",
    "xac minh danh tinh",
    "xac minh do la ban",
    "2 step verification",
    "xac minh 2 buoc",
    "verification code",
    "ma xac minh",
    "check your phone",
    "kiem tra dien thoai",
    "try another way",
    "thu cach khac",
    "recovery email",
    "email khoi phuc",
    "i agree",
    "toi dong y",
    "terms of service",
    "dieu khoan dich vu",
)
error_words = (
    "wrong password",
    "mat khau khong dung",
    "couldnt sign you in",
    "khong the dang nhap",
    "account not found",
    "khong tim thay tai khoan",
)

if mode in {"challenge", "auth_error"}:
    words = challenge_words if mode == "challenge" else error_words
    found = any(word in all_text for word in words)
    print("PROBE=FOUND" if found else "PROBE=ABSENT")
    raise SystemExit(0)

candidates = []
for node in nodes:
    rid = normalize(node.get("resource-id", ""))
    text = normalize(node.get("text", ""))
    desc = normalize(node.get("content-desc", ""))
    cls = node.get("class", "")
    is_edit = cls.endswith("EditText")
    is_password = node.get("password", "false") == "true" or "password" in rid

    if mode == "email":
        match = is_edit and not is_password and any(
            token in rid for token in ("identifierid", "account_name", "email", "username")
        )
        if not match and is_edit and not is_password:
            match = any(token in all_text for token in (
                "email or phone", "email hoac so dien thoai", "dia chi email"
            ))
    elif mode == "password":
        match = is_edit and is_password
    elif mode == "next":
        exact = {"next", "tiep theo", "continue", "tiep tuc"}
        match = (
            text in exact
            or desc in exact
            or any(token in rid for token in ("identifiernext", "passwordnext"))
            or rid.endswith("/next")
        )
    else:
        raise SystemExit(3)

    if match:
        candidates.append(node)

unique = {}
for node in candidates:
    box = bounds(node)
    unique[box] = node

items = list(unique.items())
print(f"COUNT={len(items)}")
if len(items) != 1:
    print("PROBE=AMBIGUOUS" if items else "PROBE=MISSING")
    raise SystemExit(0)

box, node = items[0]
x1, y1, x2, y2 = box
print("PROBE=OK")
print(f"X={(x1 + x2) // 2}")
print(f"Y={(y1 + y2) // 2}")
print("FILLED=1" if node.get("text", "") else "FILLED=0")
PY_GOOGLE_UI_PROBE
}

google_probe_value() {
  local output="$1" key="$2"
  printf '%s\n' "$output" |
    awk -F= -v wanted="$key" '$1 == wanted {print substr($0, index($0, "=") + 1); exit}'
}

google_probe_flag() {
  local mode="$1" output
  output="$(google_ui_probe "$mode")" || return 1
  [ "$(google_probe_value "$output" PROBE)" = FOUND ]
}

google_probe_field() {
  local mode="$1" output probe count x y filled
  output="$(google_ui_probe "$mode")" || return 1
  probe="$(google_probe_value "$output" PROBE)"
  count="$(google_probe_value "$output" COUNT)"
  x="$(google_probe_value "$output" X)"
  y="$(google_probe_value "$output" Y)"
  filled="$(google_probe_value "$output" FILLED)"

  [ "$probe" = OK ] && [ "$count" = 1 ] || return 1
  [[ "$x" =~ ^[0-9]+$ ]] || return 1
  [[ "$y" =~ ^[0-9]+$ ]] || return 1
  printf '%s %s %s\n' "$x" "$y" "${filled:-0}"
}

google_ui_click() {
  local x="$1" y="$2"
  su -c "input tap '$x' '$y'" >/dev/null 2>&1
}

google_ui_clear_field() {
  su -c 'input keyevent 123; i=0; while [ "$i" -lt 128 ]; do input keyevent 67; i=$((i + 1)); done' \
    >/dev/null 2>&1
}

google_ui_type() {
  local value="$1"
  google_text_supported "$value" || return 2
  su -c "input text '$value'" >/dev/null 2>&1
}

google_fill_field() {
  local mode="$1" value="$2" point x y filled
  point="$(google_probe_field "$mode")" || return 1
  read -r x y filled <<< "$point"

  google_ui_click "$x" "$y" || return 1
  sleep 1

  if [ "$filled" = 1 ]; then
    google_ui_clear_field || return 1
  fi

  google_ui_type "$value"
}

google_click_next() {
  local point x y filled
  google_ui_dump || return 1
  point="$(google_probe_field next)" || return 1
  read -r x y filled <<< "$point"
  google_ui_click "$x" "$y"
}

google_assist_should_run() {
  case "$(google_status_get)" in
    NOT_CONFIGURED|CONFIG_READY|RUNNING|RETRY) return 0 ;;
    *) return 1 ;;
  esac
}

google_login_assist() {
  local enabled email password deadline email_attempts=0 password_attempts=0

  if google_account_present; then
    google_config_scrub_success
    google_status_set SUCCESS
    return 0
  fi

  if ! google_config_import; then
    open_google || true
    return 3
  fi

  enabled="$(google_config_read enabled)"
  email="$(google_config_read email)"
  password="$(google_config_read password)"

  if [ "$enabled" != true ]; then
    unset password
    google_status_set CONFIG_DISABLED
    open_google || true
    return 4
  fi

  if ! google_text_supported "$email" ||
     ! google_text_supported "$password"; then
    unset password
    google_status_set MANUAL_UNSUPPORTED_CHARACTERS
    open_google || true
    return 5
  fi

  google_status_set RUNNING
  open_google || {
    unset password
    google_status_set OPEN_FAILED
    return 6
  }

  deadline=$((SECONDS + GOOGLE_LOGIN_TIMEOUT))

  while [ "$SECONDS" -lt "$deadline" ]; do
    if google_account_present; then
      unset password
      google_config_scrub_success
      google_status_set SUCCESS
      return 0
    fi

    if ! google_ui_dump; then
      sleep 2
      continue
    fi

    if google_probe_flag auth_error; then
      unset password
      google_status_set AUTH_REJECTED
      return 7
    fi

    if google_probe_flag challenge; then
      unset password
      google_status_set MANUAL_REQUIRED
      return 8
    fi

    if google_probe_field password >/dev/null 2>&1; then
      if [ "$password_attempts" -ge 2 ]; then
        unset password
        google_status_set UI_STUCK_PASSWORD
        return 9
      fi

      google_fill_field password "$password" || {
        unset password
        google_status_set UI_PASSWORD_FIELD_FAILED
        return 10
      }
      password_attempts=$((password_attempts + 1))
      sleep 1
      google_click_next || {
        unset password
        google_status_set UI_PASSWORD_NEXT_FAILED
        return 11
      }
      sleep 3
      continue
    fi

    if google_probe_field email >/dev/null 2>&1; then
      if [ "$email_attempts" -ge 2 ]; then
        unset password
        google_status_set UI_STUCK_IDENTIFIER
        return 12
      fi

      google_fill_field email "$email" || {
        unset password
        google_status_set UI_EMAIL_FIELD_FAILED
        return 13
      }
      email_attempts=$((email_attempts + 1))
      sleep 1
      google_click_next || {
        unset password
        google_status_set UI_EMAIL_NEXT_FAILED
        return 14
      }
      sleep 3
      continue
    fi

    sleep 2
  done

  unset password
  google_status_set TIMEOUT
  return 15
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
      case "$(google_status_get)" in
        RUNNING)
          printf 'google|Đang tự đăng nhập Google. Hệ thống sẽ tự chuyển bước.|waiting\n'
          ;;
        MANUAL_REQUIRED)
          printf 'google|Google yêu cầu xác minh, CAPTCHA hoặc điều khoản. Hãy xử lý trực tiếp.|waiting\n'
          ;;
        AUTH_REJECTED)
          printf 'google|Google từ chối thông tin đăng nhập. Cập nhật cấu hình rồi mở lại.|waiting\n'
          ;;
        MANUAL_UNSUPPORTED_CHARACTERS|UI_*|TIMEOUT|OPEN_FAILED)
          printf 'google|Tự đăng nhập đã dừng an toàn. Hoàn tất thủ công hoặc cập nhật cấu hình.|waiting\n'
          ;;
        CONFIG_READY)
          printf 'google|Đã có cấu hình đăng nhập riêng. Đang chờ chạy trợ lý.|waiting\n'
          ;;
        SUCCESS)
          printf 'google|Đã phát hiện tài khoản Google. Đang chuyển sang Swift Backup.|waiting\n'
          ;;
        *)
          printf 'google|Đăng nhập Google thủ công hoặc chạy aotscript-wizard google-config.|waiting\n'
          ;;
      esac
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
      if google_assist_should_run; then
        google_login_assist || true
      else
        open_google || return 1
      fi
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
        google_config_scrub_success
        google_status_set SUCCESS
        run_mprovision wizard
      elif google_assist_should_run; then
        google_login_assist || true
        if google_account_present; then
          run_mprovision wizard
        fi
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
        elif [ "$(google_status_get)" = CONFIG_READY ]; then
          google_login_assist || true
          if google_account_present; then
            advance_safe || true
          else
            show_current || true
          fi
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

  cat > "$fake_state/google-login.json" <<'JSON'
{
  "enabled": true,
  "email": "fixture@example.com",
  "password": "Fixture123",
  "delete_after_success": true
}
JSON
  chmod 600 "$fake_state/google-login.json"

  output="$(
    PATH="$fake_bin:$PATH" \
    HOME="$fake_home" \
    PREFIX="$fake_prefix" \
    AOTSCRIPT_STATE_DIR="$fake_state" \
    AOTSCRIPT_STATE_FILE="$fake_state/mprovision.json" \
    AOTSCRIPT_GOOGLE_LOGIN_CONFIG="$fake_state/google-login.json" \
    AOTSCRIPT_GOOGLE_LOGIN_DROP="$fake_state/drop.json" \
    bash "$0" google-config-status
  )"

  printf '%s\n' "$output" | grep -Fxq 'PRIVATE_CONFIG=VALID'
  printf '%s\n' "$output" | grep -Fxq 'EMAIL=SET'
  printf '%s\n' "$output" | grep -Fxq 'PASSWORD=SET'

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
    google-config)
      [ "$#" = 1 ] || exit 2
      google_configure_interactive
      ;;
    google-config-import)
      [ "$#" = 1 ] || exit 2
      google_config_import
      google_config_status
      ;;
    google-config-status)
      [ "$#" = 1 ] || exit 2
      google_config_status
      ;;
    google-config-clear)
      [ "$#" = 1 ] || exit 2
      google_config_clear
      ;;
    google-login)
      [ "$#" = 1 ] || exit 2
      google_login_assist || true
      if google_account_present; then
        run_mprovision wizard || true
      fi
      show_current
      ;;
    self-test)
      [ "$#" = 1 ] || exit 2
      self_test
      ;;
    *)
      printf 'Cách dùng: aotscript-wizard start|done|open|stop|status|google-config|google-config-import|google-config-status|google-config-clear|google-login\n' >&2
      exit 2
      ;;
  esac
}

main "$@"
