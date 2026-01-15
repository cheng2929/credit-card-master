import streamlit as st
import pandas as pd
import pdfplumber
import re

# --- 頁面設定 ---
st.set_page_config(page_title="全能信用卡管家", page_icon="💳", layout="wide")

st.title("💳 Credit Card Master 全能信用卡管家")
st.markdown("自動辨識銀行帳單，支援 **星展、玉山、台新、永豐、國泰** 等多種格式解析與回饋試算。")

# ==========================================
# 1. 權益資料庫 (Card Database)
# 這裡定義您擁有的所有卡片，我已根據您提供的圖片建立架構
# ==========================================

class CardRule:
    def __init__(self, name, bank, base_rate=0.01, special_rate=0.03, special_keywords=[]):
        self.name = name
        self.bank = bank
        self.base_rate = base_rate
        self.special_rate = special_rate
        self.special_keywords = special_keywords

    def calculate(self, shop_name, amount):
        # 簡單的回饋邏輯：若中關鍵字給特殊回饋，否則給基礎回饋
        # (針對 CUBE 卡等複雜切換邏輯，可後續擴充)
        is_special = any(k.lower() in shop_name.lower() for k in self.special_keywords)
        rate = self.special_rate if is_special else self.base_rate
        points = round(amount * rate)
        return points, rate, "🔥 指定加碼" if is_special else "一般回饋"

# --- 初始化您的卡片庫 ---
# 注意：這裡先設定「預設回饋率」，您可以之後根據最新權益微調數字
MY_CARDS = [
    # 星展銀行
    CardRule("傳說對決卡", "星展銀行", 0.012, 0.10, ["Garena", "Steam", "Netflix", "Uber", "Foodpanda"]),
    CardRule("eco永續卡", "星展銀行", 0.015, 0.05, ["Tesla", "Gogoro", "星巴克"]),
    
    # 玉山銀行
    CardRule("Ubear卡", "玉山銀行", 0.01, 0.03, ["Line Pay", "街口", "Netflix", "Spotify", "Disney"]),
    CardRule("Pi拍錢包卡", "玉山銀行", 0.01, 0.04, ["PChome", "加油", "台灣大車隊"]),
    CardRule("熊本熊雙幣卡", "玉山銀行", 0.01, 0.02, ["日本", "Japan", "JPY"]),
    CardRule("Unicard", "玉山銀行", 0.01, 0.03, []),
    
    # 台新銀行
    CardRule("GoGo卡", "台新銀行", 0.005, 0.038, ["Line Pay", "全支付", "蝦皮"]),
    CardRule("太陽卡", "台新銀行", 0.003, 0.038, ["超商", "高鐵"]),
    
    # 永豐銀行
    CardRule("大戶卡", "永豐銀行", 0.01, 0.07, ["飯店", "航空", "電影"]),
    CardRule("Sport卡", "永豐銀行", 0.01, 0.07, ["Apple Pay", "Google Pay"]),
    CardRule("幣倍卡", "永豐銀行", 0.01, 0.03, ["外幣"]),
    
    # 國泰世華
    CardRule("CUBE卡", "國泰世華", 0.003, 0.03, []), # CUBE 邏輯較複雜，暫設 3%
    
    # 中國信託
    CardRule("LINE Pay卡", "中國信託", 0.01, 0.03, ["Hotels.com"]),
    
    # 其他
    CardRule("吉鶴卡", "聯邦銀行", 0.01, 0.025, ["日本"]),
    CardRule("iLEO卡", "第一銀行", 0.005, 0.02, ["Line Pay"]),
]

# 建立選單用的字典
CARD_OPTIONS = {f"{c.bank} - {c.name}": c for c in MY_CARDS}

# ==========================================
# 2. 銀行帳單解析器 (Bank Parsers)
# 不同銀行的 PDF 排版不同，這裡需要不同的 Regex 邏輯
# ==========================================

