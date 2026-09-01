import os
import json
import base64
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

print("🚀 [Step 5] 진짜 대시보드 HTML 생성 및 드라이브 업로드 시작!")

# 1. 인증 세팅
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

# 빈칸 정제 후 JSON 텍스트로 변환
df.fillna('', inplace=True)
db_json_data = df.to_json(orient='records', force_ascii=False)

# 3. 진짜 v4.0 대시보드 HTML 생성 (DB 데이터가 주입됨)
print("🛠️ 진짜 v4.0 대시보드 HTML 조립 중...")
html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>라방바 홈쇼핑 통합 대시보드</title>
    <!-- 구글 폰트 및 스타일 CDN -->
    <link href="https://fonts.googleapis.com/css2?family=Pretendard:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        body {{ font-family: 'Pretendard', sans-serif; background-color: #f8f9fa; margin: 0; padding: 20px; color: #333; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }}
        h1 {{ font-size: 24px; color: #1a73e8; margin: 0; }}
        .card {{ background: white; padding: 20px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); margin-bottom: 20px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }}
        th, td {{ padding: 12px 10px; border-bottom: 1px solid #eee; text-align: left; }}
        th {{ background-color: #f1f3f4; font-weight: 600; }}
        .badge {{ background: #e8f0fe; color: #1a73e8; padding: 4px 8px; border-radius: 4px; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>📊 라방바 홈쇼핑 데이터 통합 대시보드</h1>
            <span class="badge" id="total-count">데이터 로딩 중...</span>
        </header>
        
        <div class="card">
            <h3>📈 실시간 통합 DB 현황 (최신 데이터 100건 미리보기)</h3>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>방송날짜</th>
                            <th>시간대</th>
                            <th>방송정보</th>
                            <th>회사명</th>
                            <th>분류</th>
                            <th>매출액</th>
                        </tr>
                    </thead>
                    <tbody id="data-table-body">
                        <!-- 자바스크립트로 데이터가 렌더링됩니다 -->
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        // 📌 파이썬이 쏘아 올린 거대한 DB JSON 데이터
        const dbData = {db_json_data};
        
        console.log("총 데이터 건수:", dbData.length);
        document.getElementById('total-count').innerText = `총 데이터: ${{dbData.length.toLocaleString()}}건`;

        // 테이블에 데이터 뿌려주기 (최근 데이터 역순으로 100건만 렌더링)
        const tableBody = document.getElementById('data-table-body');
        const recentData = [...dbData].reverse().slice(0, 100);

        recentData.forEach(row => {{
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td>${{row['방송날짜'] || ''}}</td>
                <td>${{row['시간대'] || ''}}</td>
                <td><strong>${{row['방송정보'] || ''}}</strong></td>
                <td>${{row['회사명'] || ''}}</td>
                <td><span class="badge">${{row['분류'] || ''}}</span></td>
                <td>${{row['매출액'] || ''}}</td>
            `;
            tableBody.appendChild(tr);
        }});
    </script>
</body>
</html>
"""

# 4. 구글 드라이브에 있는 기존 'index.html'에 깔끔하게 덮어쓰기
DRIVE_FOLDER_ID = '1EDN4y1K1_3icuoU8rBDXACowkxqd5Q5A'
print("☁️ 구글 드라이브로 진짜 대시보드 업로드 중...")
drive_service = build('drive', 'v3', credentials=creds)

media = MediaIoBaseUpload(io.BytesIO(html_content.encode('utf-8')), mimetype='text/html', resumable=True)

# 기존 index.html 파일 검색
query = f"'{DRIVE_FOLDER_ID}' in parents and name='index.html' and trashed=false"
results = drive_service.files().list(q=query, fields="files(id, name)").execute()
items = results.get('files', [])

if not items:
    # 혹시라도 파일이 없으면 새로 생성
    file_metadata = {'name': 'index.html', 'parents': [DRIVE_FOLDER_ID]}
    file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    print(f"✅ 새 index.html 생성 완료 (ID: {file.get('id')})")
else:
    # 기존 파일에 덮어쓰기
    file_id = items[0]['id']
    file = drive_service.files().update(fileId=file_id, media_body=media).execute()
    print(f"✅ 진짜 대시보드로 index.html 덮어쓰기 완료! (ID: {file_id})")

print("🎉 [Step 5] 대시보드 HTML 배포 완벽 종료!")
