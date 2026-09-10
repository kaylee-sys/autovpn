import base64
import concurrent.futures
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import zipfile
from urllib.parse import parse_qs, quote, unquote, urlparse
from urllib.request import Request, build_opener, ProxyHandler, urlopen

try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

XRAY_EXE = "xray.exe"
PROXY_REGEX = re.compile(r'(?:vless|vmess|trojan|ss)://[^\s"\'<>`]+', re.IGNORECASE)

RU_KEYWORDS = [
    "🇷🇺", "RUSSIA", "РОССИЯ", "MOSCOW", "SPB", "MSK", 
    "SELECTEL", "TIMEWEB", "FIRSTVDS", "VDSINA", "BEGET", 
    "ROSTELECOM", "MTS", "BEELINE", "MEGAFON", "YANDEX", "RU"
]

MAX_CONCURRENT_TESTS = 12
port_queue = queue.Queue()
for p in range(10800, 10800 + MAX_CONCURRENT_TESTS):
    port_queue.put(p)

stop_event = threading.Event()
found_working = 0
print_lock = threading.Lock()
file_lock = threading.Lock()

OUTPUT_FILE = "configs.txt"


def ensure_xray_downloaded():
    if os.path.exists(XRAY_EXE):
        return True
    print("[*] Скачивание официального Xray-core...")
    url = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-windows-64.zip"
    try:
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=30) as resp, open("xray_temp.zip", 'wb') as f:
            f.write(resp.read())
        with zipfile.ZipFile("xray_temp.zip", 'r') as z:
            for m in z.namelist():
                if m.endswith("xray.exe"):
                    with z.open(m) as src, open(XRAY_EXE, "wb") as tgt:
                        tgt.write(src.read())
                    break
        os.remove("xray_temp.zip")
        return True
    except Exception as e:
        print(f"[!] Ошибка: {e}")
        return False


def fetch_url(url: str, timeout: float = 8.0) -> str:
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urlopen(req, timeout=timeout) as response:
            return response.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"[!] Не удалось скачать с {url}: {e}")
        return ""


