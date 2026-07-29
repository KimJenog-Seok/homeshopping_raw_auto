#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, time, re, json, base64
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ===================== 설정 =====================
WAIT = 5
ARTIFACT_DIR = Path("artifacts")
ARTIFACT_DIR.mkdir(exist_ok=True)

# 정석님 요청대로 하드코딩 유지
ECOMM_ID = "smt@trncompany.co.kr"
ECOMM_PW = "sales4580!!"
SCHEDULE_URL = "https://live.ecomm-data.com/schedule/hs"

# 새로 만들어주신 테스트용 시트 URL 반영
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1Aqwj1SkHsgAr08doidy_tfnHJNwgdFfNrMoMteoXMdE/edit"
WORKSHEET_NAME = "편성표RAW"

# ===================== 유틸 및 크롤링 =====================
def make_driver():
    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=ko-KR")
    opts.add_argument("user-agent=Mozilla/5.0 Chrome/122.0.0.0 Safari/537.36")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(60)
    return driver

def login_and_handle_session(driver):
    driver.get("https://live.ecomm-data.com")
    print("[STEP] 메인 페이지 진입 완료")

    login_link = WebDriverWait(driver, WAIT).until(
        EC.element_to_be_clickable((By.LINK_TEXT, "로그인"))
    )
    driver.execute_script("arguments[0].click();", login_link)
    
    t0 = time.time()
    while "/user/sign_in" not in driver.current_url:
        if time.time() - t0 > WAIT:
            raise Exception("로그인 페이지 진입 실패 (타임아웃)")
        time.sleep(0.5)

    time.sleep(1)
    email_input = [e for e in driver.find_elements(By.CSS_SELECTOR, "input[name='email']") if e.is_displayed()][0]
    pw_input    = [e for e in driver.find_elements(By.CSS_SELECTOR, "input[name='password']") if e.is_displayed()][0]
    email_input.clear(); email_input.send_keys(ECOMM_ID)
    pw_input.clear(); pw_input.send_keys(ECOMM_PW)
    time.sleep(0.5)

    form = driver.find_element(By.TAG_NAME, "form")
    login_button = form.find_element(By.XPATH, ".//button[contains(text(), '로그인')]")
    driver.execute_script("arguments[0].click();", login_button)
    print("✅ 로그인 시도!")

    # 세션 초과 팝업 처리
    time.sleep(2)
    try:
        session_items = [li for li in driver.find_elements(By.CSS_SELECTOR, "ul > li") if li.is_displayed()]
        if session_items:
            session_items[-1].click()
            time.sleep(1)
            close_btn = driver.find_element(By.XPATH, "//button[text()='종료 후 접속']")
            if close_btn.is_enabled():
                driver.execute_script("arguments[0].click();", close_btn)
                time.sleep(2)
    except Exception:
        pass
    print("✅ 로그인 성공 판정!")