def parse_dbs_pdf(full_text):
    """解析星展銀行格式"""
    transactions = []
    lines = full_text.split('\n')
    for line in lines:
        if any(x in line for x in ["本期應繳", "信用額度", "DBS", "繳款截止日", "帳單結帳日"]): continue
        if len(re.findall(r'\d{4}/\d{2}/\d{2}', line)) > 1: continue # 排除摘要行

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
    """解析玉山銀行格式 (通常是: 日期 卡號末四碼 摘要 金額)"""
    transactions = []
    lines = full_text.split('\n')
    for line in lines:
        # 玉山常見格式： 2024/01/01 1234 商店名稱 1,000
        # 或是： 2024/01/01 商店名稱 1,000 (無卡號)
        if "本期應繳" in line or "玉山銀行" in line: continue
        
        # 嘗試抓取 (YYYY/MM/DD) (可能有的卡號) (摘要) (金額)
        # 注意：不同時期帳單格式可能微調，這裡使用較寬鬆的抓法
        match = re.search(r'(\d{4}/\d{2}/\d{2})\s+(?:(?:\d{4})\s+)?(.+?)\s+([0-9,]+)(?:\s|$)', line)
        
        if match:
            desc = match.group(2).strip()
            # 排除明顯非消費的行
            if "小計" in desc or "利息" in desc: continue
            
            try:
                amt = float(match.group(3).replace(",", ""))
                transactions.append({"日期": match.group(1), "摘要": desc, "金額": amt})
            except: continue
    return transactions

def parse_general_pdf(full_text):
    """通用解析器 (嘗試抓取 日期...金額)"""
    transactions = []
    lines = full_text.split('\n')
    for line in lines:
        # 最通用的 regex：找日期開頭，數字結尾
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

# 側邊欄：選擇卡片
with st.sidebar:
    st.header("⚙️ 設定與卡片選擇")
    selected_card_name = st.selectbox("請選擇這張帳單所屬的卡片", list(CARD_OPTIONS.keys()))
    current_card = CARD_OPTIONS[selected_card_name]
    
    st.info(f"目前權益設定：\n- 基礎回饋: {current_card.base_rate*100}%\n- 指定加碼: {current_card.special_rate*100}%")
    
    pdf_password = st.text_input("🔒 PDF 密碼 (通常為身分證相關)", type="password")

# 主畫面：上傳區
uploaded_file = st.file_uploader("📂 上傳信用卡帳單 (PDF/CSV)", type=["pdf", "csv", "xlsx"])

if uploaded_file:
    df_tx = None
    
    # --- 處理 PDF ---
    if uploaded_file.name.endswith('.pdf'):
        if not pdf_password:
            st.warning("⚠️ 請先於左側輸入 PDF 密碼")
        else:
            with st.spinner("正在辨識銀行格式與交易資料..."):
                try:
                    with pdfplumber.open(uploaded_file, password=pdf_password) as pdf:
                        full_text = ""
                        for page in pdf.pages:
                            text = page.extract_text()
                            if text: full_text += text + "\n"
                        
                        # 自動判斷銀行邏輯 (簡單關鍵字判斷)
                        if "星展" in full_text or "DBS" in full_text:
                            st.success("偵測到：星展銀行 (DBS) 帳單")
                            tx_list = parse_dbs_pdf(full_text)
                        elif "玉山" in full_text or "E.SUN" in full_text:
                            st.success("偵測到：玉山銀行 (E.Sun) 帳單")
                            tx_list = parse_esun_pdf(full_text)
                        elif "台新" in full_text:
                            st.success("偵測到：台新銀行帳單")
                            tx_list = parse_general_pdf(full_text) # 暫用通用解析
                        else:
                            st.info("未偵測到特定銀行，使用通用格式解析")
                            tx_list = parse_general_pdf(full_text)
                        
                        if tx_list:
                            df_tx = pd.DataFrame(tx_list)
                        else:
                            st.error("讀取失敗或無交易資料，請確認密碼或檔案格式。")
                except Exception as e:
                    st.error(f"PDF 讀取錯誤: {e}")

    # --- 處理 CSV/Excel ---
    else:
        try:
            if uploaded_file.name.endswith('.csv'):
                df_tx = pd.read_csv(uploaded_file)
            else:
                df_tx = pd.read_excel(uploaded_file)
            
            # 讓使用者選欄位 (因為每家銀行 CSV 欄位名不同)
            st.write("請確認欄位對應：")
            cols = df_tx.columns.tolist()
            c1, c2 = st.columns(2)
            col_desc = c1.selectbox("商店名稱/摘要", cols, index=0)
            col_amt = c2.selectbox("金額", cols, index=1 if len(cols)>1 else 0)
            
            # 重新命名以便後續計算
            df_tx = df_tx.rename(columns={col_desc: "摘要", col_amt: "金額"})
            # 清理金額格式
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
        
        # 顯示儀表板
        m1, m2 = st.columns(2)
        m1.metric("總消費金額", f"${final_df['金額'].sum():,.0f}")
        m2.metric("預估總回饋", f"{total_points:,.0f} 點")
        
        st.dataframe(final_df, use_container_width=True)
        
        st.caption("註：此試算基於通用規則，實際回饋請以銀行帳單為準。")
