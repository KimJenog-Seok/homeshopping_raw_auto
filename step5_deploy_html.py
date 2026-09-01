import os
import json
import base64
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

print("🚀 [Step 5] HTML 대시보드 생성 및 드라이브 업로드 시작!")

# 1. 인증 세팅 (Step 4와 동일)
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

# 2. 통합 DB(Master) 데이터 읽어오기
TARGET_SHEET_ID = '106vkIpsodH2uRzb17hMrKz9uKW-BcM7VA4459-nySJ0'
gc = gspread.authorize(creds)

print("📖 통합 DB 시트 데이터 읽는 중...")
target_doc = gc.open_by_key(TARGET_SHEET_ID)
target_ws = target_doc.worksheet('RAW')
df = pd.DataFrame(target_ws.get_all_records(value_render_option='UNFORMATTED_VALUE'))

# 빈칸 등 데이터 정제 후 JSON 텍스트로 변환
df.fillna('', inplace=True)
db_json_data = df.to_json(orient='records', force_ascii=False)

# 3. 테스트용 임시 HTML 생성 
# (다음 스텝에서 이 부분을 진짜 v4.0 대시보드 코드로 싹 갈아끼울 겁니다!)
print("🛠️ HTML 파일 생성 중...")
html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>라방바 대시보드 자동화 테스트</title>
</head>
<body>
    <h1>🚀 대시보드 자동 업로드 테스트 성공!</h1>
    <p>통합 DB에서 <strong>{len(df)}개</strong>의 데이터를 성공적으로 불러와 심었습니다.</p>
    <p>이제 이 파일에 진짜 대시보드 코드만 덧씌우면 끝입니다!</p>
    <script>
        // 파이썬이 DB 데이터를 통째로 이 자리에 박아 넣었습니다.
        const rawData = {db_json_data};
        console.log("데이터 로드 성공!", rawData);
    </script>
</body>
</html>
"""

# 4. 구글 드라이브에 파일 업로드 (또는 덮어쓰기)
DRIVE_FOLDER_ID = '1EDN4y1K1_3icuoU8rBDXACowkxqd5Q5A'
print("☁️ 구글 드라이브로 파일 업로드 중...")
drive_service = build('drive', 'v3', credentials=creds)

# 텍스트 형태의 HTML을 파일 형태로 변환
media = MediaIoBaseUpload(io.BytesIO(html_content.encode('utf-8')), mimetype='text/html', resumable=True)

# 해당 폴더에 이미 'index.html' 파일이 존재하는지 검색
query = f"'{DRIVE_FOLDER_ID}' in parents and name='index.html' and trashed=false"
results = drive_service.files().list(q=query, fields="files(id, name)").execute()
items = results.get('files', [])

if not items:
    # 파일이 없으면 쌩얼로 새로 만들기
    file_metadata = {'name': 'index.html', 'parents': [DRIVE_FOLDER_ID]}
    file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    print(f"✅ 새 index.html 파일이 드라이브에 생성되었습니다. (ID: {file.get('id')})")
else:
    # 이미 파일이 존재하면 깔끔하게 덮어쓰기(Update)
    file_id = items[0]['id']
    file = drive_service.files().update(fileId=file_id, media_body=media).execute()
    print(f"✅ 기존 index.html 파일을 성공적으로 덮어썼습니다. (ID: {file_id})")

print("🎉 [Step 5] 구글 드라이브 업로드 프로세스 완벽 종료!")
