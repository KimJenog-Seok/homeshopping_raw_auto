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

def apply_formatting(sh, ws, row_count, col_count=19):
    # 포맷팅 요청들
    reqs = []
    # 숫자 포맷 정의
    def get_fmt(col_idx, pattern):
        return {
            "repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": row_count, "startColumnIndex": col_idx, "endColumnIndex": col_idx+1},
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": pattern}}},
                "fields": "userEnteredFormat.numberFormat"
            }
        }
    
    reqs.append(get_fmt(9, "#,##0"))      # J열 매출액 (인덱스 9)
    reqs.append(get_fmt(12, "0.0"))       # M열 환산가치 (인덱스 12)
    
    sh.batch_update({"requests": reqs})

def main():
    gc = gs_client_from_env()
    sh = gc.open_by_url(SPREADSHEET_URL)
    ws_raw = sh.worksheet(WORKSHEET_NAME)
    
    all_data = ws_raw.get_all_values()
    header = all_data[0]
    rows = all_data[1:]
    rows.sort(key=lambda x: (x[7] if len(x) > 7 else "", x[1] if len(x) > 1 else ""))
    
    KST = timezone(timedelta(hours=9))
    base_title = (datetime.now(KST).date() - timedelta(days=1)).strftime("%y/%m/%d")
    
    ws_bu = sh.add_worksheet(title=base_title, rows=len(rows)+1, cols=len(header))
    ws_bu.update(range_name="A1", values=[header] + rows)
    
    # 시트 이동
    all_ws = sh.worksheets()
    sh.reorder_worksheets([ws_bu] + [w for w in all_ws if w.id != ws_bu.id])
    
    # 서식 적용
    apply_formatting(sh, ws_bu, len(rows)+1)
    print("성공")

if __name__ == "__main__":
    main()
