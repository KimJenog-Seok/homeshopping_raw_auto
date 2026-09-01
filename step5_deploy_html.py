import os
import json
import base64
import gspread
import pandas as pd
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

print("🚀 [Step 5] 정석님의 찐 v4.0 대시보드 HTML 배포 시작!")

# ---------------------------------------------------------
# 1. 설정 및 구글 API 인증
# ---------------------------------------------------------
TARGET_CATEGORIES = [
    '여성의류', '공용의류', '레포츠의류', '패션잡화', '쥬얼리', '언더웨어',
    '건강식품', '뷰티', '가전', '리빙', '일반식품', '금융', '렌탈서비스', '여행'
]
GROUP_FASHION = ['여성의류', '공용의류', '레포츠의류']
GROUP_INTANGIBLE = ['금융', '렌탈서비스', '여행']

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

# ---------------------------------------------------------
# 2. 통합 DB(Master) 데이터 로드 및 전처리 (투트랙 스캔)
# ---------------------------------------------------------
TARGET_SHEET_ID = '106vkIpsodH2uRzb17hMrKz9uKW-BcM7VA4459-nySJ0'
gc = gspread.authorize(creds)

print("📖 통합 DB 시트 데이터 읽는 중... (날짜와 소수점 모두 보호!)")
target_doc = gc.open_by_key(TARGET_SHEET_ID)
target_ws = target_doc.worksheet('RAW')

# 날짜/문자는 보이는 대로(FORMATTED), 숫자는 날것으로(UNFORMATTED) 긁어옵니다.
df_fmt = pd.DataFrame(target_ws.get_all_records(value_render_option='FORMATTED_VALUE'))
df_raw = pd.DataFrame(target_ws.get_all_records(value_render_option='UNFORMATTED_VALUE'))

df_fmt.columns = df_fmt.columns.astype(str).str.strip()
df_raw.columns = df_raw.columns.astype(str).str.strip()

# 숫자형 컬럼만 소수점이 살아있는 raw 데이터로 교체
numeric_cols_target = ['판매량', '매출액', '상품수', '매출액 환산수식', '환산가치', '분리송출고려환산가치', '주문효율 /h']
for col in numeric_cols_target:
    if col in df_fmt.columns and col in df_raw.columns:
        df_fmt[col] = df_raw[col]

df = df_fmt
df = df.dropna(subset=['방송날짜'])

# [정석님 기존 데이터 전처리 로직 100% 적용]
df['방송날짜'] = pd.to_datetime(df['방송날짜'], errors='coerce')
df['방송날짜_str'] = df['방송날짜'].dt.strftime('%Y-%m-%d')

def get_weekly_label(date_obj):
    start = date_obj - timedelta(days=date_obj.weekday())
    return f"{start.month}/{start.day}주"

df['주차'] = df['방송날짜'].apply(get_weekly_label)
df['주차_시작일'] = df['방송날짜'].apply(lambda x: (x - timedelta(days=x.weekday())).strftime('%Y-%m-%d'))

if '회사명' in df.columns:
    df['회사명'] = df['회사명'].astype(str).str.strip()
    name_map = {'신세계': '신세계쇼핑', 'KT알파': 'KT알파쇼핑'}
    df['회사명'] = df['회사명'].replace(name_map)
    df = df[~df['회사명'].isin(['Live', 'LIVE', 'live'])]

col_cat = 'AI분류(수정)' if 'AI분류(수정)' in df.columns else (df.columns[18] if len(df.columns) > 18 else df.columns[-1])
col_time = '분리송출고려환산가치' if '분리송출고려환산가치' in df.columns else (df.columns[16] if len(df.columns) > 16 else None)
df['카테고리'] = df[col_cat].fillna('기타').astype(str).str.strip()
df['가치시간'] = pd.to_numeric(df[col_time], errors='coerce').fillna(0) if col_time and col_time in df.columns else 0
df = df[df['카테고리'].isin(TARGET_CATEGORIES)]

rename_map = {'방송시작시간': '방송시작시간', '방송정보': '상품명', '판매량': '판매량', '회사명': '회사명', '홈쇼핑구분': '홈쇼핑구분', '매출액 환산수식': '주문금액', '주문효율 /h': '주문효율'}
for k, v in rename_map.items():
    if k in df.columns:
        df = df.rename(columns={k: v})
    elif v not in df.columns:
        df[v] = 0

