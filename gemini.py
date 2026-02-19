import streamlit as st
import pandas as pd
import re
import gspread
import io
import base64
import json
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials
import streamlit.components.v1 as components

# --- 1. 상품 매핑 데이터 (원과호 전용) ---
KOR_TO_ENG_DICT = {
    "싱크": "SYNC UP", "렙틴": "ADIPO-LEPTIN BENEFITS", "리포조말 비타민C": "LIPOSOMAL C",
    "비타민D": "LIQUID D3 10000 IU", "엘테아닌": "L-THEANINE", "자몽씨": "GRAPEFRUIT SEED EXTRACT 400MG",
    "ND50": "MEGA PROBIOTIC™ ND 50", "ND120": "MEGA PROBIOTIC™ ND", "엔자임": "ENZYME BENEFITS",
    "브레인": "BRAIN BENEFITS", "마이타케": "MAITAKE-DMG LIQUID", "이뮤노": "IMMUNO BENEFITS",
    "콜라겐": "NATURE'S COLLAGEN", "파우더": "L-GLUTAMINE POWDER", "네츄럴": "NATURAL MIXED TOCOPHEROL E-400",
    "레스베라": "RESVERATROL-50", "코큐텐": "COQ10-DMG 300/300", 
    "아드레날": "ADRENALYZE", "이노시톨": "INOSITOL+VITEX PLUS", "커큐민": "CURCUMIN C3 COMPLEX",
    "맥시": "MAXI-HGH", "미토": "MITO-FUEL", "글루타치온": "GLUTATHIONE", "밀믹스": "MEAL MIX"
}

# --- 2. 구글 시트 연결 (Base64 압축 해제 방식) ---
def connect_google_sheet():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        # Secrets에서 압축된 한 줄짜리 키를 가져옵니다.
        encoded_key = st.secrets["ENCODED_KEY"]
        
        # Base64 압축을 풀고 JSON으로 변환합니다.
        decoded_key = base64.b64decode(encoded_key).decode("utf-8")
        key_dict = json.loads(decoded_key)
        
        creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
        client = gspread.authorize(creds)
        
        # 원과호 시트 고유 ID
        doc = client.open_by_key("17-7C-Ut21uGF_IpAd3H25VEK9wUW0J9uYKcwbxTvJeQ")
        return doc.worksheet("재고내역"), doc.worksheet("출고기록")
    except Exception as e:
        st.error(f"❌ 시트 연결 실패: {e}")
        return None, None

# --- 3. 데이터 정제 로직 ---
def format_phone_number(phone):
    if pd.isna(phone) or str(phone).strip() in ["", "nan"]: return phone
    clean = re.sub(r'\D', '', str(phone))
    if len(clean) == 11 and clean.startswith('010'): return f"{clean[0:3]}-{clean[3:7]}-{clean[7:11]}"
    return phone

def clean_check_text(val, is_pcc=False):
    text = str(val).replace('(check) ', '').replace('(check)', '').replace('[누락]', '').strip()
    return "(check)" if is_pcc and (not text or text.lower() == "nan") else text

