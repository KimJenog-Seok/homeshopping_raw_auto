import os
import json
import base64
import gspread
import pandas as pd
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------
# 1. 인증 함수 정의 (Base64 인코딩된 환경변수 KEY1 사용)
# ---------------------------------------------------------
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

# ---------------------------------------------------------
# 2. 시트 기본 설정 및 인증
# ---------------------------------------------------------
print("🚀 [Step 4] 통합 DB 업데이트 파이프라인 시작!")
gc = gs_client_from_env()

# 시트 ID 설정
SOURCE_SHEET_ID = '1Aqwj1SkHsgAr08doidy_tfnHJNwgdFfNrMoMteoXMdE'
TARGET_SHEET_ID = '106vkIpsodH2uRzb17hMrKz9uKW-BcM7VA4459-nySJ0'

# 어제 날짜 계산 (소스 시트 탭 이름 찾기용, 예: '26/08/31')
yesterday = datetime.now() - timedelta(days=1)
source_tab_name = yesterday.strftime('%y/%m/%d')
key_date_str = yesterday.strftime('%Y-%m-%d') # 키값 생성용 텍스트 날짜

# ---------------------------------------------------------
# 3. 데이터 읽기 및 '키값' 동적 생성 (원본 무결성 보존)
# ---------------------------------------------------------
print(f"📖 원본 시트 [{source_tab_name}] 탭 데이터 읽는 중...")
try:
    source_doc = gc.open_by_key(SOURCE_SHEET_ID)
    source_ws = source_doc.worksheet(source_tab_name)
    source_data = source_ws.get_all_records()
except gspread.exceptions.WorksheetNotFound:
    print(f"❌ '{source_tab_name}' 탭을 찾을 수 없습니다. 프로세스를 종료합니다.")
    exit()

df_source = pd.DataFrame(source_data)

if df_source.empty:
    print("❌ 어제 날짜의 데이터가 없습니다. 프로세스를 종료합니다.")
    exit()

# 파이썬 메모리 상에서 동적 '키값' 생성 (수식 없이 순수 텍스트로!)
# 구조: yyyy-mm-dd + 방송시작시간 + 상품명 + 회사명
df_source['키값'] = key_date_str + df_source['방송시작시간'].astype(str) + df_source['상품명'].astype(str) + df_source['회사명'].astype(str)

# ---------------------------------------------------------
# 4. 통합 DB(Master) 접근 및 기존 키값 스캔
# ---------------------------------------------------------
print("🔍 Master DB 시트 접근 및 기존 '키값' 스캔 중...")
target_doc = gc.open_by_key(TARGET_SHEET_ID)
target_ws = target_doc.worksheet('RAW')

# 타겟 시트의 헤더(1행)와 키값 열만 빠르게 추출
target_headers = target_ws.row_values(1)
if '키값' not in target_headers:
    print("❌ Master DB에 '키값' 컬럼이 없습니다. 확인해주세요!")
    exit()

key_col_idx = target_headers.index('키값') + 1
# 2행부터 있는 기존 키값들을 딕셔너리로 매핑 (키값: 시트 행 번호)
existing_keys = target_ws.col_values(key_col_idx)[1:]
key_row_map = {key: idx + 2 for idx, key in enumerate(existing_keys)}

# 타겟 시트의 헤더 순서대로 소스 데이터프레임 열 재배열 (누락된 열은 빈 문자열로)
for col in target_headers:
    if col not in df_source.columns:
        df_source[col] = ''
df_source = df_source[target_headers]

# ---------------------------------------------------------
# 5. 데이터 대조 및 Upsert (업데이트 & 어펜드) 분리
# ---------------------------------------------------------
rows_to_append = []
cells_to_update = []

for index, row in df_source.iterrows():
    row_key = row['키값']
    row_values = row.fillna('').tolist()
    
    if row_key in key_row_map:
        # 📌 이미 존재하는 데이터 -> 값이 변경되었을 수 있으므로 덮어쓰기(Update) 준비
        target_row_num = key_row_map[row_key]
        cells_to_update.append({
            'range': f'A{target_row_num}', 
            'values': [row_values]
        })
    else:
        # 📌 처음 보는 데이터 -> 하단에 추가(Append) 준비
        rows_to_append.append(row_values)

# ---------------------------------------------------------
# 6. 구글 시트에 일괄 반영 (API 호출 최소화)
# ---------------------------------------------------------
if cells_to_update:
    print(f"🔄 {len(cells_to_update)}개의 기존 데이터 업데이트 진행 중...")
    target_ws.batch_update(cells_to_update, value_input_option='USER_ENTERED')

if rows_to_append:
    print(f"➕ {len(rows_to_append)}개의 신규 데이터 하단 추가 진행 중...")
    target_ws.append_rows(rows_to_append, value_input_option='USER_ENTERED')

print("✅ [Step 4] 통합 DB 업데이트가 완벽하게 완료되었습니다!")