def crawl_schedule(driver):
    driver.get(SCHEDULE_URL)
    print("✅ 편성표 홈쇼핑 페이지 이동 완료")
    time.sleep(2)

    KST = timezone(timedelta(hours=9))
    yesterday = datetime.now(KST).date() - timedelta(days=1)
    date_text = str(yesterday.day)

    date_button_xpath = f"//div[text()='{date_text}']"
    date_button = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, date_button_xpath))
    )
    driver.execute_script("arguments[0].click();", date_button)
    time.sleep(3)

    tables = WebDriverWait(driver, 10).until(
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
                        broadcast_time = f"{spans[0].text.strip()}\n{spans[1].text.strip()}" if len(spans) == 2 else cols[1].text.strip()
                    except:
                        broadcast_time = cols[1].text.strip()

                    all_data.append({
                        "방송시간": broadcast_time, "방송정보": cols[2].text.strip(), "분류": cols[3].text.strip(),
                        "판매량": cols[4].text.strip(), "매출액": cols[5].text.strip(), "상품수": cols[6].text.strip()
                    })
        except:
            continue

    df = pd.DataFrame(all_data, columns=columns)
    print(f"총 {len(df)}개 편성표 정보 추출 완료")
    return df

# ===================== Google Sheets 및 전처리 =====================
def gs_client_from_env():
    GSVC_JSON_B64 = os.environ.get("KEY1", "")
    svc_info = json.loads(base64.b64decode(GSVC_JSON_B64).decode("utf-8"))
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(svc_info, scopes=scope)
    return gspread.authorize(creds)

PLATFORM_MAP = {
    "CJ온스타일":"Live","CJ온스타일 플러스":"TC","GS홈쇼핑":"Live","GS홈쇼핑 마이샵":"TC",
    "KT알파쇼핑":"TC","NS홈쇼핑":"Live","NS홈쇼핑 샵플러스":"TC","SK스토아":"TC",
    "공영쇼핑":"Live","롯데원티비":"TC","롯데홈쇼핑":"Live","쇼핑엔티":"TC",
    "신세계쇼핑":"TC","현대홈쇼핑":"Live","현대홈쇼핑 플러스샵":"TC","홈앤쇼핑":"Live",
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
    total = 0; rest = t
    for unit in ["억", "만"]:
        if unit in rest:
            parts = rest.split(unit)
            try: total += int(float(parts[0]) * unit_map[unit])
            except: pass
            rest = parts[1] if len(parts) > 1 else ""
    if re.fullmatch(r"-?\d+", rest): total += int(rest)
    if total == 0:
        nums = re.findall(r"-?\d+", t)
        return int(nums[0]) if nums else 0
    return total

def preprocess_dataframe(df_raw, sh):
    print("🧮 데이터 전처리 시작")
    df = df_raw.copy()
    split_result = df["방송시간"].str.split("\n", n=1, expand=True)

    def clean_date_str(val):
        match = re.search(r'(\d{2}\.\d{1,2}\.\d{1,2})', str(val))
        return match.group(1) if match else val

    cleaned_date = split_result[0].apply(clean_date_str)
    df["방송날짜"] = pd.to_datetime(cleaned_date, format="%y.%m.%d", errors="coerce").dt.strftime("%Y-%m-%d")
    df["방송시작시간"] = split_result[1].str.strip() if len(split_result.columns) == 2 else ""
    
    try: day = pd.to_datetime(df["방송날짜"].iloc[0]).date()
    except:
        KST = timezone(timedelta(hours=9))
        day = datetime.now(KST).date() - timedelta(days=1)

    df["상품명"], df["회사명"], df["홈쇼핑구분"] = zip(*df["방송정보"].astype(str).apply(split_company_from_broadcast))
    df["매출액 환산수식"] = df["매출액"].apply(_to_int_kor)

    try:
        ref_df = pd.DataFrame(sh.worksheet("기준가치").get_all_values()[1:], columns=[c.strip() for c in sh.worksheet("기준가치").get_all_values()[0]])
        if "기준시간" not in ref_df.columns:
            for c in ref_df.columns:
                if c.replace(" ", "") == "기준시간": ref_df.rename(columns={c: "기준시간"}, inplace=True); break
        df["일자"] = pd.to_datetime(df["방송날짜"]).dt.day.astype(str) + "일"
        df["시간대"] = pd.to_datetime(df["방송시작시간"], format="%H:%M", errors="coerce").dt.hour.astype(str)
        def lookup_value(row):
            try:
                val = ref_df.loc[ref_df["기준시간"] == row["시간대"], row["일자"]].values
                if len(val) > 0 and str(val[0]).strip() != "": return float(str(val[0]).replace(",", ""))
            except: pass
            return 0.0
        df["_시간당_환산가치"] = df.apply(lookup_value, axis=1)
        print("✅ 기준가치 매핑 완료")
    except Exception as e:
        print(f"⚠️ 기준가치 시트 없음/오류: {e}")
        df["_시간당_환산가치"], df["일자"], df["시간대"] = 0.0, "", ""

    def to_dt(hhmm):
        try:
            h, m = map(int, str(hhmm).split(":"))
            return datetime.combine(day, datetime.min.time()) + timedelta(hours=h, minutes=m)
        except: return pd.NaT

    df["_start_dt"] = df["방송시작시간"].apply(to_dt)
    df = df.merge(df.sort_values(["회사명", "_start_dt"]).drop_duplicates(subset=["회사명", "_start_dt"])[["회사명", "_start_dt"]].assign(_next_unique_start=lambda x: x.groupby("회사명")["_start_dt"].shift(-1)), on=["회사명","_start_dt"], how="left")

    def decide_end(row):
        st, et = row["_start_dt"], row["_next_unique_start"]
        if pd.isna(st): return pd.NaT
        if pd.isna(et): return datetime.combine(day, datetime.min.time()) + timedelta(days=1, minutes=30)
        return st + timedelta(hours=2) if et - st > timedelta(hours=2) else et
    df["_end_dt"] = df.apply(decide_end, axis=1)
    df["종료시간"] = df["_end_dt"].apply(lambda dt: "24:30" if isinstance(dt, datetime) and (dt - datetime.combine(day, datetime.min.time())) >= timedelta(days=1, minutes=30) else (dt.strftime("%H:%M") if isinstance(dt, datetime) else ""))
    df["방송시간 절대시"] = df.apply(lambda r: f"{int(max(timedelta(0), r['_end_dt'] - r['_start_dt']).total_seconds() // 60)//60:02d}:{int(max(timedelta(0), r['_end_dt'] - r['_start_dt']).total_seconds() // 60)%60:02d}" if not pd.isna(r["_start_dt"]) and not pd.isna(r["_end_dt"]) else "00:00", axis=1)
    df["_방송시간(분)"] = df["방송시간 절대시"].apply(lambda v: int(v.split(":")[0])*60 + int(v.split(":")[1]) if ":" in v else 0)
    df["환산가치"] = df.apply(lambda r: (r["_시간당_환산가치"] / 60.0) * r["_방송시간(분)"] if r["_시간당_환산가치"] != 0.0 and r["_방송시간(분)"] != 0 else 0.0, axis=1)

    grp_counts = df.groupby(["회사명", "방송시작시간"])["방송시작시간"].transform("size")
    df["분리송출구분"] = grp_counts.apply(lambda x: "분리송출" if x > 1 else "일반")
    df["분리송출고려환산가치"] = df["환산가치"] / grp_counts.clip(lower=1)
    df["주문효율 /h"] = df.apply(lambda r: float(r["매출액 환산수식"]) / float(r["분리송출고려환산가치"]) if r["분리송출고려환산가치"] != 0.0 else 0.0, axis=1)

    final_cols = ["방송날짜","방송시작시간","상품명","분류","판매량","매출액","상품수","회사명","홈쇼핑구분", "매출액 환산수식","일자","시간대","환산가치","종료시간","방송시간 절대시","분리송출구분", "분리송출고려환산가치","주문효율 /h","AI분류"]
    for c in final_cols:
        if c not in df.columns: df[c] = ""
    
    print("✅ 데이터 전처리 완료 (19개 열 생성 - AI분류는 비워둠)") 
    return df[final_cols].rename(columns={"상품명": "방송정보"})

# ===================== 메인 =====================
def main():
    driver = None
    try:
        driver = make_driver()
        login_and_handle_session(driver)
        df_raw = crawl_schedule(driver)

        gc = gs_client_from_env()
        sh = gc.open_by_url(SPREADSHEET_URL)
        print("[GS] 구글 시트 연결 OK")

        df_processed = preprocess_dataframe(df_raw, sh)

        try: ws_raw = sh.worksheet(WORKSHEET_NAME)
        except: ws_raw = sh.add_worksheet(title=WORKSHEET_NAME, rows=2, cols=len(df_processed.columns))

        df_u = df_processed.fillna("")
        payload = [df_u.columns.tolist()] + df_u.values.tolist()

        ws_raw.clear()
        ws_raw.update(range_name="A1", values=payload,value_input_option="RAW")
        print(f"🎉 1단계 크롤링 완료! 편성표RAW 업데이트 완료 ({len(payload)}행)")

    except Exception as e:
        import traceback
        print("❌ 오류 발생:", e)
        print(traceback.format_exc())
    finally:
        if driver: driver.quit()

if __name__ == "__main__":
    main()
