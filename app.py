import streamlit as st
import pandas as pd
import pdfplumber
import re

# --- 頁面設定 ---
st.set_page_config(page_title="全能信用卡管家", page_icon="💳", layout="wide")

st.title("💳 Credit Card Master 全能信用卡管家")
st.markdown("支援 **星展、玉山 (含Ubear/Pi)** 等多種格式解析與回饋試算。")

# ==========================================
# 1. 權益資料庫 (Card Database)
# ==========================================

class CardRule:
    def __init__(self, name, bank, base_rate=0.01, special_rate=0.03, special_keywords=[]):
        self.name = name
        self.bank = bank
        self.base_rate = base_rate
        self.special_rate = special_rate
        self.special_keywords = special_keywords

    def calculate(self, shop_name, amount):
        is_special = any(k.lower() in shop_name.lower() for k in self.special_keywords)
        rate = self.special_rate if is_special else self.base_rate
        points = round(amount * rate)
        return points, rate, "🔥 指定加碼" if is_special else "一般回饋"

# 初始化卡片庫
MY_CARDS = [
    # 星展銀行
    CardRule("英雄聯盟卡", "星展銀行", 0.012, 0.10, ["Garena", "Steam", "Netflix", "Uber", "Foodpanda"]),
    CardRule("eco永續卡", "星展銀行", 0.015, 0.05, ["Tesla", "Gogoro", "星巴克"]),
    
    # 玉山銀行
    CardRule("Ubear卡", "玉山銀行", 0.01, 0.03, ["Line Pay", "街口", "Netflix", "Spotify", "Disney"]),
    CardRule("Pi拍錢包卡", "玉山銀行", 0.01, 0.04, ["PChome", "加油", "台灣大車隊"]),
    CardRule("熊本熊雙幣卡", "玉山銀行", 0.01, 0.02, ["日本", "Japan", "JPY"]),
    CardRule("Unicard", "玉山銀行", 0.01, 0.03, []),
    
    # 台新銀行
    CardRule("GoGo卡", "台新銀行", 0.005, 0.038, ["Line Pay", "全支付", "蝦皮"]),
    
    # 永豐銀行
    CardRule("大戶卡", "永豐銀行", 0.01, 0.07, ["飯店", "航空", "電影"]),
    
    # 國泰世華
    CardRule("CUBE卡", "國泰世華", 0.003, 0.03, []), 
]

CARD_OPTIONS = {f"{c.bank} - {c.name}": c for c in MY_CARDS}

# ==========================================
# 2. 銀行帳單解析器 (Bank Parsers)
# ==========================================

def parse_dbs_pdf(full_text):
    """解析星展銀行格式"""
    transactions = []
    lines = full_text.split('\n')
    for line in lines:
        if any(x in line for x in ["本期應繳", "信用額度", "DBS", "繳款截止日", "帳單結帳日"]): continue
        if len(re.findall(r'\d{4}/\d{2}/\d{2}', line)) > 1: continue 

        match = re.search(r'(\d{4}/\d{2}/\d{2})\s+(.+?)\s+([0-9,]+)(?:\s|$)', line)
        if match:
            desc = match.group(2).strip()
            if re.match(r'\d{4}/\d{2}/\d{2}', desc): continue
            try:
                amt = float(match.group(3).replace(",", ""))
                transactions.append({"日期": match.group(1), "摘要": desc, "金額": amt})
            except: continue
    return transactions

def parse_esun_pdf(full_text):
    """
    解析玉山銀行格式 (強化版)
    支援格式： [消費日] [入帳日(可選)] [卡號末四碼(可選)] [摘要] [金額]
    """
    transactions = []
    lines = full_text.split('\n')
    for line in lines:
        if "本期應繳" in line or "玉山銀行" in line or "小計" in line: continue
        
        # Regex 解析邏輯：
        # 1. (\d{4}/\d{2}/\d{2}) -> 第一個日期 (消費日)
        # 2. (?:\s+\d{4}/\d{2}/\d{2})? -> 可選的第二個日期 (入帳日)
        # 3. (?:\s+\d{4})? -> 可選的四碼卡號
        # 4. (.+?) -> 摘要
        # 5. ([0-9,]+)(?:\s|$) -> 金額
        match = re.search(r'(\d{4}/\d{2}/\d{2})(?:\s+\d{4}/\d{2}/\d{2})?(?:\s+\d{4})?\s+(.+?)\s+([0-9,]+)(?:\s|$)', line)
        
        if match:
            desc = match.group(2).strip()
            # 過濾明顯雜訊
            if "轉帳" in desc or "繳款" in desc: continue
            
            try:
                amt = float(match.group(3).replace(",", ""))
                transactions.append({"日期": match.group(1), "摘要": desc, "金額": amt})
            except: continue
    return transactions

