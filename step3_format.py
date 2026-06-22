#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, base64
from datetime import datetime, timedelta, timezone
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1Aqwj1SkHsgAr08doidy_tfnHJNwgdFfNrMoMteoXMdE/edit"
WORKSHEET_NAME = "편성표RAW"

def gs_client_from_env():
    GSVC_JSON_B64 = os.environ.get("KEY1", "")
    svc_info = json.loads(base64.b64decode(GSVC_JSON_B64).decode("utf-8"))
    creds = Credentials.from_service_account_info(svc_info, scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds)

# 💡 중복 시트 방지 안전장치 복구
def unique_sheet_title(sh, base):
    title = base
    n = 1
    while True:
        try:
            sh.worksheet(title)
            title = f"{base}-{n}"
            n += 1
        except gspread.exceptions.WorksheetNotFound:
            return title

def apply_formatting(sh, ws, row_count, col_count=19):
    reqs = []
    def get_fmt(col_idx, pattern):
        return {
            "repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": row_count, "startColumnIndex": col_idx, "endColumnIndex": col_idx+1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": pattern}}},
                "fields": "userEnteredFormat.numberFormat"
            }
        }
    
    reqs.append(get_fmt(9, "#,##0"))      # J열 매출액 콤마 적용
    reqs.append(get_fmt(12, "0.0"))       # M열 환산가치 소수점 1자리 적용
    
    sh.batch_update({"requests": reqs})

def main():
    gc = gs_client_from_env()
    sh = gc.open_by_url(SPREADSHEET_URL)
    ws_raw = sh.worksheet(WORKSHEET_NAME)
    
    all_data = ws_raw.get_all_values()
    header = all_data[0]
    rows = all_data[1:]
    # 회사명, 방송시작시간 기준 정렬
    rows.sort(key=lambda x: (x[7] if len(x) > 7 else "", x[1] if len(x) > 1 else ""))
    
    KST = timezone(timedelta(hours=9))
    base_title = (datetime.now(KST).date() - timedelta(days=1)).strftime("%y/%m/%d")
    
    safe_title = unique_sheet_title(sh, base_title)
    
    ws_bu = sh.add_worksheet(title=safe_title, rows=len(rows)+1, cols=len(header))
    ws_bu.update(range_name="A1", values=[header] + rows)
    
    # 💡 최신 데이터 시트를 맨 앞으로(가장 좌측으로) 이동
    all_ws = sh.worksheets()
    new_order = [ws_bu] + [w for w in all_ws if w.id != ws_bu.id]
    sh.reorder_worksheets(new_order)
    
    # 숫자 서식 적용
    apply_formatting(sh, ws_bu, len(rows)+1)
    print("🎉 포맷팅 및 시트 생성 성공")

if __name__ == "__main__":
    main()
