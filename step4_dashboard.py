import os
import json
import base64
import gspread
import pandas as pd
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

def gs_client_from_env():
    GSVC_JSON_B64 = os.environ.get("KEY1", "")
    if not GSVC_JSON_B64:
        raise ValueError("❌ 환경변수 'KEY1'이 설정되지 않았습니다.")
        
    svc_info = json.loads(base64.b64decode(GSVC_JSON_B64).decode("utf-8"))
    scope = [
        "https://spreadsheets.google.com/feeds", 
        "https://www.googleapis.com/auth/drive", 
        "https://www.googleapis.com/auth/spreadsheets"
    ]
    creds = Credentials.from_service_account_info(svc_info, scopes=scope)
    return gspread.authorize(creds)

print("🚀 [Step 4] 통합 DB 업데이트 파이프라인 (최근 7일 재적재 동기화) 시작!")
gc = gs_client_from_env()

SOURCE_SHEET_ID = '1Aqwj1SkHsgAr08doidy_tfnHJNwgdFfNrMoMteoXMdE'
TARGET_SHEET_ID = '106vkIpsodH2uRzb17hMrKz9uKW-BcM7VA4459-nySJ0'

# -----------------------------------------------------------------
# 📌 1. 최근 7일 치(오늘 제외 어제부터 과거 7일) 개별 날짜 탭 일괄 수집
# -----------------------------------------------------------------
print("📖 원본 시트에서 최근 7일 치 개별 날짜 탭 수집 중...")
source_doc = gc.open_by_key(SOURCE_SHEET_ID)

all_source_dfs = []
days_to_sync = 7  # 최근 일주일(7일) 치를 대상으로 재적재

for i in range(1, days_to_sync + 1):
    target_date = datetime.now() - timedelta(days=i)
    source_tab_name = target_date.strftime('%y/%m/%d')
    
    try:
        source_ws = source_doc.worksheet(source_tab_name)
        source_data_fmt = source_ws.get_all_records(value_render_option='FORMATTED_VALUE')
        source_data_raw = source_ws.get_all_records(value_render_option='UNFORMATTED_VALUE')
        
        if not source_data_fmt:
            continue
            
        df_fmt = pd.DataFrame(source_data_fmt)
        df_raw = pd.DataFrame(source_data_raw)
        
        # 헤더 공백 제거
        df_fmt.columns = df_fmt.columns.astype(str).str.strip()
        df_raw.columns = df_raw.columns.astype(str).str.strip()
        
        # AI분류 컬럼명 통일 패치
        df_fmt.rename(columns={'AI분류': 'AI분류(수정)'}, inplace=True)
        df_raw.rename(columns={'AI분류': 'AI분류(수정)'}, inplace=True)
        
        # 소수점 살릴 숫자 컬럼 교체
        numeric_cols = ['판매량', '매출액', '상품수', '매출액 환산수식', '환산가치', '분리송출고려환산가치', '주문효율 /h']
        for col in numeric_cols:
            if col in df_fmt.columns and col in df_raw.columns:
                df_fmt[col] = df_raw[col]
                
        all_source_dfs.append(df_fmt)
        print(f"  └ [{source_tab_name}] 탭 데이터 로드 성공")
        
    except gspread.exceptions.WorksheetNotFound:
        # 주말이나 데이터가 없는 날짜 탭은 건너뜁니다.
        continue

if not all_source_dfs:
    print("❌ 동기화할 최근 7일 치 데이터가 없습니다. 프로세스를 종료합니다.")
    exit()

df_source = pd.concat(all_source_dfs, ignore_index=True)

# 방송날짜 표준화 및 키값 생성
df_source['방송날짜_parsed'] = pd.to_datetime(df_source['방송날짜'], errors='coerce')
df_source = df_source.dropna(subset=['방송날짜_parsed'])
formatted_date = df_source['방송날짜_parsed'].dt.strftime('%Y-%m-%d')
df_source['키값'] = formatted_date + df_source['방송시작시간'].astype(str) + df_source['방송정보'].astype(str) + df_source['회사명'].astype(str)
df_source.drop(columns=['방송날짜_parsed'], inplace=True)

