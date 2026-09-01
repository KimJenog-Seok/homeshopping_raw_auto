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

print("🚀 [Step 4] 통합 DB 업데이트 파이프라인 시작!")
gc = gs_client_from_env()

SOURCE_SHEET_ID = '1Aqwj1SkHsgAr08doidy_tfnHJNwgdFfNrMoMteoXMdE'
TARGET_SHEET_ID = '106vkIpsodH2uRzb17hMrKz9uKW-BcM7VA4459-nySJ0'

# 서버의 UTC 시간에 9시간을 더해 한국 시간(KST)으로 강제 고정
kst_now = datetime.utcnow() + timedelta(hours=9)
yesterday = kst_now - timedelta(days=1)
source_tab_name = yesterday.strftime('%y/%m/%d')

print(f"📖 원본 시트 [{source_tab_name}] 탭 데이터 읽는 중...")
try:
    source_doc = gc.open_by_key(SOURCE_SHEET_ID)
    source_ws = source_doc.worksheet(source_tab_name)
    
    # 📌 1. 눈에 보이는 텍스트 그대로 가져오기
    source_data_fmt = source_ws.get_all_records(value_render_option='FORMATTED_VALUE')
    # 📌 2. 서식을 벗겨낸 순수한 숫자 데이터 그대로 가져오기
    source_data_raw = source_ws.get_all_records(value_render_option='UNFORMATTED_VALUE')
except gspread.exceptions.WorksheetNotFound:
    print(f"❌ '{source_tab_name}' 탭을 찾을 수 없습니다. 프로세스를 종료합니다.")
    exit()

df_fmt = pd.DataFrame(source_data_fmt)
df_raw = pd.DataFrame(source_data_raw)

if df_fmt.empty:
    print("❌ 어제 날짜의 데이터가 없습니다. 프로세스를 종료합니다.")
    exit()

# 양쪽 헤더의 앞뒤 공백 제거
df_fmt.columns = df_fmt.columns.astype(str).str.strip()
df_raw.columns = df_raw.columns.astype(str).str.strip()

# 📌 [긴급 패치] AI분류 컬럼명 불일치 해결!
# 원본 시트의 'AI분류'를 통합 DB의 'AI분류(수정)'과 일치하도록 이름 변경
df_fmt.rename(columns={'AI분류': 'AI분류(수정)'}, inplace=True)
df_raw.rename(columns={'AI분류': 'AI분류(수정)'}, inplace=True)

# 📌 3. 미세한 소수점을 살려야 하는 숫자 컬럼들만 핀셋으로 교체!
numeric_cols = ['판매량', '매출액', '상품수', '매출액 환산수식', '환산가치', '분리송출고려환산가치', '주문효율 /h']
for col in numeric_cols:
    if col in df_fmt.columns and col in df_raw.columns:
        df_fmt[col] = df_raw[col]

df_source = df_fmt

formatted_date = pd.to_datetime(df_source['방송날짜']).dt.strftime('%Y-%m-%d')
df_source['키값'] = formatted_date + df_source['방송시작시간'].astype(str) + df_source['방송정보'].astype(str) + df_source['회사명'].astype(str)

print("🔍 Master DB 시트 접근 및 기존 '키값' 스캔 중...")
target_doc = gc.open_by_key(TARGET_SHEET_ID)
target_ws = target_doc.worksheet('RAW')
target_headers = target_ws.row_values(1)

if '키값' not in target_headers:
    print("❌ Master DB에 '키값' 컬럼이 없습니다. 확인해주세요!")
    exit()

target_headers = [str(col).strip() for col in target_headers]

key_col_idx = target_headers.index('키값') + 1
existing_keys = target_ws.col_values(key_col_idx)[1:]
key_row_map = {key: idx + 2 for idx, key in enumerate(existing_keys)}

for col in target_headers:
    if col not in df_source.columns:
        df_source[col] = ''
df_source = df_source[target_headers]

rows_to_append = []
cells_to_update = []

for index, row in df_source.iterrows():
    row_key = row['키값']
    row_values = row.fillna('').tolist()
    
    if row_key in key_row_map:
        target_row_num = key_row_map[row_key]
        cells_to_update.append({
            'range': f'A{target_row_num}', 
            'values': [row_values]
        })
    else:
        rows_to_append.append(row_values)

if cells_to_update:
    print(f"🔄 {len(cells_to_update)}개의 기존 데이터 업데이트 진행 중...")
    target_ws.batch_update(cells_to_update, value_input_option='USER_ENTERED')

if rows_to_append:
    print(f"➕ {len(rows_to_append)}개의 신규 데이터 하단 추가 진행 중...")
    target_ws.append_rows(rows_to_append, value_input_option='USER_ENTERED')

print("✅ [Step 4] 통합 DB 업데이트가 완벽하게 완료되었습니다!")
