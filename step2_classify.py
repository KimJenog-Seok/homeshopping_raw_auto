#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, base64, re
from concurrent.futures import ThreadPoolExecutor
import gspread
from google.oauth2.service_account import Credentials
from openai import OpenAI

# ===================== 설정 =====================
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1Aqwj1SkHsgAr08doidy_tfnHJNwgdFfNrMoMteoXMdE/edit"
WORKSHEET_NAME = "편성표RAW"

# 정석님이 작성하신 완벽한 프롬프트
SYSTEM_PROMPT = """
### 🎯 역할 및 목표 (ROLE & GOAL)
- 역할: 나는 `홈쇼핑 카테고리 구분 챗봇`이다.
  외부 홈쇼핑 편성 데이터를 내부 기준 **‘카테고리 기본 구분(14종)’** 으로 자동 분류한다.
- 최종 목표: 입력 데이터에 `CAT구분` 값을 채워넣는다.
  모든 레코드는 반드시 14개 기준 중 하나의 카테고리명으로 결정되어야 한다.
  카테고리: 금융, 렌탈서비스, 여행, 여성의류, 공용의류, 레포츠의류, 패션잡화, 언더웨어, 쥬얼리, 리빙, 가전, 일반식품, 건강식품, 뷰티 
  결과는 카테고리명만 반환한다.

### 💡 핵심 제약 (CRUCIAL CONSTRAINTS)
- `CAT구분`은 14개 표준 라벨 중 하나여야 한다.
- 빈값, 기타, 미정은 절대 금지.
- 다음 단어가 포함 된 경우 강제로 카테고리 부여:
  코지마 = 리빙
  헤스티지 = 공용의류
  비버리힐즈폴로클럽 = 레포츠의류
  보람피플 = 렌탈서비스

### 💬 출력 형식 (OUTPUT FORMAT)
[카테고리명만 반환, 예: 여성의류]
- 문장, 근거, 이유, 설명, 따옴표, 접두사, 마크다운 등 어떤 것도 포함하지 않는다.
"""

def get_reference_data():
    files = [
        "정제_샘플.txt", "정제_NEW_키워드.txt",
        "충돌키워드_우선분류표.txt", "충돌_키워드리스트.txt", "정규화_매핑표.txt"
    ]
    ref_text = "\n\n### 📚 지식 정보 (KNOWLEDGE & REFERENCE DATA) ###\n"
    for fn in files:
        if os.path.exists(fn):
            with open(fn, "r", encoding="utf-8") as f:
                ref_text += f"\n--- [{fn}] ---\n" + f.read()
    return ref_text

def classify_one_row(client, title, base, full_prompt):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": full_prompt},
                {"role": "user", "content": f"방송정보: {title}\n분류: {base}"}
            ],
            temperature=0.0
        )
        result = response.choices[0].message.content.strip()
        result = re.sub(r"[`´'\"]+", "", result).strip()
        result = re.split(r"[—\-–]", result)[-1].strip()
        return result.splitlines()[0].strip()
    except Exception as e:
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
    full_prompt = SYSTEM_PROMPT + get_reference_data()

    gc = gs_client_from_env()
    sh = gc.open_by_url(SPREADSHEET_URL)
    ws = sh.worksheet(WORKSHEET_NAME)
    
    rows = ws.get_all_values()
    if len(rows) < 2:
        print("데이터가 없습니다.")
        return

    header = rows[0]
    data = rows[1:21]
    
    # AI분류가 이미 채워져 있는지 확인 후, 비어있는 경우만 분류
    ai_col_index = header.index("AI분류") if "AI분류" in header else 18
    
    print(f"총 {len(data)}행 분류 시작...")
    results = [""] * len(data)
    tasks = []

    with ThreadPoolExecutor(max_workers=5) as executor:
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
    print("🎯 2단계: AI 카테고리 병렬 분류 완료!")

if __name__ == "__main__":
    main()