final_cols = ['방송날짜_str', '주차', '주차_시작일', '방송시작시간', '상품명', '카테고리', '판매량', '회사명', '홈쇼핑구분', '주문금액', '가치시간', '주문효율']
numeric_cols = ['판매량', '주문금액', '가치시간', '주문효율']
for col in numeric_cols:
    if col in df.columns: df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
if '홈쇼핑구분' in df.columns: df['홈쇼핑구분'] = df['홈쇼핑구분'].fillna('').astype(str)

df = df[[c for c in final_cols if c in df.columns]]

# ---------------------------------------------------------
# 3. HTML 생성 (v4.0 수정본 - 일평균 전환 + 편성표 필터 개선)
# ---------------------------------------------------------
print("🎨 홈쇼핑 주간 실적 현황(v4.0_일평균전환) 생성 중...")
data_json = df.to_json(orient='records', force_ascii=False)
companies = sorted(df['회사명'].unique().tolist())
priority = ['쇼핑엔티', '신세계쇼핑', 'SK스토아', 'KT알파쇼핑']
sorted_comps = [c for c in priority if c in companies] + [c for c in companies if c not in priority]
weeks = df[['주차_시작일', '주차']].drop_duplicates().sort_values('주차_시작일').values.tolist()
default_date = df['방송날짜_str'].max() if not df.empty else datetime.now().strftime('%Y-%m-%d')
gubun_options = sorted([g for g in df['홈쇼핑구분'].unique().tolist() if g]) if '홈쇼핑구분' in df.columns else ['TC', 'LIVE']

