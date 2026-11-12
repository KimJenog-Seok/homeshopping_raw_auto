#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, time, re, json, base64, sys, shutil, subprocess, traceback
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    import undetected_chromedriver as uc
except Exception:
    uc = None

# ===================== 설정 =====================
WAIT = 20
ARTIFACT_DIR = Path("artifacts")
ARTIFACT_DIR.mkdir(exist_ok=True)

BASE_URL = "https://live.ecomm-data.com"
LOGIN_URL = f"{BASE_URL}/user/sign_in"
SCHEDULE_URL = f"{BASE_URL}/schedule/hs"

# ▼ Secrets 를 쓰면 환경변수가 우선 적용됩니다.
# ▼ Secrets 를 안 쓰려면 아래 두 줄의 따옴표 안에 직접 입력하세요.
ID_FALLBACK = "smt@trncompany.co.kr"
PW_FALLBACK = "sales7777!!"
ECOMM_ID = (os.environ.get("ECOMM_ID") or ID_FALLBACK).strip()
ECOMM_PW = (os.environ.get("ECOMM_PW") or PW_FALLBACK).strip()

SPREADSHEET_URL = os.environ.get(
    "SPREADSHEET_URL",
    "https://docs.google.com/spreadsheets/d/1Aqwj1SkHsgAr08doidy_tfnHJNwgdFfNrMoMteoXMdE/edit",
).strip()
WORKSHEET_NAME = "편성표RAW"

KST = timezone(timedelta(hours=9))

BLOCK_KEYWORDS = ["just a moment", "access denied", "cloudflare", "forbidden",
                  "403", "captcha", "verify you are human", "접근이 거부", "차단"]


# ===================== 브라우저 =====================
def find_chrome_binary():
    cand = os.environ.get("CHROME_BIN") or os.environ.get("CHROME_PATH")
    if cand and Path(cand).exists():
        return cand
    for cmd in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium", "chrome"):
        p = shutil.which(cmd)
        if p:
            return p
    return None


