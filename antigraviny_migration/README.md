# Antigraviny / Agy Migration System

Hệ thống sao lưu, khởi tạo, phục hồi và kiểm định tự động (Backup + Bootstrap + Restore + Verify) để chuyển đổi Antigraviny/Agy CLI, môi trường ECC, toàn bộ cấu hình, launcher và runtime từ thiết bị Android/Termux cũ sang thiết bị mới.

---

## Mục tiêu kiến trúc

1. **Giữ nguyên tối đa năng lực:** Giữ trọn vẹn binary `agy`, launcher `agyn`/`agy-watch`/`toolcheck`, toàn bộ `.agents` (rules, skills, workflows, agents), custom skills (`aot-accsync`, `antigravity-lessons`), Serena MCP tools/config, tmux config, shell hooks.
2. **An toàn bảo mật (Zero-Secret Leak):** Tuyệt đối không commit token/secret vào Git. Mọi credential portable (như `antigravity-oauth-token`, `rclone.conf`, `gh/hosts.yml`, SSH keys) chỉ nằm trong local migration bundle và được mask (`***REDACTED***`) trong logs và manifest.
3. **Phân loại Credential chính xác:** Credential device-bound (Android KeyStore, hardware bound) được phân loại `DEVICE_BOUND_REAUTH_REQUIRED` và hướng dẫn re-auth thay vì báo PASS giả.
4. **Ngăn chặn Partial/Corrupt Restore:** Mã hóa SHA-256 từng file trong `MANIFEST.json`. Kiểm tra toàn vẹn trước khi ghi đĩa.
5. **Rollback tự động:** Ghi nhận rollback journal trước khi thay đổi. Tự động rollback về nguyên trạng nếu có lỗi trong quá trình phục hồi.
6. **Kiểm định 17 tiêu chí (Live Verification):** Đối chiếu thời gian thực giữa snapshot máy cũ và hệ thống máy mới.

---

## 4 Entrypoints chính

### 1. `agy-backup` (Chạy trên máy cũ)
Tạo bundle nén `.tar.gz` chứa binary, cấu hình, skills, launchers và `MANIFEST.json`.

```bash
# Tạo backup đầy đủ (bao gồm binary agy)
./antigraviny_migration/agy-backup -o agy-migration-bundle.tar.gz

# Tạo backup không kèm binary lớn (để truyền nhanh qua mạng)
./antigraviny_migration/agy-backup -o agy-config-bundle.tar.gz --no-binary
```

### 2. `agy-bootstrap` (Chạy trên máy mới)
Dựng môi trường Termux host, proot Debian 12, cài đặt dependencies (`tmux`, `git`, `python3`, `nodejs`, `rclone`, `ripgrep`, `jq`, `serena-agent`), clone repository Aotscript.

```bash
# Khởi tạo môi trường tự động
./antigraviny_migration/agy-bootstrap

# Chế độ dry-run kiểm tra trước
./antigraviny_migration/agy-bootstrap --dry-run
```

### 3. `agy-restore <bundle>` (Chạy trên máy mới)
Xác minh chữ ký SHA-256 toàn bộ bundle, giải nén và phục hồi chính xác vị trí file, giữ nguyên quyền thực thi (`chmod 0755`), thiết lập rollback bảo vệ.

```bash
# Phục hồi hệ thống
./antigraviny_migration/agy-restore agy-migration-bundle.tar.gz

# Kiểm tra tính hợp lệ của bundle (dry-run)
./antigraviny_migration/agy-restore agy-migration-bundle.tar.gz --dry-run
```

### 4. `agy-verify` (Chạy kiểm định sau khi restore)
Kiểm tra 17 điểm quan trọng thực tế trên máy hiện tại:
1. `AGY_BINARY`: Binary tồn tại và có quyền thực thi.
2. `AGY_VERSION`: Phiên bản `1.1.13`.
3. `AGY_HASH_OR_EXPECTED_BINARY`: SHA-256 khớp với binary máy cũ.
4. `REPO_REMOTE`: Remote trỏ về đúng GitHub repo.
5. `REPO_HEAD`: HEAD commit SHA khớp.
6. `ECC`: Thư mục `.agents/` hoàn chỉnh (rules, skills, workflows, agents).
7. `AGENTS_RULES`: `AGENTS.md` tồn tại và hợp lệ.
8. `CONFIG`: `~/.gemini/config/config.json`, `mcp_config.json`, `settings.json` đầy đủ.
9. `HOOKS`: Serena tool schemas và configs đầy đủ.
10. `RUNTIME`: Python 3.11+, Node.js, uv hoạt động.
11. `PROOT_OR_NATIVE_MODE`: Chạy đúng trong proot Debian 12, không bị lỗi ARM64 Bionic TLS, không dùng QEMU, không lặp nested proot.
12. `LAUNCHER`: `agyn` launcher khởi động đúng và trỏ `/root/.local/bin/agy --dangerously-skip-permissions`.
13. `TMUX_INTEGRATION`: `~/.tmux.conf` (`set -g mouse off`), tmux session quản lý tốt.
14. `DEPENDENCIES`: Các công cụ uv (`serena-agent`, `specify-cli`, `aider-chat`) sẵn sàng.
15. `PERMISSIONS`: Permissions file binary 0755, config 0644, secrets 0600.
16. `AGY_START`: Lệnh `agy --version` chạy thành công không crash.
17. `SMOKE_TEST`: End-to-end smoke test PASS.

```bash
# Kiểm tra hệ thống hiện tại đối chiếu với bundle
./antigraviny_migration/agy-verify --bundle agy-migration-bundle.tar.gz

# Kiểm tra hiện trạng máy
./antigraviny_migration/agy-verify
```

---

## Hướng dẫn di chuyển từng bước (Step-by-Step Migration Guide)

### Bước 1: Tạo bundle trên máy cũ
```bash
cd ~/Aotscript-ecc-production
./antigraviny_migration/agy-backup -o ~/agy-migration-bundle.tar.gz
```

### Bước 2: Chuyển bundle sang máy mới
Chuyển file `agy-migration-bundle.tar.gz` qua Google Drive (rclone), SSH (scp), hoặc cáp/USB sang máy mới (ví dụ lưu tại `~/agy-migration-bundle.tar.gz`).

### Bước 3: Khởi tạo và phục hồi trên máy mới
```bash
# 1. Cài git trong Termux máy mới
pkg install -y git python

# 2. Clone repo Aotscript
git clone https://github.com/tinhpr9/Aotscript.git ~/Aotscript-ecc-production
cd ~/Aotscript-ecc-production

# 3. Chạy bootstrap
./antigraviny_migration/agy-bootstrap

# 4. Chạy restore
./antigraviny_migration/agy-restore ~/agy-migration-bundle.tar.gz

# 5. Kiểm tra nghiệm thu 17 tiêu chí
./antigraviny_migration/agy-verify --bundle ~/agy-migration-bundle.tar.gz
```

---

## Chạy Selftest
```bash
python3 antigraviny_migration/selftest.py
```
Kết quả mong đợi:
```
ANTIGRAVINY_MIGRATION_SELFTEST=OK
```