html_content = f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>홈쇼핑 주간 실적 현황 v4.0</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.4/js/jquery.dataTables.min.js"></script>
    <link href="https://cdn.datatables.net/1.13.4/css/dataTables.bootstrap5.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <style>
        body {{ background-color: #f4f6f8; font-family: 'Pretendard', sans-serif; font-size: 0.9rem; }}
        .header {{ background: #212529; color: white; padding: 15px 25px; display: flex; justify-content: space-between; align-items: center; }}
        .card-box {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }}
        .section-title {{ font-size: 1.1rem; font-weight: bold; border-left: 5px solid #1a237e; padding-left: 10px; margin-bottom: 15px; color: #212529; }}
        .section-sub {{ font-size: 0.75rem; color: #888; margin-left: 10px; }}
        th, td {{ vertical-align: middle; text-align: center; border: 1px solid #dee2e6; }}
        .trend-table {{ table-layout: fixed; width: 100%; }}
        .trend-table th, .trend-table td {{ font-size: 0.75rem; padding: 4px 2px; }}
        .trend-table th {{ background-color: #f8f9fa; }}
        .comp-table td {{ padding: 8px 4px; font-size: 0.85rem; }}
        .row-subtotal td {{ background-color: #e3f2fd; font-weight: bold; }}
        .row-grandtotal td {{ background-color: #212529; color: white; font-weight: bold; }}
        .text-win {{ color: #0d6efd; font-weight: bold; }}
        .text-lose {{ color: #dc3545; font-weight: bold; }}
        .diff-val {{ font-size: 0.75rem; color: #666; font-weight: normal; margin-left: 2px; }}
        .dataTables_length, .dataTables_filter {{ display: none !important; }}
        .text-truncate-custom {{ white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 450px; display: block; }}
        .total-row td {{ background-color: #495057 !important; color: white !important; font-weight: bold; }}
        #scheduleCompMenu .dropdown-item {{ cursor: pointer; }}
        #scheduleCompMenu label {{ cursor: pointer; width: 100%; }}
        #scheduleCompBtn {{ text-align: left; }}
    </style>
</head>
<body>
<div class="header">
    <h4 class="m-0 fw-bold">홈쇼핑 주간 실적 현황</h4>
    <span class="badge bg-primary">v4.0 일평균 기준 전환</span>
</div>
<div class="container-fluid mt-3 px-3">
    <div class="card-box">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <div>
                <span class="section-title m-0">주간 실적 트렌드 (최근 12주)</span>
                <span class="section-sub">※ 주문액은 실제 방송일수 기준 일평균, 주문효율은 총주문액÷총가치시간</span>
            </div>
            <div class="d-flex gap-2">
                <div class="btn-group btn-group-sm">
                    <input type="radio" class="btn-check" name="trendMode" id="trendSales" value="sales" onchange="renderTrendSection()">
                    <label class="btn btn-outline-dark" for="trendSales">일평균 주문액</label>
                    <input type="radio" class="btn-check" name="trendMode" id="trendEff" value="eff" checked onchange="renderTrendSection()">
                    <label class="btn btn-outline-dark" for="trendEff">주문효율</label>
                </div>
                <select id="trendWeekSelect" class="form-select form-select-sm" style="width: 180px;" onchange="renderTrendSection()"></select>
            </div>
        </div>
        <div class="table-responsive mb-3">
            <table class="table trend-table mb-0" id="trendTable">
                <thead><tr id="trendHeader"></tr></thead>
                <tbody></tbody>
            </table>
        </div>
        <div id="trendChart" style="height: 300px;"></div>
    </div>

    <div class="card-box">
        <div class="d-flex justify-content-between align-items-center mb-3">
            <div>
                <span class="section-title m-0">당사 vs 경쟁사 비교 분석</span>
                <span class="section-sub">※ 주문금액은 실제 방송일수 기준 일평균</span>
            </div>
            <div class="d-flex gap-2">
                <div class="btn-group btn-group-sm">
                    <input type="radio" class="btn-check" name="viewMode" id="modeSales" value="sales" onchange="renderCompTable()">
                    <label class="btn btn-outline-primary" for="modeSales">일평균 주문금액</label>
                    <input type="radio" class="btn-check" name="viewMode" id="modePerf" value="perf" checked onchange="renderCompTable()">
                    <label class="btn btn-outline-primary" for="modePerf">주문효율</label>
                    <input type="radio" class="btn-check" name="viewMode" id="modeShare" value="share" onchange="renderCompTable()">
                    <label class="btn btn-outline-primary" for="modeShare">편성비중</label>
                    <input type="radio" class="btn-check" name="viewMode" id="modeCompEff" value="compEff" onchange="renderCompTable()">
                    <label class="btn btn-outline-danger" for="modeCompEff">타사대비효율</label>
                </div>
                <select id="weekSelect" class="form-select form-select-sm" style="width: 180px;" onchange="renderCompTable()"></select>
                <select id="extraComp" class="form-select form-select-sm" style="width: 150px;" onchange="renderCompTable()"><option value="">+ 타사 선택</option></select>
            </div>
        </div>
        <div class="table-responsive">
            <table class="table comp-table mb-0" id="compTable">
                <thead>
                    <tr>
                        <th rowspan="2" class="bg-dark text-white" style="width:16%">카테고리</th>
                        <th id="dynamicHeader" class="bg-light">쇼핑엔티 (기준)</th>
                        <th colspan="4">주요 경쟁사 비교</th>
                    </tr>
                    <tr>
                        <th id="ntHeaderSub">주문효율(백만)</th><th>신세계쇼핑</th><th>SK스토아</th><th>KT알파쇼핑</th><th id="extraCompTh">선택 회사</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
    </div>

    <div class="card-box">
        <div class="d-flex justify-content-between align-items-center mb-3 flex-wrap gap-2">
            <div class="section-title m-0">📅 일자별 상세 편성표</div>
            <div class="d-flex gap-2 align-items-center flex-wrap">
                <div class="input-group input-group-sm" style="width: auto;">
                    <span class="input-group-text">기간</span>
                    <input type="date" id="startDate" class="form-control" value="{default_date}">
                    <span class="input-group-text">~</span>
                    <input type="date" id="endDate" class="form-control" value="{default_date}">
                </div>
                <div class="dropdown" style="width:170px;">
                    <button class="btn btn-outline-secondary btn-sm dropdown-toggle w-100 text-truncate" type="button" id="scheduleCompBtn" data-bs-toggle="dropdown" data-bs-auto-close="outside" aria-expanded="false">
                        회사 선택
                    </button>
                    <ul class="dropdown-menu p-2" id="scheduleCompMenu" style="max-height:280px; overflow-y:auto; min-width:170px;">
                        <li>
                            <label class="dropdown-item m-0 px-1 d-flex align-items-center gap-1">
                                <input type="checkbox" id="scheduleCompAll" class="form-check-input mt-0"> <span class="fw-bold">전체</span>
                            </label>
                        </li>
                        <li><hr class="dropdown-divider m-1"></li>
                    </ul>
                </div>
                <select id="scheduleGubun" class="form-select form-select-sm" style="width:110px;">
                    <option value="전체">구분:전체</option>
                </select>
                <select id="scheduleCat" class="form-select form-select-sm" style="width:120px;"><option value="all">전체 카테고리</option></select>
                <input type="text" id="prodSearch" class="form-control form-control-sm" placeholder="상품명 검색..." style="width:150px;">
                <button class="btn btn-dark btn-sm px-3" onclick="renderSchedule()">조회</button>
            </div>
        </div>
        <div class="table-responsive">
            <table id="scheduleTable" class="table table-striped table-hover table-bordered" style="width:100%">
                <thead class="table-dark">
                    <tr><th>날짜</th><th>시간</th><th>상품명</th><th>카테고리</th><th>판매량</th><th>회사</th><th>구분</th><th>주문금액</th><th>효율</th></tr>
                    <tr class="total-row">
                        <td colspan="7">합계 (Total)</td><td id="totalSales">0</td><td id="totalEff">0.0</td>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
    </div>
</div>

<script>
    const rawData = {data_json};
    const weekMap = {json.dumps(weeks, ensure_ascii=False)};
    const catOrder = {json.dumps(TARGET_CATEGORIES, ensure_ascii=False)};
    const grpFashion = {json.dumps(GROUP_FASHION, ensure_ascii=False)};
    const grpIntangible = {json.dumps(GROUP_INTANGIBLE, ensure_ascii=False)};
    const compList = {json.dumps(sorted_comps, ensure_ascii=False)};
    const gubunOptions = {json.dumps(gubun_options, ensure_ascii=False)};

    $(document).ready(function() {{
        weekMap.forEach(w => {{
            $('#weekSelect').append(new Option(w[1], w[0]));
            $('#trendWeekSelect').append(new Option(w[1], w[0]));
        }});
        if(weekMap.length > 0) {{
            const lastWeek = weekMap[weekMap.length-1][0];
            $('#weekSelect').val(lastWeek);
            $('#trendWeekSelect').val(lastWeek);
        }}
        compList.forEach(c => {{
            if(c !== '쇼핑엔티') $('#extraComp').append(new Option(c, c));
        }});

        compList.forEach(c => {{
            $('#scheduleCompMenu').append(
                '<li><label class="dropdown-item m-0 px-1 d-flex align-items-center gap-1">' +
                '<input type="checkbox" class="form-check-input schedule-comp-cb mt-0" value="' + c + '"> <span>' + c + '</span>' +
                '</label></li>'
            );
        }});
        $('.schedule-comp-cb[value="쇼핑엔티"]').prop('checked', true);
        updateScheduleCompLabel();

        gubunOptions.forEach(g => $('#scheduleGubun').append(new Option(g, g)));
        catOrder.forEach(c => $('#scheduleCat').append(new Option(c, c)));
        renderAll();
    }});

    $(document).on('change', '#scheduleCompAll', function() {{
        const checked = $(this).is(':checked');
        $('.schedule-comp-cb').prop('checked', checked);
        updateScheduleCompLabel();
    }});
    $(document).on('change', '.schedule-comp-cb', function() {{
        const total = $('.schedule-comp-cb').length;
        const checkedCount = $('.schedule-comp-cb:checked').length;
        $('#scheduleCompAll').prop('checked', total > 0 && total === checkedCount);
        updateScheduleCompLabel();
    }});

    function updateScheduleCompLabel() {{
        const total = $('.schedule-comp-cb').length;
        const selected = $('.schedule-comp-cb:checked').map(function() {{ return $(this).val(); }}).get();
        let label;
        if (selected.length === 0) label = '회사 선택';
        else if (selected.length === total) label = '전체';
        else if (selected.length === 1) label = selected[0];
        else label = selected[0] + ' 외 ' + (selected.length - 1) + '개';
        $('#scheduleCompBtn').text(label);
    }}

    function getSelectedSchedComps() {{
        return $('.schedule-comp-cb:checked').map(function() {{ return $(this).val(); }}).get();
    }}

    function renderAll() {{
        renderTrendSection();
        renderCompTable();
        renderSchedule();
    }}

    function renderTrendSection() {{
        const mode = $('input[name="trendMode"]:checked').val();
        const selStart = $('#trendWeekSelect').val();
        const allStarts = weekMap.map(w => w[0]);
        const idx = allStarts.indexOf(selStart);
        const startIdx = Math.max(0, idx - 11);
        const last12Weeks = weekMap.slice(startIdx, idx + 1);

        let h = '<th class="bg-dark text-white" style="width:80px;">구분</th>';
        last12Weeks.forEach(w => h += `<th>${{w[1]}}</th>`);
        h += '<th style="width:70px;">12주평균</th>';
        $('#trendHeader').html(h);

        const targets = ['쇼핑엔티', '신세계쇼핑', 'SK스토아', 'KT알파쇼핑'];
        let b = '';
        const traces = [];
        targets.forEach(t => {{
            let row = `<tr><td class="fw-bold">${{t}}</td>`;
            let sumVal = 0, count = 0;
            const yData = [];
            last12Weeks.forEach(w => {{
                const filtered = rawData.filter(d => d['주차_시작일'] === w[0] && d['회사명'] === t);
                const s = filtered.reduce((a, b) => a + b['주문금액'], 0);
                const tr = filtered.reduce((a, b) => a + (!grpIntangible.includes(b['카테고리']) ? b['가치시간'] : 0), 0);
                const days = new Set(filtered.map(d => d['방송날짜_str'])).size;
                const val = mode === 'sales' ? (days ? Math.round(s / days / 1000000) : 0) : (tr ? Math.round(s / tr / 1000000) : 0);
                row += `<td>${{val.toLocaleString()}}</td>`;
                sumVal += val; count++;
                yData.push(val);
            }});
            row += `<td class="bg-light fw-bold">${{count?Math.round(sumVal/count).toLocaleString():0}}</td></tr>`;
            b += row;
            traces.push({{ x: last12Weeks.map(w=>w[1]), y: yData, name: t, mode: 'lines+markers', line: {{width: t==='쇼핑엔티'?4:2}} }});
        }});
        $('#trendTable tbody').html(b);
        Plotly.newPlot('trendChart', traces, {{
            margin: {{t:10, b:40, l:40, r:10}},
            legend: {{orientation:'h', y:1.2}},
            xaxis: {{ tickfont: {{ size: 10 }} }}
        }});
    }}

    function renderCompTable() {{
        const mode = $('input[name="viewMode"]:checked').val();
        const selWeek = $('#weekSelect').val();
        const extra = $('#extraComp').val();
        const allStarts = weekMap.map(w => w[0]);
        const prevWeek = allStarts[allStarts.indexOf(selWeek)-1];

        $('#ntHeaderSub').text(mode==='sales'?'일평균 주문금액(백만)':(mode==='share'?'편성비중(%)':'주문효율(백만)'));
        $('#extraCompTh').text(extra || '선택 회사');

        const targets = ['신세계쇼핑', 'SK스토아', 'KT알파쇼핑'];
        if(extra && !targets.includes(extra)) targets.push(extra);
        const allComps = ['쇼핑엔티', ...targets];

        function getStats(wk) {{
            const res = {{ raw: {{}}, fashion: {{}}, intangible: {{}}, total: {{}} }};
            allComps.forEach(c => {{
                res.total[c] = {{ s:0, t:0, t_real:0, days: new Set() }};
                res.fashion[c] = {{ s:0, t:0, days: new Set() }}; res.intangible[c] = {{ s:0, t:0, days: new Set() }};
            }});
            rawData.filter(d => d['주차_시작일'] === wk).forEach(d => {{
                const c = d['회사명']; if(!allComps.includes(c)) return;
                const cat = d['카테고리'], s = d['주문금액'], t = d['가치시간'], date = d['방송날짜_str'];
                if(!res.raw[c]) res.raw[c] = {{}};
                if(!res.raw[c][cat]) res.raw[c][cat] = {{ s:0, t:0, days: new Set() }};
                res.raw[c][cat].s += s; res.raw[c][cat].t += t; res.raw[c][cat].days.add(date);
                res.total[c].s += s; res.total[c].t += t; res.total[c].days.add(date);
                if(!grpIntangible.includes(cat)) res.total[c].t_real += t;
                if(grpFashion.includes(cat)) {{ res.fashion[c].s += s; res.fashion[c].t += t; res.fashion[c].days.add(date); }}
                if(grpIntangible.includes(cat)) {{ res.intangible[c].s += s; res.intangible[c].t += t; res.intangible[c].days.add(date); }}
            }});
            return res;
        }}

        const curr = getStats(selWeek);
        const prev = prevWeek ? getStats(prevWeek) : null;

        function calc(s, t, totalT, days) {{
            if(mode === 'sales') return days ? Math.round(s / days / 1000000) : 0;
            if(mode === 'perf' || mode === 'compEff') return t ? Math.round(s / t / 1000000) : 0;
            if(mode === 'share') return totalT ? (t / totalT * 100) : 0;
            return 0;
        }}

        function getEntry(stats, comp, type, catName) {{
            if(type==='item') return stats.raw[comp]?.[catName] || {{ s:0, t:0, days: new Set() }};
            if(type==='subtotal') return (catName==='fashion' ? stats.fashion[comp] : stats.intangible[comp]);
            return stats.total[comp];
        }}

        function makeRow(title, type, catName=null) {{
            let trCls = type==='subtotal'?'row-subtotal':(type==='grandtotal'?'row-grandtotal':'');
            let h = `<tr class="${{trCls}}"><td class="text-start ps-3">${{title}}</td>`;

            const ntEntry = getEntry(curr, '쇼핑엔티', type, catName);
            const ntRealT = curr.total['쇼핑엔티'].t_real;
            const ntDays = ntEntry.days.size;
            let ntEffBase = (type==='grandtotal' && (mode==='perf'||mode==='compEff')) ? (ntRealT?ntEntry.s/ntRealT:0) : (ntEntry.t?ntEntry.s/ntEntry.t:0);
            let ntVal = calc(ntEntry.s, (type==='grandtotal' && (mode==='perf'||mode==='compEff'))?ntRealT:ntEntry.t, curr.total['쇼핑엔티'].t, ntDays);

            if(mode !== 'compEff' && prev) {{
                const pEntry = getEntry(prev, '쇼핑엔티', type, catName);
                const pRealT = prev.total['쇼핑엔티'].t_real;
                const pDays = pEntry.days.size;
                let pVal = calc(pEntry.s, (type==='grandtotal' && mode==='perf')?pRealT:pEntry.t, prev.total['쇼핑엔티'].t, pDays);
                let diff = ntVal - pVal;
                let dDisp = (diff>0?'+':'') + (mode==='share'?diff.toFixed(1)+'%p':diff.toLocaleString());
                h += `<td>${{mode==='share'?ntVal.toFixed(1)+'%':ntVal.toLocaleString()}} <span class="diff-val">(${{dDisp}})</span></td>`;
            }} else {{
                h += `<td>${{mode==='share'?ntVal.toFixed(1)+'%':ntVal.toLocaleString()}}</td>`;
            }}

            targets.forEach(t => {{
                const tEntry = getEntry(curr, t, type, catName);
                const tRealT = curr.total[t].t_real;
                const tDays = tEntry.days.size;
                let tEff = (type==='grandtotal' && (mode==='perf'||mode==='compEff')) ? (tRealT?tEntry.s/tRealT:0) : (tEntry.t?tEntry.s/tEntry.t:0);
                let tVal = calc(tEntry.s, (type==='grandtotal' && (mode==='perf'||mode==='compEff'))?tRealT:tEntry.t, curr.total[t].t, tDays);

                if(mode==='compEff') {{
                    let r = ntEffBase > 0 ? (tEff/ntEffBase*100) : 0;
                    let cls = r > 100 ? 'text-lose fw-bold' : (r < 100 && r > 0 ? 'text-win fw-bold' : '');
                    h += `<td><span class="${{cls}}">${{r>0?Math.round(r)+'%':'0%'}}</span></td>`;
                }} else {{
                    let cls = tVal > ntVal ? 'text-lose' : (tVal < ntVal ? 'text-win' : '');
                    let tDisp = mode==='share'?tVal.toFixed(1)+'%':tVal.toLocaleString();
                    if(prev) {{
                        const tpEntry = getEntry(prev, t, type, catName);
                        const tpRealT = prev.total[t].t_real;
                        const tpDays = tpEntry.days.size;
                        let tpVal = calc(tpEntry.s, (type==='grandtotal' && mode==='perf')?tpRealT:tpEntry.t, prev.total[t].t, tpDays);
                        let td = tVal - tpVal;
                        let tdD = (td>0?'+':'') + (mode==='share'?td.toFixed(1)+'%p':td.toLocaleString());
                        h += `<td><span class="${{cls}}">${{tDisp}}</span> <span class="diff-val">(${{tdD}})</span></td>`;
                    }} else {{
                        h += `<td><span class="${{cls}}">${{tDisp}}</span></td>`;
                    }}
                }}
            }});
            return h + '</tr>';
        }}
        let b = makeRow('전체 합계', 'grandtotal');
        catOrder.forEach(cat => {{
            b += makeRow(cat, 'item', cat);
            if(cat === '레포츠의류') b += makeRow('패션 소계', 'subtotal', 'fashion');
            if(cat === '여행') b += makeRow('무형 소계', 'subtotal', 'intangible');
        }});
        $('#compTable tbody').html(b);
    }}

    function renderSchedule() {{
        const start = $('#startDate').val(), end = $('#endDate').val();
        const selectedComps = getSelectedSchedComps();
        const gubun = $('#scheduleGubun').val();
        const cat = $('#scheduleCat').val(), searchTxt = $('#prodSearch').val().trim().toLowerCase();
        if ($.fn.DataTable.isDataTable('#scheduleTable')) $('#scheduleTable').DataTable().destroy();
        const filtered = rawData.filter(d => {{
            return d['방송날짜_str'] >= start && d['방송날짜_str'] <= end
                && (selectedComps.length===0 || selectedComps.includes(d['회사명']))
                && (gubun==='전체' || d['홈쇼핑구분']===gubun)
                && (cat==='all'||d['카테고리']===cat)
                && (searchTxt===''||d['상품명'].toLowerCase().includes(searchTxt));
        }});
        let sumS = 0, sumT = 0;
        const tableData = filtered.map(d => {{
            sumS += d['주문금액']; if(!grpIntangible.includes(d['카테고리'])) sumT += d['가치시간'];
            return [d['방송날짜_str'], d['방송시작시간'].substring(0,5), d['상품명'], d['카테고리'], d['판매량'].toLocaleString(), d['회사명'], d['홈쇼핑구분'], Math.round(d['주문금액']/1000000).toLocaleString(), (d['주문효율']/1000000).toFixed(1)];
        }});
        $('#totalSales').text(Math.round(sumS/1000000).toLocaleString());
        $('#totalEff').text(sumT ? (sumS/sumT/1000000).toFixed(1) : '0.0');
        $('#scheduleTable').DataTable({{
            data: tableData, order: [[0, 'desc'], [1, 'desc']], paging: false, searching: false, info: false,
            columnDefs: [{{ targets: 2, className: "text-start", render: (d)=>`<div class="text-truncate-custom" title="${{d}}">${{d}}</div>` }}]
        }});
    }}
</script>
</body>
</html>
"""

# ---------------------------------------------------------
# 4. 구글 드라이브 업로드 (기존 파일 덮어쓰기)
# ---------------------------------------------------------
DRIVE_FOLDER_ID = '1EDN4y1K1_3icuoU8rBDXACowkxqd5Q5A'
print("☁️ 구글 드라이브로 파일 업데이트 중...")
drive_service = build('drive', 'v3', credentials=creds)

media = MediaIoBaseUpload(io.BytesIO(html_content.encode('utf-8')), mimetype='text/html', resumable=True)

query = f"'{DRIVE_FOLDER_ID}' in parents and name='index.html' and trashed=false"
results = drive_service.files().list(q=query, fields="files(id, name)").execute()
items = results.get('files', [])

if items:
    file_id = items[0]['id']
    drive_service.files().update(fileId=file_id, media_body=media).execute()
    print(f"✅ 구글 드라이브 index.html 덮어쓰기 완료! (ID: {file_id})")
else:
    print("❌ 드라이브에 index.html 파일이 없습니다. 빈 파일을 먼저 만들어주세요!")

print("🎉 [Step 5] 대시보드 HTML 배포 완벽 종료!")
