#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, time, re, json, base64, sys, socket, select, threading, urllib.request
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ===================== 설정 =====================
WAIT = 20
ARTIFACT_DIR = Path("artifacts")
ARTIFACT_DIR.mkdir(exist_ok=True)

# 환경변수(Secrets)가 있으면 우선 사용, 없으면 아래 값 사용
ECOMM_ID = (os.environ.get("ECOMM_ID") or "여기에_아이디").strip()
ECOMM_PW = (os.environ.get("ECOMM_PW") or "여기에_비밀번호").strip()

# iProyal 프록시 (Secrets 권장: 유료 서비스라 노출되면 요금이 나갈 수 있음)
#   PROXY_ADDR : geo.iproyal.com:12321
#   PROXY_AUTH : 아이디:비밀번호   (대시보드 proxy_auth 값 그대로)
PROXY_ADDR = (os.environ.get("PROXY_ADDR") or "").strip()
PROXY_AUTH = (os.environ.get("PROXY_AUTH") or "").strip()

# 프록시 요금 절약: 크롤링에 필요 없는 접속은 중계에서 차단 (프록시를 아예 안 거침)
BLOCKED_HOSTS = (
    "optimizationguide-pa.googleapis.com",  # Chrome 내부 모델 다운로드 (트래픽의 90%)
    "googletagmanager.com",
    "google-analytics.com",
    "doubleclick.net",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "gstatic.com",
    "update.googleapis.com",
    "clients2.google.com",
    "safebrowsing.googleapis.com",
    "content-autofill.googleapis.com",
)

SCHEDULE_URL = "https://live.ecomm-data.com/schedule/hs"
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1Aqwj1SkHsgAr08doidy_tfnHJNwgdFfNrMoMteoXMdE/edit"
WORKSHEET_NAME = "편성표RAW"