def chrome_major(binary):
    if not binary:
        return None
    try:
        out = subprocess.check_output([binary, "--version"], text=True, stderr=subprocess.STDOUT)
        m = re.search(r"(\d+)\.\d+\.\d+\.\d+", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def build_options(opts_cls):
    opts = opts_cls()
    # 기본은 헤드리스 OFF (GitHub Actions 에서는 xvfb-run 위에서 일반 모드로 실행)
    if os.environ.get("HEADLESS", "0") == "1":
        opts.add_argument("--headless=new")
    for arg in ("--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--window-size=1920,1080", "--lang=ko-KR"):
        opts.add_argument(arg)
    return opts


def make_driver():
    binary = find_chrome_binary()
    ver = chrome_major(binary)
    print(f"[INFO] chrome={binary} / major={ver} / DISPLAY={os.environ.get('DISPLAY')} "
          f"/ HEADLESS={os.environ.get('HEADLESS', '0')}")

    driver = None
    if uc is not None:
        try:
            kwargs = dict(options=build_options(uc.ChromeOptions), use_subprocess=True)
            if ver:
                kwargs["version_main"] = ver
            if binary:
                kwargs["browser_executable_path"] = binary
            driver = uc.Chrome(**kwargs)
            print("[INFO] undetected_chromedriver 로 실행")
        except Exception as e:
            print(f"⚠️ uc 실행 실패, 일반 selenium 으로 재시도: {type(e).__name__}: {str(e)[:300]}")

    if driver is None:
        opts = build_options(webdriver.ChromeOptions)
        if binary:
            opts.binary_location = binary
        driver = webdriver.Chrome(options=opts)
        print("[INFO] 일반 selenium 으로 실행")

    driver.set_page_load_timeout(60)
    return driver


def dump_page(driver, tag):
    """현재 화면 상태를 로그/파일로 남겨 차단 여부를 판단할 수 있게 함"""
    try:
        title = driver.title or ""
        body = driver.find_element(By.TAG_NAME, "body").text[:500]
        print(f"[DEBUG:{tag}] URL   : {driver.current_url}")
        print(f"[DEBUG:{tag}] TITLE : {title}")
        print(f"[DEBUG:{tag}] BODY  : {body!r}")
        driver.save_screenshot(str(ARTIFACT_DIR / f"{tag}.png"))
        (ARTIFACT_DIR / f"{tag}.html").write_text(driver.page_source, encoding="utf-8")

        text = (title + " " + body).lower()
        hit = [k for k in BLOCK_KEYWORDS if k in text]
        if hit:
            print(f"🚫 [{tag}] 차단/검증 페이지로 의심됩니다. 감지 키워드: {hit}")
    except Exception as e:
        print(f"⚠️ dump_page 실패: {e}")


# ===================== 로그인 / 크롤링 =====================
def visible(driver, css):
    items = [e for e in driver.find_elements(By.CSS_SELECTOR, css) if e.is_displayed()]
    return items[0] if items else None


def open_login_form(driver):
    """로그인 폼을 연다. 직접 URL 로 안 되면 메인 → '로그인' 링크 순서로 시도"""
    driver.get(LOGIN_URL)
    print("[STEP] 로그인 페이지 직접 진입")
    time.sleep(3)
    dump_page(driver, "login_page")
    try:
        WebDriverWait(driver, WAIT).until(lambda d: visible(d, "input[name='email']") is not None)
        return
    except Exception:
        print("⚠️ 직접 진입 실패 → 메인 페이지에서 '로그인' 링크 클릭 시도")

    driver.get(BASE_URL)
    time.sleep(3)
    dump_page(driver, "main_page")
    link = WebDriverWait(driver, WAIT).until(EC.element_to_be_clickable((
        By.XPATH,
        "//a[contains(normalize-space(.), '로그인')] | //button[contains(normalize-space(.), '로그인')]")))
    link.click()
    WebDriverWait(driver, WAIT).until(lambda d: visible(d, "input[name='email']") is not None)


def login(driver):
    print(f"[INFO] ID 길이={len(ECOMM_ID)}, PW 길이={len(ECOMM_PW)} (0이면 입력/Secrets 확인)")
    if not ECOMM_ID or not ECOMM_PW:
        raise RuntimeError("ID/PW 가 비어 있습니다. Secrets(ECOMM_ID, ECOMM_PW) 또는 코드 상단 FALLBACK 확인")

    open_login_form(driver)
    time.sleep(2)

    email_input = visible(driver, "input[name='email']")
    pw_input = visible(driver, "input[name='password']")
    for el, val in ((email_input, ECOMM_ID), (pw_input, ECOMM_PW)):
        el.click()
        el.clear()
        el.send_keys(val)
        time.sleep(0.5)

    driver.find_element(By.XPATH, "//button[contains(., '로그인')]").click()
    print("[STEP] 로그인 버튼 클릭")

    try:
        WebDriverWait(driver, 20).until(lambda d: "/user/sign_in" not in d.current_url)
    except Exception:
        dump_page(driver, "login_failed")
        raise RuntimeError(f"로그인 실패. 현재 URL: {driver.current_url}")

    print(f"✅ 로그인 성공: {driver.current_url}")
    time.sleep(3)

    # 중복 접속 세션이 있으면 종료 후 접속
    try:
        btns = driver.find_elements(By.XPATH, "//button[text()='종료 후 접속']")
        if btns:
            items = [li for li in driver.find_elements(By.CSS_SELECTOR, "ul > li") if li.is_displayed()]
            if items:
                items[-1].click()
                time.sleep(1)
            btns[0].click()
            time.sleep(2)
            print("[STEP] 기존 세션 종료 처리")
    except Exception:
        pass


def crawl_schedule(driver):
    driver.get(SCHEDULE_URL)
    print("[STEP] 편성표 페이지 이동")
    time.sleep(3)

    yesterday = datetime.now(KST).date() - timedelta(days=1)
    date_text = str(yesterday.day)

    try:
        date_button = WebDriverWait(driver, WAIT).until(
            EC.element_to_be_clickable((By.XPATH, f"//div[text()='{date_text}']"))
        )
    except Exception:
        dump_page(driver, "schedule_failed")
        raise
    driver.execute_script("arguments[0].click();", date_button)
    time.sleep(3)

    tables = WebDriverWait(driver, WAIT).until(
        EC.presence_of_all_elements_located((By.TAG_NAME, "table"))
    )

    columns = ["방송시간", "방송정보", "분류", "판매량", "매출액", "상품수"]
    rows_out = []
    for table in tables:
        try:
            for row in table.find_element(By.TAG_NAME, "tbody").find_elements(By.TAG_NAME, "tr"):
                cols = row.find_elements(By.TAG_NAME, "td")
                if len(cols) < 7:
                    continue
                spans = cols[1].find_elements(By.TAG_NAME, "span")
                if len(spans) == 2:
                    bt = f"{spans[0].text.strip()}\n{spans[1].text.strip()}"
                else:
                    bt = cols[1].text.strip()
                rows_out.append({
                    "방송시간": bt,
                    "방송정보": cols[2].text.strip(),
                    "분류": cols[3].text.strip(),
                    "판매량": cols[4].text.strip(),
                    "매출액": cols[5].text.strip(),
                    "상품수": cols[6].text.strip(),
                })
        except Exception:
            continue

    df = pd.DataFrame(rows_out, columns=columns)
    print(f"[STEP] 편성표 {len(df)}건 추출")
    return df


def logout(driver):
    print("[STEP] 로그아웃")
    try:
        menu = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//b[contains(text(), '쇼핑엔티')]"))
        )
        driver.execute_script("arguments[0].click();", menu)
        time.sleep(1)
        btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//a[contains(text(), '로그아웃')]"))
        )
        driver.execute_script("arguments[0].click();", btn)
        time.sleep(2)
        print("✅ 로그아웃 완료")
    except Exception as e:
        print(f"⚠️ 로그아웃 실패(무시): {type(e).__name__}")


