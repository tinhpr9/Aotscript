# Aotscript — setup một lệnh trong Termux

Điều kiện duy nhất: **Termux đã được cài và mở**. Mỗi máy dán cùng một lệnh dưới đây, sau đó nhập Device ID (ví dụ `m88`) và nhóm `NOVA` hoặc `MARMOT` khi được hỏi. Nếu Termux đóng hoặc máy reboot, chạy lại đúng câu lệnh để tiếp tục từ state đã lưu; provision và backup đã xong sẽ không chạy lại.

```sh
pkg install -y curl && AOT_SETUP_TMP="$(mktemp)" && curl -fsSL --retry 3 --connect-timeout 15 "https://raw.githubusercontent.com/tinhpr9/Aotscript/main/setup.sh?t=$(date +%s)" -o "$AOT_SETUP_TMP" && bash -n "$AOT_SETUP_TMP" && bash "$AOT_SETUP_TMP"; AOT_SETUP_RC=$?; rm -f "$AOT_SETUP_TMP"; [ "$AOT_SETUP_RC" -eq 0 ]
```

Các checkpoint giao diện vẫn bắt buộc làm tay và được wizard/notification nhắc bằng nút **MỞ LẠI / ĐÃ XONG**:

- Tắt Play Protect, cập nhật Google Play; cài Termux:API và Termux:Boot từ F-Droid, rồi mở Termux:Boot một lần.
- Google config/import/login khi quy trình yêu cầu; Swift Backup app kèm label/data và các app còn lại không data.
- Developer Options: app trên bộ nhớ ngoài, resize, 700dp, freeform và desktop mode; cập nhật keyboard, chuẩn bị Delta/Shouko.
- Toolcheck, cookie setup/check/login, auto-exec, key Shouko, 1.1.1.1/WARP, đủ user và không trùng account.
- Ghi chú `97598239454123, kêu AI chỉnh link` vẫn là cảnh báo thủ công vì chưa xác định chính xác file và định dạng cần sửa.

Setup không chạy AOT Group Control, không kiểm tra/hướng dẫn Root, không xóa dữ liệu và không reset máy. `mprovision done pre` tự kiểm tra rclone; `done post` tự chạy UI post, audit, backup cuối và publish nên không cần gọi các bước đó riêng.
