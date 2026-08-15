# Aotscript — Project Map

## AI / Maintainer: START HERE
Quy tắc:
1. Khi bắt đầu task phải đọc README.md + AGENTS.md + main mới nhất.
2. Xác định task thuộc đúng PROJECT bên dưới trước khi đọc code.
3. Chỉ đọc sâu thư mục/file liên quan; không audit cả repo vô ích.
4. Trước khi tiếp tục checkpoint phải kiểm PR/commit mới nhất trên GitHub.
5. Không trộn task giữa các project.
6. Mỗi PR chỉ nên phục vụ một project/milestone.
7. Sau milestone lớn/merge quan trọng phải cập nhật checkpoint trong README nếu trạng thái đã thay đổi.

## PROJECT 1 — AOT OmniControl
Mục tiêu:
- hệ thống điều khiển tập trung nhiều UGPhone
- AOT Hub/WebApp
- Telegram
- Discord
- Cloudflare Worker/FleetState
- device fleet worker
- batch/ACK/dedupe/targeting
- Canary/Stable update + rollback

Các path chính:
- `cloudflare-worker/`
- `aot-group-control/`
- `tests/` (selftests tương ứng: `test_fleet_batch_architecture.py`, `test_agent.py`, vv.)
- `AGENTS.md` (Release & immutable rules)

Kiến trúc:
Telegram / Discord / AOT Hub -> Cloudflare Worker -> FleetState -> selected ONLINE devices -> action -> ACK/result

Cloudflare Worker/FleetState là source of truth.
Không dựng lại Python omnicontrol/CLI/server trung gian.

Checkpoint hiện tại:
- PR #45 đã merge: remove dead Python omnicontrol package; /batch và /update chạy native qua Worker.
- Milestone core tiếp theo: Discord frontend/control surface, reuse cùng core với Telegram.
- Không được nhầm Batch PHÂN SERVER là milestone Discord/core.

## PROJECT 2 — Batch PHÂN SERVER
Mục tiêu:
- lấy nguồn server từ `tong_hop_link.txt`
- phân private server UNIQUE cho nhiều device × 1..10 tab
- ghi xuống: `/storage/emulated/0/Download/Shouko/server_links.txt`
- mapping package hi..hr
- không trùng server
- fail-closed nếu thiếu/sai
- đây là task riêng, không trộn với Discord/OmniControl core.

Các path chính:
- `tong_hop_link.txt`

## PROJECT 3 — Device Setup / Provision / Clone Migration
Mục tiêu:
- setup.sh / provision-device.sh / msetup
- Termuxboot
- aotsetup checkpoint
- clone-safe migration
- Shouko identity/state

Các path chính:
- `setup.sh`
- `setup-m166.sh`
- `provision-device.sh`
- `wizard-supervisor.sh`
- `Termuxboot`
- `tests/setup-self-test.sh`
- `tests/test_aot_msetup_integration.py`
- `tests/test_aot_launcher.py`

### Setup hiện tại
Điều kiện ban đầu: Termux đã được cài và mở. Bootstrap đã được GPT kiểm tra chỉ cần chạy một lần; nó cài launcher local vào `$PREFIX/bin/aotsetup`.

Sau lần đầu:
- Chạy `aotsetup` để tiếp tục đúng checkpoint, không tải lại `setup.sh`.
- Chạy `aotsetup update` chỉ khi muốn chủ động lấy launcher mới từ nhánh `main`.
- Máy mới hỏi Device ID và nhóm `NOVA`/`MARMOT` một lần. Máy clone tự đối chiếu setup-driver, mprovision và Shouko; state mâu thuẫn hoặc JSON hỏng sẽ dừng trước mutation.
- Migration clone lưu state nguồn cùng manifest SHA-256 trong `$HOME/.local/state/aotscript/foreign-state/`, không replay provision, backup hoặc restore đã hoàn tất.

Whitelist phân loại identity chỉ gồm `$STATE_BASE/setup-driver/device_id`, `$STATE_BASE/setup-driver/device_group`, `$STATE_BASE/mprovision.json`, `Shouko/device_id.txt` và `Shouko/device_group.txt`. `agent_state.json` và provision reports là state phụ thuộc identity nên chỉ được archive/reset trong migration; `agent_config.json`, cookie, Delta, auto-exec, key và dữ liệu ứng dụng không thuộc whitelist và không bị sửa.

Checkpoint giao diện vẫn làm tay qua wizard/notification **MỞ LẠI / ĐÃ XONG**: Play Protect và Google Play; Termux:API/Boot từ F-Droid; Google login khi cần; Swift Backup; Developer Options 700dp/freeform/desktop; keyboard và Delta/Shouko; toolcheck, cookie, auto-exec, key Shouko, WARP và kiểm tra user/account.