def process_excel(df):
    df = df.copy()
    if '우편번호' in df.columns:
        df['우편번호'] = df['우편번호'].apply(lambda x: str(int(float(x))).zfill(5) if pd.notnull(x) else "")
    for col in ['수령자휴대폰번호', '주문자전화번호']:
        if col in df.columns: df[col] = df[col].apply(format_phone_number)
    if '옵션' in df.columns and '주문수량' in df.columns:
        df['주문수량'] = pd.to_numeric(df['주문수량'], errors='coerce').fillna(1).astype(int)
        df.loc[df['옵션'].astype(str).str.contains('3개'), '주문수량'] *= 3
    
    if all(c in df.columns for c in ['수령자명', '수령자휴대폰번호', '주소']):
        total_qty = df.groupby(['수령자명', '수령자휴대폰번호', '주소'])['주문수량'].transform('sum')
        mask_over_6 = total_qty > 6
    else: mask_over_6 = pd.Series([False] * len(df))

    for i, row in df.iterrows():
        if str(row.get('주문자명')) != str(row.get('수령자명')):
            df.at[i, '수령자명'] = f"(check) {row.get('수령자명', '')}"
        r_raw, o_raw = str(row.get('수령자휴대폰번호', "")), str(row.get('주문자전화번호', ""))
        if re.sub(r'\D', '', r_raw) != re.sub(r'\D', '', o_raw) and o_raw not in ["", "nan"]:
            df.at[i, '수령자휴대폰번호'] = f"(check) {format_phone_number(r_raw)}"
        pccc = str(row.get('개인통관번호', "")).strip()
        if pccc == "" or pccc.lower() in ["nan", "none"] or not pccc.upper().startswith('P'):
            df.at[i, '개인통관번호'] = f"(check) {pccc}"
        if mask_over_6.at[i]:
            df.at[i, '주문수량'] = f"(check) [합계:{int(total_qty.at[i])}개] {df.at[i, '주문수량']}"
    return df

# --- 4. FIFO 분석 및 시뮬레이션 ---
def analyze_fifo_stock(order_df, ws_inv):
    all_inv_data = ws_inv.get_all_values()
    IDX_DATE_IN, IDX_PROD, IDX_IN, IDX_OUT, IDX_STOCK, IDX_TRACK = 0, 3, 7, 8, 10, 11
    
    inv_data = []
    for i, row in enumerate(all_inv_data[1:], start=2):
        if len(row) < 12: continue
        inv_data.append(row + [i])
    
    temp_inv_df = pd.DataFrame(inv_data)
    temp_inv_df[IDX_DATE_IN] = pd.to_datetime(temp_inv_df[IDX_DATE_IN], errors='coerce')
    temp_inv_df = temp_inv_df.sort_values(by=IDX_DATE_IN)

    preview_rows, task_list, board_msgs = [], [], []
    today = datetime.now().strftime('%Y-%m-%d')

    for _, order in order_df.iterrows():
        name = clean_check_text(order['수령자명'])
        eng_name = next((v for k, v in KOR_TO_ENG_DICT.items() if k in str(order['온라인상품명'])), "알수없음")
        raw_q = str(order['주문수량'])
        qty_needed = int(re.search(r'\d+', str(raw_q).split(']')[-1]).group()) if ']' in str(raw_q) else int(re.search(r'\d+', str(raw_q)).group() if re.search(r'\d+', str(raw_q)) else 1)
        
        if eng_name == "알수없음": continue
        matches = temp_inv_df[temp_inv_df[IDX_PROD].str.strip() == eng_name]
        order_msg = [f"◾{name}"]

        for idx, row in matches.iterrows():
            if qty_needed <= 0: break
            s_in, s_out = float(row[IDX_IN] or 0), float(row[IDX_OUT] or 0)
            current_stock = s_in - s_out
            if current_stock > 0:
                take = min(qty_needed, current_stock)
                new_out, new_stock = s_out + take, s_in - (s_out + take)
                in_date = row[IDX_DATE_IN]
                date_str = in_date.strftime('%Y-%m-%d') if pd.notnull(in_date) else "날짜없음"
                
                preview_rows.append({"수령자": name, "상품명": eng_name, "현재고": int(current_stock), "출고": int(take), "잔여": int(new_stock), "트래킹": row[IDX_TRACK], "입고일": date_str})
                task_list.append({'row': row.iloc[-1], 'updates': [(9, new_out, s_out), (11, new_stock, current_stock)], 'log': [today, name, eng_name, int(take), int(new_stock), row[IDX_TRACK], date_str]})
                order_msg.append(f"- {eng_name}/{row[IDX_TRACK]}/{int(take)}")
                temp_inv_df.at[idx, IDX_OUT] = str(new_out); qty_needed -= take
        
        if len(order_msg) > 1: board_msgs.append("\n".join(order_msg))
            
    return pd.DataFrame(preview_rows), task_list, "\n\n".join(board_msgs)