# ===================== Google Sheets =====================
def gs_client_from_env():
    b64 = os.environ.get("KEY1", "").strip()
    if not b64:
        raise RuntimeError("KEY1 환경변수가 비어 있습니다 (구글 서비스계정 키)")
    info = json.loads(base64.b64decode(b64).decode("utf-8"))
    scope = ["https://spreadsheets.google.com/feeds",
             "https://www.googleapis.com/auth/drive",
             "https://www.googleapis.com/auth/spreadsheets"]
    return gspread.authorize(Credentials.from_service_account_info(info, scopes=scope))


# ===================== 전처리 (로직 기존과 동일) =====================
PLATFORM_MAP = {
    "CJ온스타일": "Live", "CJ온스타일 플러스": "TC", "GS홈쇼핑": "Live", "GS홈쇼핑 마이샵": "TC",
    "KT알파쇼핑": "TC", "NS홈쇼핑": "Live", "NS홈쇼핑 샵플러스": "TC", "SK스토아": "TC",
    "공영쇼핑": "Live", "롯데원티비": "TC", "롯데홈쇼핑": "Live", "쇼핑엔티": "TC",
    "신세계쇼핑": "TC", "현대홈쇼핑": "Live", "현대홈쇼핑 플러스샵": "TC", "홈앤쇼핑": "Live",
}
PLATFORMS_BY_LEN = sorted(PLATFORM_MAP.keys(), key=len, reverse=True)

FINAL_COLS = ["방송날짜", "방송시작시간", "상품명", "분류", "판매량", "매출액", "상품수", "회사명", "홈쇼핑구분",
              "매출액 환산수식", "일자", "시간대", "환산가치", "종료시간", "방송시간 절대시", "분리송출구분",
              "분리송출고려환산가치", "주문효율 /h", "AI분류"]


def split_company(text):
    if not text:
        return text, "", ""
    t = text.rstrip()
    for key in PLATFORMS_BY_LEN:
        pattern = r"\s*" + re.escape(key) + r"\s*$"
        if re.search(pattern, t):
            return re.sub(pattern, "", t).rstrip(), key, PLATFORM_MAP[key]
    return text, "", ""


