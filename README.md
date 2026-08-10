# Aotscript — setup bền vững trong Termux

Điều kiện ban đầu: Termux đã được cài và mở. Bootstrap đã được GPT kiểm tra chỉ cần chạy một lần; nó cài launcher local vào `$PREFIX/bin/aotsetup`.

Sau lần đầu:

- Chạy `aotsetup` để tiếp tục đúng checkpoint, không tải lại `setup.sh`.
- Chạy `aotsetup update` chỉ khi muốn chủ động lấy launcher mới từ nhánh `main`.
- Máy mới hỏi Device ID và nhóm `NOVA`/`MARMOT` một lần. Máy clone tự đối chiếu setup-driver, mprovision và Shouko; state mâu thuẫn hoặc JSON hỏng sẽ dừng trước mutation.
- Migration clone lưu state nguồn cùng manifest SHA-256 trong `$HOME/.local/state/aotscript/foreign-state/`, không replay provision, backup hoặc restore đã hoàn tất.

Whitelist phân loại identity chỉ gồm `$STATE_BASE/setup-driver/device_id`, `$STATE_BASE/setup-driver/device_group`, `$STATE_BASE/mprovision.json`, `Shouko/device_id.txt` và `Shouko/device_group.txt`. `agent_state.json` và provision reports là state phụ thuộc identity nên chỉ được archive/reset trong migration; `agent_config.json`, cookie, Delta, auto-exec, key và dữ liệu ứng dụng không thuộc whitelist và không bị sửa.

Checkpoint giao diện vẫn làm tay qua wizard/notification **MỞ LẠI / ĐÃ XONG**: Play Protect và Google Play; Termux:API/Boot từ F-Droid; Google login khi cần; Swift Backup; Developer Options 700dp/freeform/desktop; keyboard và Delta/Shouko; toolcheck, cookie, auto-exec, key Shouko, WARP và kiểm tra user/account.

Ghi chú `97598239454123, kêu AI chỉnh link` vẫn chỉ là cảnh báo thủ công cho đến khi xác định đúng file/field. Setup không kích hoạt AOT Group Control, không hiển thị checkpoint Root, không xóa dữ liệu ứng dụng và không reset máy.
