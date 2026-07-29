import time
import urllib.request
import os
import json
import zipfile
import subprocess
import re

# ==========================================
# CẤU HÌNH CLOUD
# ==========================================
OWNER = "tinhpr9"
REPO = "Aotscript"
COMMAND_URL = f"https://raw.githubusercontent.com/{OWNER}/{REPO}/main/lenh.txt"
DOWNLOAD_DIR = "/storage/emulated/0/Download/GitHub_All_Files"
CONFIG_PATH = "/storage/emulated/0/Download/Shouko/config.json"

LAST_CMD = ""

# Hàm 1: Tự động quét Github, tải và cài đặt Delta
def auto_update_system():
    print("\n[*] BẮT ĐẦU QUY TRÌNH KÉO FILE VÀ CÀI ĐẶT...")
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)

    api_url = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"
    try:
        req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            assets = data.get("assets", [])

        if not assets:
            return print("[!] Không tìm thấy file nào trên Release GitHub.")

        for idx, asset in enumerate(assets, 1):
            file_name = asset["name"]
            dl_url = asset["browser_download_url"]
            file_path = os.path.join(DOWNLOAD_DIR, file_name)

            print(f" -> Đang tải: {file_name}")
            urllib.request.urlretrieve(dl_url, file_path)

            if file_name.endswith(".zip"):
                extract_path = os.path.join(DOWNLOAD_DIR, f"extracted_{file_name}")
                os.makedirs(extract_path, exist_ok=True)
                try:
                    with zipfile.ZipFile(file_path, 'r') as zip_ref:
                        zip_ref.extractall(extract_path)
                except Exception as e:
                    print(f" [!] Lỗi bung zip: {e}")
                    continue
                    
                for root, dirs, files in os.walk(extract_path):
                    for f in files:
                        if f.endswith(".apk"):
                            apk_path = os.path.join(root, f)
                            print(f"   => Cài đặt đè: {f} ...")
                            subprocess.run(f"su -c 'pm install -r \"{apk_path}\"'", shell=True)

            elif file_name.endswith(".apk"):
                print(f"   => Cài đặt đè: {file_name} ...")
                subprocess.run(f"su -c 'pm install -r \"{file_path}\"'", shell=True)

        print("[+] CẬP NHẬT DELTA HOÀN TẤT CHO THIẾT BỊ NÀY!")
    except Exception as e:
        print(f"[!] Lỗi Update: {e}")

# Hàm 2: Tự động đổi link Solver API (ĐÃ BỎ TỰ ĐỘNG REBOOT)
def update_solver_url(new_url):
    print(f"\n[*] ĐANG TIẾN HÀNH TRÁO LINK SOLVER...")
    if not os.path.exists(CONFIG_PATH):
        return print(f"[!] Lỗi: Không tìm thấy file {CONFIG_PATH} trong máy ảo.")
    
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Tráo link
        updated_content = re.sub(r'"solver_url":\s*"[^"]*"', f'"solver_url": "{new_url}"', content)
        
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write(updated_content)
            
        print(f"[+] Thành công! Đã cấy link solver mới vào config.json")
        print(f"[*] Xong việc. (Tính năng tự động reboot đã được gỡ bỏ theo lệnh)")
        
    except Exception as e:
        print(f"[!] Lỗi khi ghi đè file config: {e}")

# ==========================================
# HỆ THỐNG LẮNG NGHE LỆNH (LOOP)
# ==========================================
print("="*50)
print("  📡 HỆ THỐNG COMMAND & CONTROL ĐÃ KHỞI ĐỘNG  ")
print("="*50)
print("[*] Đang chờ tín hiệu từ GitHub...")

while True:
    try:
        url_no_cache = f"{COMMAND_URL}?t={int(time.time())}"
        req = urllib.request.Request(url_no_cache, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as response:
            current_cmd = response.read().decode('utf-8').strip()
        
        if current_cmd and current_cmd != LAST_CMD:
            LAST_CMD = current_cmd
            
            # --- LỆNH 1: UPDATE DELTA ---
            if current_cmd == "UPDATE_DELTA":
                print("\n[🚀] ĐÃ NHẬN LỆNH: UPDATE_DELTA")
                auto_update_system()
                print("\n[*] Xong việc. Đưa máy về chế độ chờ...\n")
            
            # --- LỆNH 2: ĐỔI LINK SOLVER TÙY CHỈNH ---
            elif current_cmd.startswith("UPDATE_SOLVER"):
                print("\n[🔧] ĐÃ NHẬN LỆNH: UPDATE_SOLVER")
                parts = current_cmd.split(" ", 1)
                if len(parts) > 1:
                    new_link = parts[1].strip()
                    update_solver_url(new_link)
                else:
                    print("[!] Lỗi: Thiếu link đính kèm. Cú pháp chuẩn: UPDATE_SOLVER <link>")
            
            # --- LỆNH 3: KHỞI ĐỘNG LẠI TOÀN BỘ (Thủ công) ---
            elif current_cmd == "REBOOT":
                print("\n[🔥] ĐÃ NHẬN LỆNH: REBOOT")
                print("[*] Đang ép khởi động lại thiết bị...")
                time.sleep(2)
                os.system("su -c 'reboot'")
            
            # --- LỆNH 4: NẰM IM ---
            elif current_cmd == "IDLE":
                print("\n[💤] ĐÃ NHẬN LỆNH: IDLE. Tạm nghỉ chờ việc.")
                
    except Exception as e:
        pass
        
    time.sleep(15)
