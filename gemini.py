import streamlit as st
import pandas as pd
import os
import re
import streamlit.components.v1 as components

# --- 1. 상품명 매핑 데이터 ---
MAPPING_DICT = {
    "싱크": "6234726923", "렙틴": "22", "리포조말 비타민C": "82", "비타민D": "121",
    "엘테아닌": "84", "자몽씨": "116", "ND50": "21", "ND120": "21-1",
    "엔자임": "6236015197", "브레인": "91", "마이타케": "40", "이뮤노": "16",
    "콜라겐": "10", "파우더": "115", "네츄럴 비타민E": "81", "레스베라": "5050",
    "코큐텐": "32", "아드레날": "11111", "이노시톨": "22222", "커큐민": "33333",
    "맥시": "44444", "미토": "55555", "글루타치온": "66666", "밀믹스": "P3"
}

# --- 2. 보조 함수 로직 ---
def format_phone_number(phone):
    if pd.isna(phone) or str(phone).strip() in ["", "nan"]:
        return phone
    clean_number = re.sub(r'\D', '', str(phone))
    if len(clean_number) == 11 and clean_number.startswith('010'):
        return f"{clean_number[0:3]}-{clean_number[3:7]}-{clean_number[7:11]}"
    elif len(clean_number) == 10 and clean_number.startswith('010'):
        return f"{clean_number[0:3]}-{clean_number[3:6]}-{clean_number[6:10]}"
    return phone

def clean_check_text(val, is_pcc=False):
    text = str(val).replace('(check) ', '').replace('(check)', '').replace('[누락]', '').strip()
    text = re.sub(r'\[합계:\d+개\] ', '', text)
    if is_pcc and (not text or text.lower() == "nan" or text == "None"):
        return "(check)"
    return text

# --- 3. 데이터 가공 함수 ---
def process_excel(df):
    df = df.copy()
    
    if '우편번호' in df.columns:
        df['우편번호'] = df['우편번호'].apply(lambda x: str(int(float(x))).zfill(5) if pd.notnull(x) and str(x).strip() not in ["", "nan"] else "")
    if '배송방법' in df.columns:
        df = df.drop(columns=['배송방법'])
    
    if '쇼핑몰주문번호' in df.columns:
        idx = df.columns.get_loc('쇼핑몰주문번호') + 1
        split_data = df['쇼핑몰주문번호'].astype(str).str.split(' ', n=1, expand=True)
        detail_val = split_data[1] if split_data.shape[1] > 1 else ""
        if '주문번호상세' not in df.columns: df.insert(idx, '주문번호상세', detail_val)
        df['쇼핑몰주문번호'] = split_data[0]

    for col in ['수령자휴대폰번호', '주문자전화번호']:
        if col in df.columns: df[col] = df[col].apply(format_phone_number)

    if '옵션' in df.columns and '주문수량' in df.columns:
        df['주문수량'] = pd.to_numeric(df['주문수량'], errors='coerce').fillna(1).astype(int)
        mask_3ea = df['옵션'].astype(str).str.contains('3개')
        df.loc[mask_3ea, '주문수량'] *= 3

    if all(c in df.columns for c in ['수령자명', '수령자휴대폰번호', '주소']):
        total_qty = df.groupby(['수령자명', '수령자휴대폰번호', '주소'])['주문수량'].transform('sum')
        mask_over_6 = total_qty > 6
    else:
        mask_over_6 = pd.Series([False] * len(df)); total_qty = pd.Series([0] * len(df))

    for i, row in df.iterrows():
        if str(row.get('주문자명')) != str(row.get('수령자명')):
            df.at[i, '주문자명'] = f"(check) {row.get('주문자명', '')}"; df.at[i, '수령자명'] = f"(check) {row.get('수령자명', '')}"
        
        product_name = str(row.get('온라인상품명', ""))
        for key, val in MAPPING_DICT.items():
            if key in product_name:
                df.at[i, '상품번호'] = val
                break
        
        r_phone, o_phone = str(df.at[i, '수령자휴대폰번호']), str(df.at[i, '주문자전화번호'])
        if r_phone != o_phone or (r_phone != "" and not r_phone.startswith("010")):
            df.at[i, '수령자휴대폰번호'] = f"(check) {r_phone}"
        
        pccc = str(row.get('개인통관번호', "")).strip()
        if pccc == "" or pccc.lower() in ["nan", "none"]: df.at[i, '개인통관번호'] = "(check) [누락]"
        elif not pccc.upper().startswith('P'): df.at[i, '개인통관번호'] = f"(check) {pccc}"
            
        df.at[i, '매입처주소'] = row.get('주소')
        if mask_over_6.at[i]:
            df.at[i, '주문수량'] = f"(check) [합계:{int(total_qty.at[i])}개] {df.at[i, '주문수량']}"
            
    return df