# ---- RELAY START
class AuthProxyRelay:
    """로컬(127.0.0.1)에서 '인증 없는 프록시'처럼 동작하고,
    업스트림(유료 프록시)으로 보낼 때 아이디/비밀번호를 붙여 전달한다.
    Chrome 은 인증 프록시를 직접 못 쓰기 때문에 필요."""

    def __init__(self, upstream_addr, auth, blocked_hosts=()):
        self.blocked_hosts = tuple(h.lower() for h in blocked_hosts)
        self.blocked_count = 0
        host, port = upstream_addr.rsplit(":", 1)
        self.up_host, self.up_port = host.strip(), int(port)
        user, pw = auth.split(":", 1)
        token = base64.b64encode(f"{user}:{pw}".encode()).decode()
        self.auth_header = f"Proxy-Authorization: Basic {token}\r\n".encode()
        self.stats = {}
        self.lock = threading.Lock()
        self.server = None
        self.port = None

    def start(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(128)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self.port

    def stop(self):
        try:
            self.server.close()
        except Exception:
            pass

    def _accept_loop(self):
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    @staticmethod
    def _read_head(sock):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(65536)
            if not chunk:
                return None, b""
            data += chunk
            if len(data) > 1_000_000:
                return None, b""
        head, _, rest = data.partition(b"\r\n\r\n")
        return head, rest

    def _is_blocked(self, host):
        host = (host or "").lower()
        return any(host == b or host.endswith("." + b) for b in self.blocked_hosts)

    def _add(self, label, n):
        with self.lock:
            self.stats[label] = self.stats.get(label, 0) + n

    def _handle(self, client):
        upstream = None
        try:
            client.settimeout(60)
            head, rest = self._read_head(client)
            if head is None:
                return
            lines = head.split(b"\r\n")
            method, target, _ver = lines[0].decode("latin-1").split(" ", 2)
            is_connect = method.upper() == "CONNECT"
            label = target.rsplit(":", 1)[0] if is_connect else (urlparse(target).hostname or "http")

            # 차단 목록에 있으면 프록시로 보내지 않고 바로 거절 (요금 0)
            if self._is_blocked(label):
                with self.lock:
                    self.blocked_count += 1
                client.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                return

            upstream = socket.create_connection((self.up_host, self.up_port), timeout=30)

            if is_connect:
                upstream.sendall(
                    f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n".encode()
                    + self.auth_header + b"Proxy-Connection: keep-alive\r\n\r\n"
                )
                uhead, urest = self._read_head(upstream)
                if uhead is None:
                    client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    return
                status = uhead.split(b"\r\n")[0].decode("latin-1")
                if " 200" not in status:
                    print(f"[RELAY] 업스트림 응답 이상: {status} (target={target})")
                    client.sendall(uhead + b"\r\n\r\n")
                    return
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if urest:
                    client.sendall(urest)
                if rest:
                    upstream.sendall(rest)
            else:
                new_head = lines[0] + b"\r\n" + self.auth_header + b"\r\n".join(lines[1:]) + b"\r\n\r\n"
                upstream.sendall(new_head + rest)

            self._pipe(client, upstream, label)
        except Exception:
            pass
        finally:
            for s in (client, upstream):
                try:
                    if s:
                        s.close()
                except Exception:
                    pass

    def _pipe(self, client, upstream, label):
        client.settimeout(None)
        upstream.settimeout(None)
        socks = [client, upstream]
        while True:
            r, _, x = select.select(socks, [], socks, 120)
            if x or not r:
                return
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (upstream if s is client else client).sendall(data)
                self._add(label, len(data))

    def report(self):
        with self.lock:
            total = sum(self.stats.values())
            top = sorted(self.stats.items(), key=lambda kv: kv[1], reverse=True)[:5]
        print(f"[PROXY] 총 트래픽: {total / 1024 / 1024:.2f} MB (차단한 불필요 접속 {self.blocked_count}건)")
        for host, n in top:
            print(f"[PROXY]   {host}: {n / 1024 / 1024:.2f} MB")
# ---- RELAY END


def check_exit_ip(port):
    """프록시를 통해 나가는 IP 를 확인 (실패하면 브라우저를 띄우기 전에 중단)"""
    proxy = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    try:
        with opener.open("https://ipv4.icanhazip.com", timeout=30) as r:
            return r.read().decode().strip()
    except Exception as e:
        raise RuntimeError(f"프록시 연결 확인 실패: {e} (PROXY_ADDR/PROXY_AUTH 값, 요금/잔여 트래픽 확인)")


def setup_proxy():
    if not (PROXY_ADDR and PROXY_AUTH):
        print("[PROXY] 설정 없음 → 프록시 없이 직접 접속")
        return None
    relay = AuthProxyRelay(PROXY_ADDR, PROXY_AUTH, blocked_hosts=BLOCKED_HOSTS)
    port = relay.start()
    ip = check_exit_ip(port)
    print(f"[PROXY] 중계 시작(127.0.0.1:{port}) / 나가는 IP: {ip}")
    return relay


# ===================== 브라우저 / 크롤링 =====================
def make_driver(proxy_port=None):
    opts = webdriver.ChromeOptions()

    if os.environ.get("HEADLESS", "1") == "1":
        opts.add_argument("--headless=new")
    for arg in ("--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--window-size=1920,1080", "--lang=ko-KR"):
        opts.add_argument(arg)

    # 프록시 요금 절약: Chrome 이 몰래 받는 내부 파일/업데이트/통계 끄기
    for arg in ("--disable-background-networking", "--disable-component-update",
                "--disable-sync", "--no-first-run", "--disable-default-apps",
                "--metrics-recording-only", "--disable-client-side-phishing-detection",
                "--safebrowsing-disable-auto-update", "--disable-breakpad",
                "--disable-features=OptimizationHints,OptimizationGuideModelDownloading,"
                "OptimizationHintsFetching,OptimizationTargetPrediction,Translate,MediaRouter"):
        opts.add_argument(arg)

    # 느린 회선에서도 버티도록: 이미지 등 전부 기다리지 않음
    opts.page_load_strategy = "eager"

    # 프록시 트래픽(GB 과금) 절약: 이미지 차단
    if os.environ.get("BLOCK_IMAGES", "1") == "1":
        opts.add_experimental_option("prefs", {"profile.managed_default_content_settings.images": 2})

    if proxy_port:
        opts.add_argument(f"--proxy-server=http://127.0.0.1:{proxy_port}")

    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin and Path(chrome_bin).exists():
        opts.binary_location = chrome_bin

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(90)
    return driver


def dump_state(driver):
    try:
        print("[DEBUG] URL  :", driver.current_url)
        print("[DEBUG] TITLE:", driver.title)
        print("[DEBUG] BODY :", repr(driver.find_element(By.TAG_NAME, "body").text[:300]))
        driver.save_screenshot(str(ARTIFACT_DIR / "error_screenshot.png"))
    except Exception as e:
        print(f"⚠️ 상태 저장 실패: {e}")


def login_and_handle_session(driver):
    driver.get("https://live.ecomm-data.com")
    print("[STEP] 메인 페이지 진입 완료")

    login_link = WebDriverWait(driver, WAIT).until(
        EC.element_to_be_clickable((By.LINK_TEXT, "로그인"))
    )
    login_link.click()

    t0 = time.time()
    while "/user/sign_in" not in driver.current_url:
        if time.time() - t0 > WAIT:
            raise Exception("로그인 페이지 진입 실패 (타임아웃)")
        time.sleep(0.5)

    time.sleep(4)
    email_input = [e for e in driver.find_elements(By.CSS_SELECTOR, "input[name='email']") if e.is_displayed()][0]
    pw_input = [e for e in driver.find_elements(By.CSS_SELECTOR, "input[name='password']") if e.is_displayed()][0]

    email_input.click()
    time.sleep(0.5)
    email_input.clear()
    email_input.send_keys(ECOMM_ID)
    time.sleep(0.5)

    pw_input.click()
    time.sleep(0.5)
    pw_input.clear()
    pw_input.send_keys(ECOMM_PW)
    time.sleep(0.5)

    login_btn = driver.find_element(By.XPATH, "//button[contains(text(), '로그인')]")
    login_btn.click()
    print("✅ 로그인 버튼 클릭 완료")

    try:
        WebDriverWait(driver, 15).until(lambda d: "/user/sign_in" not in d.current_url)
        print(f"✅ 로그인 성공! URL: {driver.current_url}")
    except Exception:
        raise Exception(f"❌ 로그인 실패! 현재 URL: {driver.current_url}")

    time.sleep(3)

    try:
        session_items = [li for li in driver.find_elements(By.CSS_SELECTOR, "ul > li") if li.is_displayed()]
        if session_items:
            session_items[-1].click()
            time.sleep(1)
            close_btn = driver.find_element(By.XPATH, "//button[text()='종료 후 접속']")
            if close_btn.is_enabled():
                close_btn.click()
                time.sleep(2)
    except Exception:
        pass

    time.sleep(3)


def crawl_schedule(driver):
    driver.get(SCHEDULE_URL)
    print("✅ 편성표 홈쇼핑 페이지 이동 완료")
    time.sleep(3)

    KST = timezone(timedelta(hours=9))
    yesterday = datetime.now(KST).date() - timedelta(days=1)
    date_text = str(yesterday.day)

    date_button_xpath = f"//div[text()='{date_text}']"
    date_button = WebDriverWait(driver, WAIT).until(
        EC.element_to_be_clickable((By.XPATH, date_button_xpath))
    )
    driver.execute_script("arguments[0].click();", date_button)
    time.sleep(3)

    tables = WebDriverWait(driver, WAIT).until(
        EC.presence_of_all_elements_located((By.TAG_NAME, "table"))
    )

    all_data = []
    columns = ['방송시간', '방송정보', '분류', '판매량', '매출액', '상품수']

    for table in tables:
        try:
            tbody = table.find_element(By.TAG_NAME, "tbody")
            rows = tbody.find_elements(By.TAG_NAME, "tr")
            for row in rows:
                cols = row.find_elements(By.TAG_NAME, "td")
                if len(cols) >= 7:
                    try:
                        spans = cols[1].find_elements(By.TAG_NAME, "span")
                        broadcast_time = f"{spans[0].text.strip()}\n{spans[1].text.strip()}" if len(spans) == 2 else \
                            cols[1].text.strip()
                    except Exception:
                        broadcast_time = cols[1].text.strip()

                    all_data.append({
                        "방송시간": broadcast_time, "방송정보": cols[2].text.strip(), "분류": cols[3].text.strip(),
                        "판매량": cols[4].text.strip(), "매출액": cols[5].text.strip(), "상품수": cols[6].text.strip()
                    })
        except Exception:
            continue

    df = pd.DataFrame(all_data, columns=columns)
    print(f"총 {len(df)}개 편성표 정보 추출 완료")
    return df


def execute_logout(driver):
    print("🚪 로그아웃 절차 시작")
    try:
        user_menu = WebDriverWait(driver, WAIT).until(
            EC.element_to_be_clickable((By.XPATH, "//b[contains(text(), '쇼핑엔티')]"))
        )
        driver.execute_script("arguments[0].click();", user_menu)
        time.sleep(1)

        logout_btn = WebDriverWait(driver, WAIT).until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), '로그아웃')]"))
        )
        driver.execute_script("arguments[0].click();", logout_btn)
        time.sleep(2)
        print("✅ 정상적으로 로그아웃 되었습니다.")
    except Exception as e:
        print(f"⚠️ 로그아웃 과정에서 문제가 발생했습니다: {type(e).__name__}")