def get_country_name(config_url: str) -> str:
    try:
        parsed = urlparse(config_url)
        host = parsed.hostname
        if config_url.startswith("vmess://"):
            b64 = config_url.replace("vmess://", "")
            b64 += "=" * ((4 - len(b64) % 4) % 4)
            host = json.loads(base64.b64decode(b64).decode('utf-8', errors='ignore')).get("add", host)
            
        if not host: return "Unknown"
        ip = socket.gethostbyname(host)
        req = Request(f"http://ip-api.com/json/{ip}?fields=status,country", headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get("status") == "success":
                return data.get("country", "Unknown")
    except: 
        pass
    return "Unknown"


def format_config_name(config_url: str, country: str) -> str:
    base_url, _, _ = config_url.partition('#')
    return f"{base_url}#{quote(country)}"


def is_obvious_ru_node(config_url: str) -> bool:
    try:
        parsed = urlparse(config_url)
        tag = unquote(parsed.fragment).upper()
        if any(kw in tag for kw in RU_KEYWORDS): return True
        
        host = parsed.hostname or ""
        if config_url.startswith("vmess://"):
            b64 = config_url.replace("vmess://", "")
            b64 += "=" * ((4 - len(b64) % 4) % 4)
            data = json.loads(base64.b64decode(b64).decode('utf-8', errors='ignore'))
            ps = data.get("ps", "").upper()
            host = data.get("add", host)
            if any(kw in ps for kw in RU_KEYWORDS): return True

        if host.lower().endswith(('.ru', '.su', '.xn--p1ai', '.ru.com')):
            return True
    except: pass
    return False


def is_ru_ip(config_url: str) -> bool:
    try:
        parsed = urlparse(config_url)
        host = parsed.hostname
        if config_url.startswith("vmess://"):
            b64 = config_url.replace("vmess://", "")
            b64 += "=" * ((4 - len(b64) % 4) % 4)
            host = json.loads(base64.b64decode(b64).decode('utf-8', errors='ignore')).get("add", host)
            
        if not host: return False
        ip = socket.gethostbyname(host)
        req = Request(f"http://ip-api.com/json/{ip}?fields=countryCode", headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=2.0) as resp:
            if json.loads(resp.read().decode('utf-8')).get("countryCode") == "RU":
                return True
    except: pass
    return False


def load_target_urls() -> list[str]:
    target_links = [
        "https://raw.githack.com/igareck/vpn-configs-for-russia/main/BLACK_VLESS_RUS_mobile.txt",
        "https://translated.turbopages.org/proxy_u/de-de.ru.fcee2a9c-6aa276b0-e132d93a-74722d776562/https/bitbucket.org/igareck/vpn-configs-for-russia/raw/main/WHITE-CIDR-RU-all.txt",
        "https://raw.githubusercontent.com/zieng2/wl/refs/heads/main/vless_universal.txt",
        "https://raw.githubusercontent.com/whoahaow/rjsxrd/refs/heads/main/githubmirror/bypass/bypass-all.txt"
    ]
    texts = []
    print("[*] Скачивание свежих конфигов из интернета...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        for res in executor.map(fetch_url, target_links):
            if res: texts.append(res)
    return texts


def extract_configs_from_text(text: str) -> list[str]:
    extracted = set(PROXY_REGEX.findall(text))
    if len(text.strip()) > 20 and len(text) < 1000000:
        cleaned = text.strip()
        try:
            decoded = base64.b64decode(cleaned + '=' * ((4 - len(cleaned) % 4) % 4)).decode('utf-8', errors='ignore')
            extracted.update(PROXY_REGEX.findall(decoded))
        except: pass
    return list(extracted)


def convert_url_to_xray_config(config_url: str, local_port: int):
    try:
        parsed = urlparse(config_url)
        outbound = {}
        if config_url.startswith("vless://"):
            q = parse_qs(parsed.query)
            outbound = {
                "protocol": "vless",
                "settings": {"vnext": [{"address": parsed.hostname, "port": int(parsed.port or 443), "users": [{"id": parsed.username, "encryption": "none"}]}]},
                "streamSettings": {"network": q.get('type', ['tcp'])[0], "security": q.get('security', ['none'])[0]}
            }
            if outbound["streamSettings"]["security"] == "reality":
                outbound["streamSettings"]["realitySettings"] = {"show": False, "fingerprint": q.get('fp', ['chrome'])[0], "serverName": q.get('sni', [''])[0], "publicKey": q.get('pbk', [''])[0], "shortId": q.get('sid', [''])[0], "spiderX": q.get('spx', ['/'])[0]}
            elif outbound["streamSettings"]["security"] == "tls":
                outbound["streamSettings"]["tlsSettings"] = {"serverName": q.get('sni', [parsed.hostname])[0], "fingerprint": q.get('fp', ['chrome'])[0]}
            if outbound["streamSettings"]["network"] == "ws":
                outbound["streamSettings"]["wsSettings"] = {"path": q.get('path', ['/'])[0], "headers": {"Host": q.get('host', [parsed.hostname])[0]} }

        elif config_url.startswith("vmess://"):
            b64 = config_url.replace("vmess://", "")
            data = json.loads(base64.b64decode(b64 + "=" * ((4 - len(b64) % 4) % 4)).decode('utf-8'))
            outbound = {
                "protocol": "vmess",
                "settings": {"vnext": [{"address": data.get("add"), "port": int(data.get("port", 443)), "users": [{"id": data.get("id"), "alterId": int(data.get("aid", 0)), "security": "auto"}]}]},
                "streamSettings": {"network": data.get("net", "tcp")}
            }
            if data.get("tls") == "tls":
                outbound["streamSettings"]["security"] = "tls"
                outbound["streamSettings"]["tlsSettings"] = {"serverName": data.get("sni") or data.get("host") or data.get("add")}
            if data.get("net") == "ws":
                outbound["streamSettings"]["wsSettings"] = {"path": data.get("path", "/"), "headers": {"Host": data.get("host") or data.get("add")}}
        
        if not outbound: return None
        return {"log": {"loglevel": "none"}, "inbounds": [{"port": local_port, "listen": "127.0.0.1", "protocol": "http"}], "outbounds": [outbound]}
    except: return None


def check_node_worker(cfg: str):
    global found_working
    if stop_event.is_set() or is_obvious_ru_node(cfg):
        return

    port = port_queue.get()
    temp_file = f"temp_cfg_{port}.json"
    proc = None

    try:
        xray_cfg = convert_url_to_xray_config(cfg, port)
        if not xray_cfg: return

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(xray_cfg, f)

        proc = subprocess.Popen([XRAY_EXE, "run", "-c", temp_file], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.4)

        proxy = ProxyHandler({'http': f'http://127.0.0.1:{port}', 'https': f'http://127.0.0.1:{port}'})
        opener = build_opener(proxy)
        
        connected = False
        try:
            with opener.open(Request("http://www.google.com/generate_204", headers={'User-Agent': 'Mozilla/5.0'}), timeout=3.0) as resp:
                if resp.status in (200, 204):
                    with opener.open(Request("http://httpbin.org/bytes/10", headers={'User-Agent': 'Mozilla/5.0'}), timeout=2.5) as sub_resp:
                        if sub_resp.status == 200:
                            connected = True
        except: pass

        if connected and not stop_event.is_set():
            if is_ru_ip(cfg): 
                return 
            
            country = get_country_name(cfg)
            
            with file_lock:
                found_working += 1
                final_cfg = format_config_name(cfg, country)
                
                with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
                    f.write(final_cfg + "\n")
                with print_lock:
                    print(f"[+] [{country}] — Рабочий, сохранен")

    except Exception:
        pass
    finally:
        if proc:
            proc.terminate()
            try: proc.wait(0.5)
            except: proc.kill()
        if os.path.exists(temp_file):
            try: os.remove(temp_file)
            except: pass
        port_queue.put(port)


def push_to_github():
    try:
        if not os.path.exists(".git"):
            print("[!] Папка не является Git-репозиторием. Авто-загрузка на GitHub пропущена.")
            return
        
        print("[*] Отправка изменений на GitHub...")
        subprocess.run(["git", "add", OUTPUT_FILE], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Проверяем, есть ли реально изменения для коммита
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip():
            print("[*] Файл configs.txt не изменился с прошлого раза, пуш пропущен.")
            return
        
        subprocess.run(["git", "commit", "-m", "Auto-update working configs.txt"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "push"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[+] Успешно запушено на GitHub!")
    except Exception as e:
        print(f"[!] Ошибка при отправке в GitHub: {e}")


def main():
    if not ensure_xray_downloaded(): return

    print("==================================================")
    print("      ПРОВЕРКА КОНФИГОВ С АВТО-ПУШЕМ НА GITHUB")
    print("==================================================")
    try:
        interval_min = int(input("Интервал повторного обновления в минутах (0 для запуска 1 раз): ").strip() or "0")
    except ValueError:
        interval_min = 0
    print("==================================================\n")

    while True:
        global found_working
        found_working = 0
        stop_event.clear()

        all_configs = set()

        if os.path.exists(OUTPUT_FILE):
            print(f"[*] Обнаружен существующий файл '{OUTPUT_FILE}'. Перепроверяем старые ноды...")
            try:
                with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                    old_text = f.read()
                    all_configs.update(extract_configs_from_text(old_text))
                print(f"[+] Найдено старых конфигов для перепроверки: {len(all_configs)}")
            except:
                pass

        open(OUTPUT_FILE, "w").close()

        raw_texts = load_target_urls()
        for text in raw_texts:
            all_configs.update(extract_configs_from_text(text))
        
        configs = list(all_configs)
        print(f"[+] Всего уникальных ключей к проверке: {len(configs)}")
        if configs:
            print("[*] Запуск многопоточной проверки...")
            with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_TESTS) as executor:
                for cfg in configs:
                    if stop_event.is_set(): break
                    executor.submit(check_node_worker, cfg)

        print(f"\n[+] Цикл завершен! Всего живых конфигов: {found_working}")
        print(f"[+] Результаты записаны в '{OUTPUT_FILE}'")

        # Выгружаем на GitHub после каждого обновления файла
        push_to_github()

        if interval_min <= 0:
            print("[*] Однократный режим завершен.")
            break

        print(f"\n[*] Ожидание {interval_min} минут до следующего запуска...")
        try:
            time.sleep(interval_min * 60)
        except KeyboardInterrupt:
            print("\n[!] Остановлено пользователем.")
            break


if __name__ == "__main__":
    main()