Ghi chú `97598239454123, kêu AI chỉnh link` vẫn chỉ là cảnh báo thủ công cho đến khi xác định đúng file/field. Setup không kích hoạt AOT Group Control, không hiển thị checkpoint Root, không xóa dữ liệu ứng dụng và không reset máy.

## PROJECT 4 — Swift Backup / Backup Restore Automation
Mục tiêu:
- OPEN_SWIFT_BACKUP
- OPEN_SWIFT_APPS
- BACKUP_RESTORE_DATA
- semantic state machine
- exactly-once / ACK / fail-closed

Các path chính:
- `aot-group-control/controller.py`
- `aot-group-control/relay.py`
- `tests/test_backup_restore_data.py`

Ghi chú: PR #42 nếu vẫn còn open thì là nhánh cũ cần kiểm trạng thái trước khi tiếp tục, không mặc định dùng. (PR #42 hiện tại đang open từ fix/restore-data-ui).

## PROJECT 5 — Worker Release / CI
Mục tiêu:
- immutable GitHub Releases
- WORKER_VERSION
- manifests
- release-worker workflow
- Canary/Stable
- staged activation / health ACK / rollback

Các path chính:
- `.github/workflows/release-worker.yml`
- `scripts/build-worker-release.py`
- `scripts/verify_worker_release.py`
- `aot-group-control/worker-manifest-canary.json`
- `aot-group-control/worker-manifest-stable.json`
- Các file `aot-*.zip` ở root dir

**Chỉ dẫn:** Người đọc BẮT BUỘC phải xem `AGENTS.md` trước khi sửa worker behavior.

## PROJECT 6 — Solver / Script / Delta / misc device commands
Mục tiêu: Các script lệnh tĩnh, tool chạy độc lập trên device
Các path chính:
- `agent`
- `aot`
- `Marmotgag2`, `Marmotgag2event`, `Novagag2`
- `Toolcheck`, `Track`, `Updatedelta`
- `lenh.txt`, `lenh_all.txt`, `lenh_marmot.txt`, `lenh_nova.txt`

## PROJECT 7 — Rejoin Tool
Mục tiêu: Daemon & logic tự động rejoin game
Các path chính:
- `rejoin-tool/rejoin`
- `rejoin-tool/rejoin_cli.py`
- `rejoin-tool/rejoin_core.py`
- `rejoin-tool/rejoin_daemon.py`
- `rejoin-tool/tests/`

(Nếu auto-rejoin cần tham khảo source cũ thì xem `agent` tại root repo).

## CURRENT CHECKPOINTS
| Project | Last known milestone | Status | Next step | Relevant PR |
| --- | --- | --- | --- | --- |
| PROJECT 1 (OmniControl) | Remove dead Python omnicontrol | Merged | Discord frontend/control surface (reuse core) | #45 |
| PROJECT 2 (Phân Server) | Allocate Server feature | N/A | Implement unique server allocation | N/A |
| PROJECT 4 (Swift Backup) | BACKUP_RESTORE_DATA state machine | Open | Review & Merge PR #42 | #42 |

## FILE OWNERSHIP / ROUTING
| Path | Project | Purpose | Read when... |
| --- | --- | --- | --- |
| `cloudflare-worker/` | Project 1 | AOT Hub, Telegram, state management | Modifying server, worker, or Telegram bot logic |
| `aot-group-control/` | Project 1, 4, 5 | Python client for Worker | Modifying device worker actions, relay, backup/restore |
| `tong_hop_link.txt` | Project 2 | Server source links | Working on Batch Phân Server |
| `setup.sh`, `provision-device.sh`, vv | Project 3 | Device Setup, migration | Updating setup checkpoint, Termux bootstrap |
| `aot-group-control/controller.py` | Project 4 | Swift Backup automation | Fixing backup/restore UI automation steps |
| `.github/workflows/`, `scripts/` | Project 5 | Release packaging | Troubleshooting worker release CI/CD |
| `AGENTS.md` | Project 1, 5 | Release / Safety Rules | ALWAYS read before touching `aot-group-control` or `cloudflare-worker` |
| `agent`, `Marmotgag2`, `Updatedelta` | Project 6 | Misc device scripts, solver | Updating solvers or Delta tools |
| `rejoin-tool/` | Project 7 | Auto Rejoin logic | Modifying rejoin behavior |

## IMPORTANT CROSS-PROJECT RULES
- Không hard-code device ID/group/session/secret.
- Không trộn project trong cùng PR.
- Main có thể thay đổi, README chỉ là map; checkpoint vẫn phải verify với GitHub trước khi hành động.
- AGENTS.md có quyền ưu tiên đối với worker/release policy.
- Nếu README và code/main lệch nhau, main + AGENTS.md là nguồn kiểm chứng, sau đó sửa README.