# -----------------------------------------------------------------
# 📌 2. Master DB(RAW) 접근 및 최근 7일 치 데이터 선제 삭제(Clean)
# -----------------------------------------------------------------
print("🔍 Master DB 시트 접근 및 최근 7일 치 기존 데이터 정밀 정리 중...")
target_doc = gc.open_by_key(TARGET_SHEET_ID)
target_ws = target_doc.worksheet('RAW')

target_headers = target_ws.row_values(1)
if '키값' not in target_headers or '방송날짜' not in target_headers:
    print("❌ Master DB에 '키값' 혹은 '방송날짜' 컬럼이 없습니다. 확인해주세요!")
    exit()

target_headers = [str(col).strip() for col in target_headers]

# 전체 데이터를 한 번에 가져와서 판다스로 처리 (API 호출 최소화 및 안전성 확보)
raw_all_data = target_ws.get_all_records(value_render_option='FORMATTED_VALUE')
df_target_existing = pd.DataFrame(raw_all_data)

if not df_target_existing.empty:
    df_target_existing.columns = df_target_existing.columns.astype(str).str.strip()
    
    # 오늘 기준 최근 7일 범위 계산
    cutoff_sync_date = (datetime.now() - timedelta(days=days_to_sync)).strftime('%Y-%m-%d')
    
    # 최근 7일 이외의 과거 데이터는 완벽히 보존하고, 최근 7일 치만 발췌하여 교체 대상 파악
    # (개별 시트에서 수정한 최신 AI분류 값을 덮어씌우기 위해 최근 7일 치는 지우고 새로 적재)
    df_target_existing['방송날짜_fmt'] = pd.to_datetime(df_target_existing['방송날짜'], errors='coerce').dt.strftime('%Y-%m-%d')
    
    # 7일 이외의 데이터만 남김
    df_preserved = df_target_existing[df_target_existing['방송날짜_fmt'] < cutoff_sync_date].copy()
    if '방송날짜_fmt' in df_preserved.columns:
        df_preserved.drop(columns=['방송날짜_fmt'], inplace=True)
        
    print(f"🧹 최근 {days_to_sync}일 치 대상 데이터 갱신을 위해 Master DB 재구축 진행...")
    
    # 누락 컬럼 맞추기
    for col in target_headers:
        if col not in df_source.columns:
            df_source[col] = ''
    df_source_aligned = df_source[target_headers]
    
    # 보존된 과거 데이터와 이번에 새로 긁어온 최근 7일 치 데이터를 결합 (중복 원천 차단)
    for col in target_headers:
        if col not in df_preserved.columns:
            df_preserved[col] = ''
    df_preserved_aligned = df_preserved[target_headers]
    
    df_final_merged = pd.concat([df_preserved_aligned, df_source_aligned], ignore_index=True)
    
    # 키값 기준 중복 제거 (혹시 모를 중복 방지 안전장치)
    if '키값' in df_final_merged.columns:
        df_final_merged.drop_duplicates(subset=['키값'], keep='last', inplace=True)
        
    # RAW 시트 전체 클리어 후 한 번에 안전하게 다시 밀어넣기 (행 꼬임 및 중복 원천 방지)
    print("🔄 Master DB 'RAW' 탭 전체 동기화 덮어쓰기 진행 중...")
    target_ws.clear()
    
    # 헤더 + 데이터 전체 리스트 변환
    final_rows = [target_headers] + df_final_merged.fillna('').values.tolist()
    target_ws.update(final_rows, value_input_option='USER_ENTERED')

else:
    # 기존 데이터가 아예 없을 경우 신규 적재
    for col in target_headers:
        if col not in df_source.columns:
            df_source[col] = ''
    df_source_aligned = df_source[target_headers]
    final_rows = [target_headers] + df_source_aligned.fillna('').values.tolist()
    target_ws.update(final_rows, value_input_option='USER_ENTERED')

print("✅ [Step 4] 최근 7일 치 개별 시트 수정 내용 반영 및 통합 DB 동기화가 완벽하게 완료되었습니다!")