# ===================== Google Sheets 및 전처리 =====================
def gs_client_from_env():
    GSVC_JSON_B64 = os.environ.get("KEY1", "")
    svc_info = json.loads(base64.b64decode(GSVC_JSON_B64).decode("utf-8"))
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive",
             "https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(svc_info, scopes=scope)
    return gspread.authorize(creds)


PLATFORM_MAP = {
    "CJ온스타일": "Live", "CJ온스타일 플러스": "TC", "GS홈쇼핑": "Live", "GS홈쇼핑 마이샵": "TC",
    "KT알파쇼핑": "TC", "NS홈쇼핑": "Live", "NS홈쇼핑 샵플러스": "TC", "SK스토아": "TC",
    "공영쇼핑": "Live", "롯데원티비": "TC", "롯데홈쇼핑": "Live", "쇼핑엔티": "TC",
    "신세계쇼핑": "TC", "현대홈쇼핑": "Live", "현대홈쇼핑 플러스샵": "TC", "홈앤쇼핑": "Live",
}
PLATFORMS_BY_LEN = sorted(PLATFORM_MAP.keys(), key=len, reverse=True)


def split_company_from_broadcast(text):
    if not text: return text, "", ""
    t = text.rstrip()
    for key in PLATFORMS_BY_LEN:
        pattern = r"\s*" + re.escape(key) + r"\s*$"
        if re.search(pattern, t):
            cleaned = re.sub(pattern, "", t).rstrip()
            return cleaned, key, PLATFORM_MAP[key]
    return text, "", ""