def to_int_kor(s):
    if not s or str(s).strip() in ["", "-"]:
        return 0
    t = str(s).strip().replace(",", "").replace(" ", "")
    if re.fullmatch(r"-?\d+(\.\d+)?", t):
        return int(float(t))
    unit_map = {"억": 100_000_000, "만": 10_000}
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)(억|만)", t)
    if m:
        return int(float(m.group(1)) * unit_map[m.group(2)])
    total, rest = 0, t
    for unit in ["억", "만"]:
        if unit in rest:
            parts = rest.split(unit)
            try:
                total += int(float(parts[0]) * unit_map[unit])
            except Exception:
                pass
            rest = parts[1] if len(parts) > 1 else ""
    if re.fullmatch(r"-?\d+", rest):
        total += int(rest)
    if total == 0:
        nums = re.findall(r"-?\d+", t)
        return int(nums[0]) if nums else 0
    return total


def load_reference(sh):
    try:
        raw = sh.worksheet("기준가치").get_all_values()
        ref = pd.DataFrame(raw[1:], columns=[c.strip() for c in raw[0]])
        if "기준시간" not in ref.columns:
            for c in ref.columns:
                if c.replace(" ", "") == "기준시간":
                    ref = ref.rename(columns={c: "기준시간"})
                    break
        ref["기준시간"] = pd.to_numeric(ref["기준시간"], errors="coerce")
        return ref
    except Exception as e:
        print(f"⚠️ 기준가치 시트 로드 오류: {e}")
        return pd.DataFrame()