# --- 5. UI 메인 ---
st.set_page_config(page_title="원과호 비서 v16.0", layout="wide")
st.title("📦 원과호 해외주문처리 비서 (v16.0 완결판)")

uploaded = st.file_uploader("📂 플레이오토 엑셀 파일 업로드", type=["xlsx"])

if uploaded:
    if "df" not in st.session_state or st.session_state.fname != uploaded.name:
        st.session_state.df = process_excel(pd.read_excel(uploaded))
        st.session_state.fname = uploaded.name
        st.session_state.last_tasks = []

    df = st.session_state.df
    check_rows = df[df.astype(str).apply(lambda row: row.str.contains('\(check\)').any(), axis=1)]
    with st.expander(f"⚠️ 필수 검수 항목 ({len(check_rows)}건)", expanded=not check_rows.empty):
        if not check_rows.empty:
            st.dataframe(check_rows.style.applymap(lambda x: 'background-color: #FFEB3B' if '(check)' in str(x) else ''), use_container_width=True)
        else: st.success("✅ 모든 데이터가 정상입니다.")

    st.markdown("---")
    edited_df = st.data_editor(df, use_container_width=True, key="main_editor")

    st.markdown("---")
    if st.button("🔍 재고 차감 시뮬레이션 실행"):
        ws_inv, _ = connect_google_sheet()
        if ws_inv:
            pre_df, tasks, msgs = analyze_fifo_stock(edited_df, ws_inv)
            st.session_state.pre_df, st.session_state.tasks, st.session_state.msgs = pre_df, tasks, msgs

    if "pre_df" in st.session_state:
        st.subheader("📋 출고 예정 미리보기")
        st.dataframe(st.session_state.pre_df, use_container_width=True)
        c1, c2 = st.columns(2)
        with c1:
            if st.button("🚀 전체 출고 승인 (시트 반영)"):
                ws_i, ws_s = connect_google_sheet()
                if ws_i:
                    for t in st.session_state.tasks:
                        for col, val, _ in t['updates']: ws_i.update_cell(t['row'], col, val)
                    ws_s.append_rows([t['log'] for t in st.session_state.tasks])
                    st.session_state.last_tasks = st.session_state.tasks
                    st.success("🎉 반영 완료!"); st.balloons()
                    st.text_area("📋 고배송 문구:", st.session_state.msgs, height=300)
        with c2:
            if st.session_state.last_tasks and st.button("🔙 방금 작업 롤백"):
                ws_i, _ = connect_google_sheet()
                for t in st.session_state.last_tasks:
                    for col, _, old_val in t['updates']: ws_i.update_cell(t['row'], col, old_val)
                st.session_state.last_tasks = []
                st.warning("⏪ 재고 롤백 완료!")

    st.markdown("---")
    st.subheader("🔍 통관 검증 및 최종 파일")
    col_a, col_b = st.columns([1, 1.5])
    with col_a:
        if st.button("🔗 검증용 텍스트 생성"):
            v_list = [f"{clean_check_text(r['수령자명'])}/{clean_check_text(r['개인통관번호'], True)}/{clean_check_text(r['수령자휴대폰번호'])}/{r.get('우편번호','')}" for _, r in edited_df.iterrows()]
            st.text_area("GSI 검증 텍스트:", "\n".join(v_list), height=200)
        towrap = io.BytesIO()
        with pd.ExcelWriter(towrap, engine='openpyxl') as writer: edited_df.to_excel(writer, index=False)
        st.download_button("💾 가공 주문서 다운로드", towrap.getvalue(), file_name=f"처리완료_{uploaded.name}")
    with col_b: components.iframe("https://gsiexpress.com/pcc_chk.php", height=450, scrolling=True)
