#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, base64
from datetime import datetime, timedelta, timezone
import gspread
from google.oauth2.service_account import Credentials

# ===================== 설정 =====================
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1Aqwj1SkHsgAr08doidy_tfnHJNwgdFfNrMoMteoXMdE/edit"
WORKSHEET_NAME = "편성표RAW"

def gs_client_from_env():
    GSVC_JSON_B64 = os.environ.get("KEY1", "")
    svc_info = json.loads(base64.b64decode(GSVC_JSON_B64).decode("utf-8"))
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(svc_info, scopes=scope)
    return gspread.authorize(creds)

def make_yesterday_title_kst():
    KST = timezone(timedelta(hours=9))
    yday = datetime.now(KST).date() - timedelta(days=1)
    return yday.strftime("%y/%m/%d")

def unique_sheet_title(sh, base):
    title = base
    n = 1
    while True:
        try:
            sh.worksheet(title)
            n += 1
            title = f"{base}-{n}"
        except gspread.exceptions.WorksheetNotFound:
            return title

def apply_formatting(sh, ws, row_count, col_count=19):
    reqs = []
    
    # 1. 전체 테두리
    reqs.append({
        "updateBorders": {
            "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": row_count, "startColumnIndex": 0, "endColumnIndex": col_count},
            "top": {"style": "SOLID"}, "bottom": {"style": "SOLID"},
            "left": {"style": "SOLID"}, "right": {"style": "SOLID"},
            "innerHorizontal": {"style": "SOLID"}, "innerVertical": {"style": "SOLID"},
        }
    })

    # 2. 열 너비 설정
    reqs.append({
        "updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": col_count},
            "properties": {"pixelSize": 100}, "fields": "pixelSize"
        }
    })
    reqs.append({
        "updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 3}, # C열 (방송정보)
            "properties": {"pixelSize": 600}, "fields": "pixelSize"
        }
    })
    reqs.append({
        "updateDimensionProperties": {
            "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 7, "endIndex": 9}, # H, I열
            "properties": {"pixelSize": 130}, "fields": "pixelSize"
        }
    })
    for idx in [9, 16, 17, 18]: # J, Q, R, S열
        reqs.append({
            "updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": idx, "endIndex": idx+1},
                "properties": {"pixelSize": 160}, "fields": "pixelSize"
            }
        })

    # 3. 정렬 (기본 가운데, C열만 왼쪽)
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": row_count, "startColumnIndex": 0, "endColumnIndex": col_count},
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER"}},
            "fields": "userEnteredFormat.horizontalAlignment"
        }
    })
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": row_count, "startColumnIndex": 2, "endColumnIndex": 3},
            "cell": {"userEnteredFormat": {"horizontalAlignment": "LEFT"}},
            "fields": "userEnteredFormat.horizontalAlignment"
        }
    })

    # 4. 헤더 스타일 (회색 배경)
    reqs.append({
        "repeatCell": {
            "range": {"sheetId": ws.id, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": col_count},
            "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.8, "green": 0.8, "blue": 0.8}, "horizontalAlignment": "CENTER", "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)"
        }
    })

    # 5. 숫자 포맷 적용
    def number_format(col_idx, pattern):
        return {
            "repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": row_count, "startColumnIndex": col_idx, "endColumnIndex": col_idx+1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": pattern}}},
                "fields": "userEnteredFormat.numberFormat"
            }
        }
    
    reqs.append(number_format(9, "#,##0"))      # J열 (매출액 환산수식)
    reqs.append(number_format(17, "#,##0"))     # R열 (주문효율 /h)
    reqs.append(number_format(12, "#,##0.00"))  # M열 (환산가치)
    reqs.append(number_format(16, "#,##0.00"))  # Q열 (분리송출고려환산가치)

    sh.batch_update({"requests": reqs})
    print(f"✅ 서식 적용 완료 ({row_count}행)")

def main():
    try:
        gc = gs_client_from_env()
        sh = gc.open_by_url(SPREADSHEET_URL)
        ws_raw = sh.worksheet(WORKSHEET_NAME)
        
        # 1. RAW 데이터 가져오기 및 정렬 (회사명, 방송시작시간 기준)
        all_data = ws_raw.get_all_values()
        if len(all_data) < 2:
            print("처리할 데이터가 없습니다.")
            return
            
        header = all_data[0]
        rows = all_data[1:]
        
        # 회사명(index 7), 방송시작시간(index 1) 기준으로 정렬
        rows.sort(key=lambda x: (x[7] if len(x) > 7 else "", x[1] if len(x) > 1 else ""))
        final_data = [header] + rows

        # 2. 어제 날짜 시트 생성
        base_title = make_yesterday_title_kst()
        backup_title = unique_sheet_title(sh, base_title)
        
        rows_cnt = max(2, len(final_data))
        cols_cnt = max(19, len(header))
        
        ws_bu = sh.add_worksheet(title=backup_title, rows=rows_cnt, cols=cols_cnt)
        ws_bu.update(range_name="A1", values=final_data)
        print(f"✅ 백업 시트 데이터 복사 완료 → {backup_title}")

        # 3. 서식 적용
        apply_formatting(sh, ws_bu, rows_cnt, cols_cnt)

        # 4. 시트 순서 재배치 (방금 만든 시트를 무조건 맨 앞으로)
        all_ws = sh.worksheets()
        new_order = [ws_bu] + [w for w in all_ws if w.id != ws_bu.id]
        sh.reorder_worksheets(new_order)
        print(f"📌 {backup_title} 시트를 맨 앞으로 이동 완료")

        # 5. 숫자 포맷 적용 (정석님 요청 반영)
        def number_format(col_idx, pattern):
        return {
            "repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": row_count, "startColumnIndex": col_idx, "endColumnIndex": col_idx+1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": pattern}}},
                "fields": "userEnteredFormat.numberFormat"
            }
        }
    
        reqs.append(number_format(9, "#,##0"))      # J열: 매출액 (인덱스 9)
        reqs.append(number_format(12, "0.0"))       # M열: 환산가치 (인덱스 12, 소수점 1자리)
        reqs.append(number_format(16, "#,##0.00"))  # Q열 (기존 유지)
        reqs.append(number_format(17, "#,##0"))     # R열 (기존 유지)

        print("🎉 3단계: 포맷팅 및 시트 생성 완벽 종료!")

    except Exception as e:
        import traceback
        print(f"❌ 오류 발생: {e}")
        print(traceback.format_exc())

if __name__ == "__main__":
    main()