def preprocess(df_raw, sh):
    print("[STEP] 전처리 시작")
    df = df_raw.copy()

    # 1. 날짜/시간 파싱
    parts = df["방송시간"].str.split("\n", n=1, expand=True)

    def clean_date(v):
        m = re.search(r"(\d{2}\.\d{1,2}\.\d{1,2})", str(v))
        return m.group(1) if m else v

    df["방송날짜"] = pd.to_datetime(parts[0].apply(clean_date), format="%y.%m.%d",
                                errors="coerce").dt.strftime("%Y-%m-%d")
    df["방송시작시간"] = parts[1].str.strip() if parts.shape[1] == 2 else ""

    try:
        day = pd.to_datetime(df["방송날짜"].iloc[0]).date()
    except Exception:
        day = datetime.now(KST).date() - timedelta(days=1)
    midnight = datetime.combine(day, datetime.min.time())

    # 2. 회사명/구분
    df["상품명"], df["회사명"], df["홈쇼핑구분"] = zip(*df["방송정보"].astype(str).apply(split_company))
    df["매출액 환산수식"] = df["매출액"].apply(to_int_kor)

    ref_df = load_reference(sh)
    df["일자"] = pd.to_datetime(df["방송날짜"]).dt.day.astype(str) + "일"
    df["시간대"] = pd.to_datetime(df["방송시작시간"], format="%H:%M", errors="coerce").dt.hour.astype(str)

    # 3. 시작/종료 시간
    def to_dt(hhmm):
        try:
            h, m = map(int, str(hhmm).split(":"))
            return midnight + timedelta(hours=h, minutes=m)
        except Exception:
            return pd.NaT

    df["_start_dt"] = df["방송시작시간"].apply(to_dt)
    nxt = (df.sort_values(["회사명", "_start_dt"])
             .drop_duplicates(subset=["회사명", "_start_dt"])[["회사명", "_start_dt"]]
             .assign(_next_unique_start=lambda x: x.groupby("회사명")["_start_dt"].shift(-1)))
    df = df.merge(nxt, on=["회사명", "_start_dt"], how="left")

    def decide_end(r):
        st, et = r["_start_dt"], r["_next_unique_start"]
        if pd.isna(st):
            return pd.NaT
        if pd.isna(et):
            return midnight + timedelta(days=1, minutes=30)
        return st + timedelta(hours=2) if et - st > timedelta(hours=2) else et

    df["_end_dt"] = df.apply(decide_end, axis=1)

    def end_str(dt):
        if not isinstance(dt, datetime):
            return ""
        if dt - midnight >= timedelta(days=1, minutes=30):
            return "24:30"
        return dt.strftime("%H:%M")

    df["종료시간"] = df["_end_dt"].apply(end_str)

    def minutes(r):
        if pd.notna(r["_start_dt"]) and pd.notna(r["_end_dt"]):
            return int(max(timedelta(0), r["_end_dt"] - r["_start_dt"]).total_seconds() // 60)
        return 0

    df["_방송시간(분)"] = df.apply(minutes, axis=1)
    df["방송시간 절대시"] = df["_방송시간(분)"].apply(lambda m: f"{m // 60:02d}:{m % 60:02d}")

    # 4. 시간대별 환산가치
    def split_value(r):
        if pd.isna(r["_start_dt"]) or pd.isna(r["_end_dt"]) or ref_df.empty:
            return 0.0
        total, cur, end = 0.0, r["_start_dt"], r["_end_dt"]
        while cur < end:
            nxt_hour = (cur + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
            seg_end = min(nxt_hour, end)
            seg_min = (seg_end - cur).total_seconds() / 60.0
            try:
                v = ref_df.loc[ref_df["기준시간"] == cur.hour, r["일자"]].values
                rate = float(str(v[0]).replace(",", "")) if len(v) and str(v[0]).strip() != "" else 0.0
            except Exception:
                rate = 0.0
            total += rate / 60.0 * seg_min
            cur = seg_end
        return total

    df["환산가치"] = df.apply(split_value, axis=1)

    # 5. 분리송출 (2개면 58:42, 3개 이상이면 N등분)
    g = df.groupby(["회사명", "방송시작시간"])
    df["_순번"] = g.cumcount()
    df["_총개수"] = g["방송시작시간"].transform("size")

    def ratio(r):
        if r["_총개수"] == 1:
            return r["환산가치"]
        if r["_총개수"] == 2:
            return r["환산가치"] * (0.58 if r["_순번"] == 0 else 0.42)
        return r["환산가치"] / r["_총개수"]

    df["분리송출고려환산가치"] = df.apply(ratio, axis=1)
    df["분리송출구분"] = df["_총개수"].apply(lambda x: "분리송출" if x > 1 else "일반")

    # 6. 주문효율
    df["주문효율 /h"] = df.apply(
        lambda r: float(r["매출액 환산수식"]) / float(r["분리송출고려환산가치"])
        if r["분리송출고려환산가치"] != 0.0 else 0.0, axis=1)

    for c in FINAL_COLS:
        if c not in df.columns:
            df[c] = ""

    print("[STEP] 전처리 완료")
    return df[FINAL_COLS].rename(columns={"상품명": "방송정보"})


# ===================== 메인 =====================
def main():
    driver = None
    logged_in = False
    try:
        driver = make_driver()
        login(driver)
        logged_in = True
        df_raw = crawl_schedule(driver)

        if df_raw.empty:
            dump_page(driver, "empty_schedule")
            raise RuntimeError("추출된 편성표가 0건입니다.")

        sh = gs_client_from_env().open_by_url(SPREADSHEET_URL)
        print("[GS] 구글 시트 연결 OK")

        df_out = preprocess(df_raw, sh).fillna("")

        try:
            ws = sh.worksheet(WORKSHEET_NAME)
        except Exception:
            ws = sh.add_worksheet(title=WORKSHEET_NAME, rows=2, cols=len(df_out.columns))

        payload = [df_out.columns.tolist()] + df_out.values.tolist()
        ws.clear()
        ws.update(range_name="A1", values=payload, value_input_option="RAW")
        print(f"🎉 완료! {WORKSHEET_NAME} 업데이트 ({len(payload)}행)")

    except Exception as e:
        print("❌ 오류 발생:", repr(e))
        print(traceback.format_exc())
        if driver:
            dump_page(driver, "error")
        sys.exit(1)

    finally:
        if driver:
            if logged_in:
                logout(driver)
            try:
                driver.quit()
            except Exception:
                pass
            print("🛑 브라우저 종료")


if __name__ == "__main__":
    main()
