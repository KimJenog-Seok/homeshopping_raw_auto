#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, base64, re, time
from concurrent.futures import ThreadPoolExecutor
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI

# ===================== 설정 =====================
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1Aqwj1SkHsgAr08doidy_tfnHJNwgdFfNrMoMteoXMdE/edit"
WORKSHEET_NAME = "편성표RAW"

# 💡 최적화된 프롬프트: 불필요한 샘플 매칭을 없애고 충돌 규칙을 최우선으로 적용
SYSTEM_PROMPT = """
### 🎯 역할 및 목표
- 당신은 홈쇼핑 상품 데이터를 14개 기본 카테고리로 정확히 분류하는 AI입니다.
- 카테고리: 금융, 렌탈서비스, 여행, 여성의류, 공용의류, 레포츠의류, 패션잡화, 언더웨어, 쥬얼리, 리빙, 가전, 일반식품, 건강식품, 뷰티
- 결과는 오직 14개 중 하나의 카테고리명만 반환해야 합니다. 빈칸, 기타, 미정은 절대 금지됩니다.

### 💡 핵심 제약 및 분류 절차
1. 제공된 [지식 정보]의 '충돌키워드_우선분류표'와 '정제_NEW_키워드'를 가장 최우선으로 검토합니다.
2. 만약 해당 상품이 공용의류/레포츠의류 등 충돌 키워드를 포함하고 있다면, 반드시 우선분류표의 기준을 따릅니다.
3. 다음 특정 브랜드가 포함된 경우 강제로 카테고리를 부여합니다:
   - 코지마 = 리빙
   - 헤스티지 = 공용의류
   - 비버리힐즈폴로클럽 = 레포츠의류
   - 보람피플 = 렌탈서비스
   - 레드닥터_의료기기 = 패션잡화
4. 지식 정보에 명시된 규칙이 없다면, 상품의 기능과 용도를 파악하여 14개 중 가장 적합한 카테고리를 하나 선택합니다.

### 💬 출력 형식
- [카테고리명만 반환, 예: 여성의류]
- 문장, 이유, 설명 등 부가적인 텍스트는 절대 출력하지 마세요.
"""

def get_optimized_reference_data():
    # 💡 학습파일 정체 10만토큰 언더로 유지할것.
    core_files = [
        "정제_샘플_축약_10만토큰이하_26년 7월 13일.csv",
        "정제_NEW_키워드.csv",
        "충돌키워드_우선분류표.csv", 
        "충돌_키워드리스트.csv"
    ]
    ref_text = "\n\n### 📚 지식 정보 (KNOWLEDGE & REFERENCE DATA) ###\n"
    for fn in core_files:
        if os.path.exists(fn):
            with open(fn, "r", encoding="utf-8-sig") as f:
                ref_text += f"\n--- [{fn}] ---\n" + f.read()
    return ref_text

def classify_one_row(client, title, base, full_prompt, max_retries=3):
    # 💡 RateLimitError 방어를 위한 재시도(Retry) 로직 도입
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": full_prompt},
                    {"role": "user", "content": f"방송정보: {title}\n분류: {base}"}
                ],
                temperature=0.4
            )
            result = response.choices[0].message.content.strip()
            result = re.sub(r"[`´'\"]+", "", result).strip()
            result = re.split(r"[—\-–]", result)[-1].strip()
            return result.splitlines()[0].strip()
            
        except Exception as e:
            error_name = type(e).__name__
            print(f"⚠️ [경고/재시도 {attempt+1}/{max_retries}]: {error_name} - {e}")
            
            # 마지막 시도가 아니라면 5초 대기 후 재시도
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                print(f"❌ [최종 에러 상세 내용]: {error_name} - {e}")
                return f"분류오류"

def gs_client_from_env():
    GSVC_JSON_B64 = os.environ.get("KEY1", "")
    svc_info = json.loads(base64.b64decode(GSVC_JSON_B64).decode("utf-8"))
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(svc_info, scopes=scope)
    return gspread.authorize(creds)

def main():
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    if not OPENAI_API_KEY:
        raise RuntimeError("❌ OPENAI_API_KEY가 없습니다.")

    client = OpenAI(api_key=OPENAI_API_KEY)
    
    # 압축된 핵심 데이터만 프롬프트에 결합
    full_prompt = SYSTEM_PROMPT + get_optimized_reference_data()

    gc = gs_client_from_env()
    sh = gc.open_by_url(SPREADSHEET_URL)
    ws = sh.worksheet(WORKSHEET_NAME)
    
    rows = ws.get_all_values()
    if len(rows) < 2:
        print("데이터가 없습니다.")
        return

    header = rows[0]
    
    # 💡 전체 행 진행
    data = rows[1:] 
    
    print(f"총 {len(data)}행 분류 시작...")
    results = [""] * len(data)
    tasks = []

    # 💡 RateLimit 초과를 막기 위해 워커 수를 5에서 2로 조정
    with ThreadPoolExecutor(max_workers=2) as executor:
        for idx, row in enumerate(data):
            title = row[2] if len(row) > 2 else ""
            base = row[3] if len(row) > 3 else ""
            
            tasks.append((idx, executor.submit(classify_one_row, client, title, base, full_prompt)))

        for idx, future in tasks:
            results[idx] = future.result()
            print(f"✅ 완료 ← 행 {idx+2}") 

    update_range = f"S2:S{len(data)+1}"
    update_values = [[r] for r in results] 
    
    ws.update(range_name=update_range, values=update_values)
    print("🎯 2단계: AI 카테고리 병렬 분류 완료 (전체 행 처리 완료)!")

if __name__ == "__main__":
    main()