# --- 4. UI 구성 ---
st.set_page_config(page_title="해외주문처리 비서", layout="wide")
st.title("📦 해외주문처리 비서")

uploaded_file = st.file_uploader("📂 엑셀 파일을 업로드하세요", type=["xlsx", "xls"])

if uploaded_file:
    if "current_filename" not in st.session_state or st.session_state.current_filename != uploaded_file.name:
        st.session_state.processed_df = process_excel(pd.read_excel(uploaded_file))
        st.session_state.current_filename = uploaded_file.name
        st.session_state.val_text = ""

    df = st.session_state.processed_df
    check_rows = df[df.astype(str).apply(lambda row: row.str.contains('\(check\)').any(), axis=1)]
    option_3_rows = df[df['옵션'].astype(str).str.contains('3개')]

    # 1. 필수 검수 항목 (위)
    st.subheader(f"⚠️ 필수 검수 항목 ({len(check_rows)}건)")
    if not check_rows.empty:
        st.dataframe(check_rows.style.applymap(lambda x: 'background-color: #FFEB3B' if '(check)' in str(x) else ''), use_container_width=True)
    else:
        st.success("필수 검수 항목이 없습니다.")

    st.markdown("---")

    # 2. 수량 배수 적용 내역 (아래)
    st.subheader(f"🔢 수량 배수(3개) 적용 내역 ({len(option_3_rows)}건)")
    if not option_3_rows.empty:
        st.dataframe(option_3_rows[['수령자명', '온라인상품명', '옵션', '주문수량']], use_container_width=True)
    else:
        st.write("적용 내역이 없습니다.")

    st.markdown("---")
    st.subheader("📝 데이터 편집기 (최종 수정)")
    edited_df = st.data_editor(df, use_container_width=True, num_rows="dynamic", key="main_editor")

    # 3. 통관번호 실시간 검증 도우미 (복구 완료!)
    st.markdown("---")
    st.subheader("🛡️ 통관번호 실시간 검증 도우미")
    
    col_v, col_site = st.columns([1, 1.5])
    
    with col_v:
        if st.button("🔗 검증용 텍스트 생성"):
            v_list = []
            for _, row in edited_df.iterrows():
                name = clean_check_text(row.get('수령자명', ''))
                pcc = clean_check_text(row.get('개인통관번호', ''), is_pcc=True)
                phone = clean_check_text(row.get('수령자휴대폰번호', ''))
                zip_c = clean_check_text(row.get('우편번호', ''))
                if name or pcc: v_list.append(f"{name}/{pcc}/{phone}/{zip_c}")
            st.session_state.val_text = "\n".join(v_list)
        
        if st.session_state.get('val_text'):
            st.text_area("GSI 검증용 텍스트 (복사해서 오른쪽 사이트에 붙여넣으세요):", st.session_state.val_text, height=450)
            st.info("💡 텍스트 영역 클릭 후 Ctrl+A, Ctrl+C로 복사하세요.")

    with col_site:
        st.write("🌐 GSI 익스프레스 사이트")
        components.iframe("https://gsiexpress.com/pcc_chk.php", height=600, scrolling=True)

    # 4. 최종 저장
    st.markdown("---")
    if st.button("🚀 최종 결과물 다운로드"):
        output_name = f"처리완료_{uploaded_file.name}"
        edited_df.to_excel(output_name, index=False)
        st.balloons()
        with open(output_name, "rb") as f:
            st.download_button("💾 엑셀 파일 받기", f, file_name=output_name)