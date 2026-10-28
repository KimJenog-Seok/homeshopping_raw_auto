#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, time, re, json, base64, sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

# ===================== 설정 =====================
WAIT = 5
ARTIFACT_DIR = Path("artifacts")
ARTIFACT_DIR.mkdir(exist_ok=True)

ECOMM_ID = "smt@trncompany.co.kr"
ECOMM_PW = "sales7777!!"
SCHEDULE_URL = "https://live.ecomm-data.com/schedule/hs"

SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1Aqwj1SkHsgAr08doidy_tfnHJNwgdFfNrMoMteoXMdE/edit"
WORKSHEET_NAME = "편성표RAW"

# ===================== 유틸 및 크롤링 =====================
def make_driver():
    opts = webdriver.ChromeOptions()
    
    # 💡 깃허브 액션용 필수: 헤드리스 모드 활성화
    opts.add_argument("--headless=new")
    
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--lang=ko-KR")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
    
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', {
              get: () => undefined
            })
        '''
    })
    driver.set_page_load_timeout(60)
    return driver

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
    pw_input    = [e for e in driver.find_elements(By.CSS_SELECTOR, "input[name='password']") if e.is_displayed()][0]
    
    # 💡 [핵심 수정 1] 이메일 창 물리적 클릭 후 입력!
    email_input.click()
    time.sleep(0.5)
    email_input.clear()
    email_input.send_keys(ECOMM_ID)
    time.sleep(0.5)
    
    # 💡 [핵심 수정 2] 비밀번호 창 물리적 클릭 후 입력!
    pw_input.click()
    time.sleep(0.5)
    pw_input.clear()
    pw_input.send_keys(ECOMM_PW)
    time.sleep(0.5)
    
    # 💡 [핵심 수정 3] 엔터키 대신 '로그인' 버튼 물리적 클릭!
    login_btn = driver.find_element(By.XPATH, "//button[contains(text(), '로그인')]")
    login_btn.click()
    print("✅ 로그인 버튼 물리적 클릭 완료!")

    try:
        WebDriverWait(driver, 10).until(
            lambda d: "/user/sign_in" not in d.current_url
        )
        print(f"✅ 실제 로그인 성공 판정! 변경된 URL: {driver.current_url}")
    except:
        raise Exception(f"❌ 로그인 실패! 튕겼거나 막혔습니다. 현재 URL: {driver.current_url}")
    
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
        print(f"⚠️ 로그아웃 과정에서 문제가 발생했습니다: {e}")

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
    print("🧮 데이터 전처리 시작 (분 단위 시간 분할합산 및 58:42 가중치 적용)")
    df = df_raw.copy()
    
    # 1. 날짜 및 시간 기본 파싱
    split_result = df["방송시간"].str.split("\n", n=1, expand=True)
    def clean_date_str(val):
        match = re.search(r'(\d{2}\.\d{1,2}\.\d{1,2})', str(val))
        return match.group(1) if match else val

    df["방송날짜"] = pd.to_datetime(split_result[0].apply(clean_date_str), format="%y.%m.%d", errors="coerce").dt.strftime("%Y-%m-%d")
    df["방송시작시간"] = split_result[1].str.strip() if len(split_result.columns) == 2 else ""
    
    try: day = pd.to_datetime(df["방송날짜"].iloc[0]).date()
    except:
        KST = timezone(timedelta(hours=9))
        day = datetime.now(KST).date() - timedelta(days=1)

    # 2. 회사명 및 홈쇼핑 구분 분리
    df["상품명"], df["회사명"], df["홈쇼핑구분"] = zip(*df["방송정보"].astype(str).apply(split_company_from_broadcast))
    df["매출액 환산수식"] = df["매출액"].apply(_to_int_kor)

    # 3. 기준가치 시트 로드
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

    # 4. 방송 시작 및 종료 시간 계산
    def to_dt(hhmm):
        try:
            h, m = map(int, str(hhmm).split(":"))
            return datetime.combine(day, datetime.min.time()) + timedelta(hours=h, minutes=m)
        except: return pd.NaT

    df["_start_dt"] = df["방송시작시간"].apply(to_dt)
    df = df.merge(
        df.sort_values(["회사명", "_start_dt"]).drop_duplicates(subset=["회사명", "_start_dt"])[["회사명", "_start_dt"]]
        .assign(_next_unique_start=lambda x: x.groupby("회사명")["_start_dt"].shift(-1)),
        on=["회사명","_start_dt"], how="left"
    )

    def decide_end(row):
        st, et = row["_start_dt"], row["_next_unique_start"]
        if pd.isna(st): return pd.NaT
        if pd.isna(et): return datetime.combine(day, datetime.min.time()) + timedelta(days=1, minutes=30)
        return st + timedelta(hours=2) if et - st > timedelta(hours=2) else et

    df["_end_dt"] = df.apply(decide_end, axis=1)
    df["종료시간"] = df["_end_dt"].apply(
        lambda dt: "24:30" if isinstance(dt, datetime) and (dt - datetime.combine(day, datetime.min.time())) >= timedelta(days=1, minutes=30)
        else (dt.strftime("%H:%M") if isinstance(dt, datetime) else "")
    )
    
    df["_방송시간(분)"] = df.apply(
        lambda r: int(max(timedelta(0), r['_end_dt'] - r['_start_dt']).total_seconds() // 60) if pd.notna(r["_start_dt"]) and pd.notna(r["_end_dt"]) else 0, axis=1
    )
    df["방송시간 절대시"] = df["_방송시간(분)"].apply(lambda m: f"{m//60:02d}:{m%60:02d}")

    # 5. 시간대별 쪼개기 환산가치 계산 로직
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
                hour_rate = float(str(val_str[0]).replace(",", "")) if len(val_str) > 0 and str(val_str[0]).strip() != "" else 0.0
            except:
                hour_rate = 0.0
            
            total_val += (hour_rate / 60.0) * segment_minutes
            current_time = segment_end
            
        return total_val

    df["환산가치"] = df.apply(calculate_split_value, axis=1)

    # 6. 분리송출 58:42 비율 및 N등분 배분
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
    
    # 7. 주문효율 산출
    df["주문효율 /h"] = df.apply(
        lambda r: float(r["매출액 환산수식"]) / float(r["분리송출고려환산가치"]) if r["분리송출고려환산가치"] != 0.0 else 0.0, axis=1
    )

    # 기존 시트 열 포맷 19개 유지
    final_cols = ["방송날짜","방송시작시간","상품명","분류","판매량","매출액","상품수","회사명","홈쇼핑구분", 
                  "매출액 환산수식","일자","시간대","환산가치","종료시간","방송시간 절대시","분리송출구분", 
                  "분리송출고려환산가치","주문효율 /h","AI분류"]
    for c in final_cols:
        if c not in df.columns: df[c] = ""
        
    print("✅ 데이터 전처리 완료 (19개 열 생성 - 분 단위 합산 및 58:42 배분 완료)") 
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
        
        # 💡 에러 스크린샷 
        if driver:
            try:
                screenshot_path = str(ARTIFACT_DIR / "error_screenshot.png")
                driver.save_screenshot(screenshot_path)
                print(f"📸 에러 발생 순간의 화면을 {screenshot_path} 에 저장했습니다.")
            except Exception as ss_e:
                print(f"⚠️ 스크린샷 저장 실패: {ss_e}")
                
        # 💡 깃허브에게 강제로 에러를 보고해서 무한루프 방지
        sys.exit(1)
        
    finally:
        if driver: 
            execute_logout(driver)
            driver.quit()
            print("🛑 브라우저 종료 완료.")

if __name__ == "__main__":
    main()
