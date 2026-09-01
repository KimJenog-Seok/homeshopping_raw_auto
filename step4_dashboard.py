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

print("🚀 [Step 4] 통합 DB 안전 동기화 파이프라인 시작 (메모리 최적화 버전)")
gc = gs_client_from_env()

SOURCE_SHEET_ID = '1Aqwj1SkHsgAr08doidy_tfnHJNwgdFfNrMoMteoXMdE'
TARGET_SHEET_ID = '106vkIpsodH2uRzb17hMrKz9uKW-BcM7VA4459-nySJ0'

source_doc = gc.open_by_key(SOURCE_SHEET_ID)
target_doc = gc.open_by_key(TARGET_SHEET_ID)
target_ws = target_doc.worksheet('RAW')

# -----------------------------------------------------------------
# 1. 최근 7일 치 개별 날짜 탭 데이터 한 번에 모으기
# -----------------------------------------------------------------
days_to_sync = 7
print(f"📖 최근 {days_to_sync}일 치 개별 날짜 탭 수집 중...")
all_source_dfs = []

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
        
        df_fmt.columns = df_fmt.columns.astype(str).str.strip()
        df_raw.columns = df_raw.columns.astype(str).str.strip()
        
        # AI분류 컬럼명 통일
        df_fmt.rename(columns={'AI분류': 'AI분류(수정)'}, inplace=True)
        df_raw.rename(columns={'AI분류': 'AI분류(수정)'}, inplace=True)
        
        # 소수점 살릴 숫자 컬럼 교체
        numeric_cols = ['판매량', '매출액', '상품수', '매출액 환산수식', '환산가치', '분리송출고려환산가치', '주문효율 /h']
        for col in numeric_cols:
            if col in df_fmt.columns and col in df_raw.columns:
                df_fmt[col] = df_raw[col]
                
        all_source_dfs.append(df_fmt)
        print(f"  └ [{source_tab_name}] 탭 로드 완료")
    except gspread.exceptions.WorksheetNotFound:
        continue

if not all_source_dfs:
    print("❌ 수집된 최근 7일 치 데이터가 없습니다. 종료합니다.")
    exit()

df_source_all = pd.concat(all_source_dfs, ignore_index=True)

# 방송날짜 표준화 및 키값 생성
df_source_all['방송날짜_parsed'] = pd.to_datetime(df_source_all['방송날짜'], errors='coerce')
df_source_all = df_source_all.dropna(subset=['방송날짜_parsed'])
formatted_date = df_source_all['방송날짜_parsed'].dt.strftime('%Y-%m-%d')
df_source_all['키값'] = formatted_date + df_source_all['방송시작시간'].astype(str) + df_source_all['방송정보'].astype(str) + df_source_all['회사명'].astype(str)
df_source_all.drop(columns=['방송날짜_parsed'], inplace=True)

# 중복 제거 (최신 수정본 유지)
df_source_all.drop_duplicates(subset=['키값'], keep='last', inplace=True)

# -----------------------------------------------------------------
# 2. Master DB(RAW)와 대조하여 안전한 업서트(갱신/추가) 수행
# -----------------------------------------------------------------
print("🔍 Master DB(RAW) 기존 키값 스캔 중...")
target_headers = target_ws.row_values(1)
if '키값' not in target_headers:
    print("❌ Master DB에 '키값' 컬럼이 없습니다!")
    exit()

target_headers = [str(col).strip() for col in target_headers]
key_col_idx = target_headers.index('키값') + 1

# 컬럼 구조 맞추기
for col in target_headers:
    if col not in df_source_all.columns:
        df_source_all[col] = ''
df_source_all = df_source_all[target_headers]

# 기존 키값 목록을 한 번에 읽어오기 (API 호출 최소화)
existing_keys = target_ws.col_values(key_col_idx)[1:]
key_row_map = {key: idx + 2 for idx, key in enumerate(existing_keys)}

cells_to_update = []
rows_to_append = []

for _, row in df_source_all.iterrows():
    row_key = row['키값']
    row_values = row.fillna('').tolist()
    
    if row_key in key_row_map:
        # 이미 존재하는 데이터라면 기존 행 번호에 덮어쓰기 예약 (수정값 반영!)
        target_row_num = key_row_map[row_key]
        cells_to_update.append({
            'range': f'A{target_row_num}', 
            'values': [row_values]
        })
    else:
        # 신규 데이터라면 하단 추가 목록에 적재
        rows_to_append.append(row_values)

# 3. 안전한 일괄 반영 (Clear 없이 Update와 Append만 수행)
if cells_to_update:
    print(f"🔄 개별 시트에서 수정된 최근 데이터 {len(cells_to_update)}건 반영 중...")
    target_ws.batch_update(cells_to_update, value_input_option='USER_ENTERED')

if rows_to_append:
    print(f"➕ 신규 데이터 {len(rows_to_append)}건 하단 추가 중...")
    target_ws.append_rows(rows_to_append, value_input_option='USER_ENTERED')

print("✅ [Step 4] 데이터 유실 없이 최근 7일 치 수정 내역과 통합 DB 동기화가 완벽하게 완료되었습니다!")