def parse_general_pdf(full_text):
    """通用解析器"""
    transactions = []
    lines = full_text.split('\n')
    for line in lines:
        match = re.search(r'(\d{4}/\d{2}/\d{2})\s+(.+?)\s+([0-9,]+)(?:\s|$)', line)
        if match:
            try:
                amt = float(match.group(3).replace(",", ""))
                transactions.append({"日期": match.group(1), "摘要": match.group(2).strip(), "金額": amt})
            except: continue
    return transactions

# ==========================================
# 3. 主程式邏輯
# ==========================================

with st.sidebar:
    st.header("⚙️ 設定與卡片選擇")
    selected_card_name = st.selectbox("請選擇這張帳單所屬的卡片", list(CARD_OPTIONS.keys()))
    current_card = CARD_OPTIONS[selected_card_name]
    
    st.divider()
    pdf_password = st.text_input("🔒 PDF 密碼", type="password", help="星展: 身分證+生日後4碼 / 玉山: 身分證全碼")
    
    # 新增：偵錯模式開關
    debug_mode = st.checkbox("🐞 開啟偵錯模式 (讀不到資料時使用)")

uploaded_file = st.file_uploader("📂 上傳信用卡帳單 (PDF/CSV)", type=["pdf", "csv", "xlsx"])

if uploaded_file:
    df_tx = None
    
    # --- 處理 PDF ---
    if uploaded_file.name.endswith('.pdf'):
        if not pdf_password:
            st.warning("⚠️ 請先於左側輸入 PDF 密碼")
        else:
            try:
                with pdfplumber.open(uploaded_file, password=pdf_password) as pdf:
                    full_text = ""
                    for page in pdf.pages:
                        text = page.extract_text()
                        if text: full_text += text + "\n"
                    
                    # 偵錯模式：顯示原始文字
                    if debug_mode:
                        st.warning("🐞 偵錯模式：以下是 PDF 讀取到的原始文字，請截圖給開發者")
                        st.text_area("PDF Raw Text", full_text[:2000], height=300)

                    # 自動判斷銀行邏輯
                    if "星展" in full_text or "DBS" in full_text:
                        if not debug_mode: st.success("偵測到：星展銀行 (DBS) 帳單")
                        tx_list = parse_dbs_pdf(full_text)
                    elif "玉山" in full_text or "E.SUN" in full_text:
                        if not debug_mode: st.success("偵測到：玉山銀行 (E.Sun) 帳單")
                        tx_list = parse_esun_pdf(full_text)
                    elif "台新" in full_text:
                        if not debug_mode: st.success("偵測到：台新銀行帳單")
                        tx_list = parse_general_pdf(full_text)
                    else:
                        st.info("未偵測到特定銀行，使用通用格式解析")
                        tx_list = parse_general_pdf(full_text)
                    
                    if tx_list:
                        df_tx = pd.DataFrame(tx_list)
                    else:
                        st.error("讀取失敗，找不到交易資料。請開啟「偵錯模式」檢查文字內容。")

            except Exception as e:
                st.error(f"PDF 讀取錯誤 (密碼錯誤或檔案損毀): {e}")

    # --- 處理 CSV/Excel ---
    else:
        try:
            if uploaded_file.name.endswith('.csv'):
                df_tx = pd.read_csv(uploaded_file)
            else:
                df_tx = pd.read_excel(uploaded_file)
            
            st.write("請確認欄位對應：")
            cols = df_tx.columns.tolist()
            c1, c2 = st.columns(2)
            col_desc = c1.selectbox("商店名稱/摘要", cols, index=0)
            col_amt = c2.selectbox("金額", cols, index=1 if len(cols)>1 else 0)
            
            df_tx = df_tx.rename(columns={col_desc: "摘要", col_amt: "金額"})
            df_tx["金額"] = df_tx["金額"].astype(str).str.replace(",","").str.replace("$","").astype(float)
            
        except Exception as e:
            st.error(f"檔案格式錯誤: {e}")

    # --- 開始計算回饋 ---
    if df_tx is not None and not df_tx.empty:
        st.divider()
        st.subheader(f"📊 {current_card.name} - 回饋試算結果")
        
        results = []
        total_points = 0
        
        for idx, row in df_tx.iterrows():
            points, rate, note = current_card.calculate(str(row["摘要"]), float(row["金額"]))
            total_points += points
            results.append({
                "摘要": row["摘要"],
                "金額": row["金額"],
                "回饋率": f"{rate*100:.1f}%",
                "預估點數": points,
                "說明": note
            })
            
        final_df = pd.DataFrame(results)
        
        m1, m2 = st.columns(2)
        m1.metric("總消費金額", f"${final_df['金額'].sum():,.0f}")
        m2.metric("預估總回饋", f"{total_points:,.0f} 點")
        
        st.dataframe(final_df, use_container_width=True)