def _to_int_kor(s):
    if not s or str(s).strip() in ["", "-"]: return 0
    t = str(s).strip().replace(",", "").replace(" ", "")
    if re.fullmatch(r"-?\d+(\.\d+)?", t): return int(float(t))
    unit_map = {"억": 100_000_000, "만": 10_000}
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)(억|만)", t)
    if m: return int(float(m.group(1)) * unit_map[m.group(2)])
    total = 0
    rest = t
    for unit in ["억", "만"]:
        if unit in rest:
            parts = rest.split(unit)
            try:
                total += int(float(parts[0]) * unit_map[unit])
            except Exception:
                pass
            rest = parts[1] if len(parts) > 1 else ""
    if re.fullmatch(r"-?\d+", rest): total += int(rest)
    if total == 0:
        nums = re.findall(r"-?\d+", t)
        return int(nums[0]) if nums else 0
    return total


def preprocess_dataframe(df_raw, sh):
    print("🧮 데이터 전처리 시작 (분 단위 시간 분할합산 및 58:42 가중치 적용)")
    df = df_raw.copy()

    split_result = df["방송시간"].str.split("\n", n=1, expand=True)

    def clean_date_str(val):
        match = re.search(r'(\d{2}\.\d{1,2}\.\d{1,2})', str(val))
        return match.group(1) if match else val

    df["방송날짜"] = pd.to_datetime(split_result[0].apply(clean_date_str), format="%y.%m.%d",
                                errors="coerce").dt.strftime("%Y-%m-%d")
    df["방송시작시간"] = split_result[1].str.strip() if len(split_result.columns) == 2 else ""

    try:
        day = pd.to_datetime(df["방송날짜"].iloc[0]).date()
    except Exception:
        KST = timezone(timedelta(hours=9))
        day = datetime.now(KST).date() - timedelta(days=1)

    df["상품명"], df["회사명"], df["홈쇼핑구분"] = zip(*df["방송정보"].astype(str).apply(split_company_from_broadcast))
    df["매출액 환산수식"] = df["매출액"].apply(_to_int_kor)

    try:
        ref_raw = sh.worksheet("기준가치").get_all_values()
        ref_df = pd.DataFrame(ref_raw[1:], columns=[c.strip() for c in ref_raw[0]])
        if "기준시간" not in ref_df.columns:
            for c in ref_df.columns:
                if c.replace(" ", "") == "기준시간":
                    ref_df.rename(columns={c: "기준시간"}, inplace=True)
                    break
        ref_df["기준시간"] = pd.to_numeric(ref_df["기준시간"], errors='coerce')
    except Exception as e:
        print(f"⚠️ 기준가치 시트 로드 오류: {e}")
        ref_df = pd.DataFrame()

    df["일자"] = pd.to_datetime(df["방송날짜"]).dt.day.astype(str) + "일"
    df["시간대"] = pd.to_datetime(df["방송시작시간"], format="%H:%M", errors="coerce").dt.hour.astype(str)

    def to_dt(hhmm):
        try:
            h, m = map(int, str(hhmm).split(":"))
            return datetime.combine(day, datetime.min.time()) + timedelta(hours=h, minutes=m)
        except Exception:
            return pd.NaT

    df["_start_dt"] = df["방송시작시간"].apply(to_dt)
    df = df.merge(
        df.sort_values(["회사명", "_start_dt"]).drop_duplicates(subset=["회사명", "_start_dt"])[["회사명", "_start_dt"]]
        .assign(_next_unique_start=lambda x: x.groupby("회사명")["_start_dt"].shift(-1)),
        on=["회사명", "_start_dt"], how="left"
    )

    def decide_end(row):
        st, et = row["_start_dt"], row["_next_unique_start"]
        if pd.isna(st): return pd.NaT
        if pd.isna(et): return datetime.combine(day, datetime.min.time()) + timedelta(days=1, minutes=30)
        return st + timedelta(hours=2) if et - st > timedelta(hours=2) else et

    df["_end_dt"] = df.apply(decide_end, axis=1)
    df["종료시간"] = df["_end_dt"].apply(
        lambda dt: "24:30" if isinstance(dt, datetime) and (
                dt - datetime.combine(day, datetime.min.time())) >= timedelta(days=1, minutes=30)
        else (dt.strftime("%H:%M") if isinstance(dt, datetime) else "")
    )

    df["_방송시간(분)"] = df.apply(
        lambda r: int(max(timedelta(0), r['_end_dt'] - r['_start_dt']).total_seconds() // 60) if pd.notna(
            r["_start_dt"]) and pd.notna(r["_end_dt"]) else 0, axis=1
    )
    df["방송시간 절대시"] = df["_방송시간(분)"].apply(lambda m: f"{m // 60:02d}:{m % 60:02d}")

    def calculate_split_value(row):
        if pd.isna(row["_start_dt"]) or pd.isna(row["_end_dt"]) or ref_df.empty:
            return 0.0

        total_val = 0.0
        current_time = row["_start_dt"]
        end_time = row["_end_dt"]
        target_day = row["일자"]

        while current_time < end_time:
            current_hour = current_time.hour
            next_hour_time = (current_time + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            segment_end = min(next_hour_time, end_time)
            segment_minutes = (segment_end - current_time).total_seconds() / 60.0

            try:
                val_str = ref_df.loc[ref_df["기준시간"] == current_hour, target_day].values
                hour_rate = float(str(val_str[0]).replace(",", "")) if len(val_str) > 0 and str(
                    val_str[0]).strip() != "" else 0.0
            except Exception:
                hour_rate = 0.0

            total_val += (hour_rate / 60.0) * segment_minutes
            current_time = segment_end

        return total_val

    df["환산가치"] = df.apply(calculate_split_value, axis=1)

    df["_순번"] = df.groupby(["회사명", "방송시작시간"]).cumcount()
    df["_총개수"] = df.groupby(["회사명", "방송시작시간"])["방송시작시간"].transform("size")

    def apply_split_ratio(row):
        if row["_총개수"] == 1:
            return row["환산가치"]
        elif row["_총개수"] == 2:
            return row["환산가치"] * 0.58 if row["_순번"] == 0 else row["환산가치"] * 0.42
        else:
            return row["환산가치"] / row["_총개수"]

    df["분리송출고려환산가치"] = df.apply(apply_split_ratio, axis=1)
    df["분리송출구분"] = df["_총개수"].apply(lambda x: "분리송출" if x > 1 else "일반")

    df["주문효율 /h"] = df.apply(
        lambda r: float(r["매출액 환산수식"]) / float(r["분리송출고려환산가치"]) if r[
                                                                                   "분리송출고려환산가치"] != 0.0 else 0.0,
        axis=1
    )

    final_cols = ["방송날짜", "방송시작시간", "상품명", "분류", "판매량", "매출액", "상품수", "회사명", "홈쇼핑구분",
                  "매출액 환산수식", "일자", "시간대", "환산가치", "종료시간", "방송시간 절대시", "분리송출구분",
                  "분리송출고려환산가치", "주문효율 /h", "AI분류"]
    for c in final_cols:
        if c not in df.columns: df[c] = ""

    print("✅ 데이터 전처리 완료 (19개 열 생성, 분 단위 합산 및 58:42 배분 완료)")
    return df[final_cols].rename(columns={"상품명": "방송정보"})


# ===================== 메인 =====================
def main():
    driver = None
    relay = None
    logged_in = False
    try:
        relay = setup_proxy()
        driver = make_driver(relay.port if relay else None)
        login_and_handle_session(driver)
        logged_in = True
        df_raw = crawl_schedule(driver)

        if df_raw.empty:
            raise RuntimeError("추출된 편성표가 0건입니다.")

        gc = gs_client_from_env()
        sh = gc.open_by_url(SPREADSHEET_URL)
        print("[GS] 구글 시트 연결 OK")

        df_processed = preprocess_dataframe(df_raw, sh)

        try:
            ws_raw = sh.worksheet(WORKSHEET_NAME)
        except Exception:
            ws_raw = sh.add_worksheet(title=WORKSHEET_NAME, rows=2, cols=len(df_processed.columns))

        df_u = df_processed.fillna("")
        payload = [df_u.columns.tolist()] + df_u.values.tolist()

        ws_raw.clear()
        ws_raw.update(range_name="A1", values=payload, value_input_option="RAW")
        print(f"🎉 1단계 크롤링 완료! 편성표RAW 업데이트 완료 ({len(payload)}행)")

    except Exception as e:
        import traceback
        print("❌ 오류 발생:", repr(e))
        print(traceback.format_exc())
        if driver:
            dump_state(driver)
        sys.exit(1)

    finally:
        if driver:
            if logged_in:
                execute_logout(driver)
            try:
                driver.quit()
            except Exception:
                pass
            print("🛑 브라우저 종료 완료.")
        if relay:
            relay.report()
            relay.stop()


if __name__ == "__main__":
    main()
