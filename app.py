import streamlit as st
import pandas as pd
import pdfplumber
import re
from datetime import datetime

# --- 頁面設定 ---
st.set_page_config(page_title="全能信用卡管家", page_icon="💳", layout="wide")

st.title("💳 Credit Card Master 全能信用卡管家")
st.markdown("專為您的卡片陣容打造：自動辨識 **星展、玉山** PDF 帳單，並依據卡別自動切換回饋公式。")

# ==========================================
# 1. 您的專屬卡片資料庫 (User Card Database)
# 依據您上傳的圖片建立
# ==========================================

class CardRule:
    def __init__(self, name, bank, base_rate=0.01, special_rate=0.03, special_keywords=[]):
        self.name = name
        self.bank = bank
        self.base_rate = base_rate
        self.special_rate = special_rate
        self.special_keywords = special_keywords

    def calculate(self, shop_name, amount):
        # 簡單判斷：名稱中包含關鍵字即享加碼
        is_special = any(k.lower() in shop_name.lower() for k in self.special_keywords)
        rate = self.special_rate if is_special else self.base_rate
        points = round(amount * rate)
        return points, rate, "🔥 指定加碼" if is_special else "一般回饋"

# 依據您的圖片建立的卡片清單
MY_CARDS_DB = [
    # --- 星展銀行 DBS ---
    CardRule("英雄聯盟卡", "星展銀行", 0.012, 0.10, ["Garena", "Steam", "Netflix", "Uber", "Foodpanda"]),
    CardRule("eco永續卡", "星展銀行", 0.015, 0.05, ["Tesla", "Gogoro", "星巴克"]),

    # --- 玉山銀行 E.Sun ---
    # Unicard (依據您的帳單截圖)
    CardRule("Unicard", "玉山銀行", 0.01, 0.035, ["Line Pay", "街口", "麥當勞", "肯德基"]), 
    CardRule("Ubear卡", "玉山銀行", 0.01, 0.03, ["Line Pay", "Netflix", "Spotify", "Disney", "Nintendo"]),
    CardRule("Pi拍錢包卡", "玉山銀行", 0.01, 0.04, ["PChome", "加油", "台灣大車隊"]),
    CardRule("熊本熊雙幣卡", "玉山銀行", 0.01, 0.02, ["日本", "Japan", "JPY"]),
    CardRule("家樂福聯名卡", "玉山銀行", 0.01, 0.03, ["家樂福", "Carrefour"]),
    CardRule("統一時代聯名卡", "玉山銀行", 0.01, 0.03, ["統一時代", "Uni-President"]),

    # --- 台新銀行 Taishin ---
    CardRule("GoGo卡", "台新銀行", 0.005, 0.038, ["Line Pay", "全支付", "蝦皮", "Momo"]),
    CardRule("太陽卡", "台新銀行", 0.003, 0.038, ["超商", "高鐵", "加油"]),

    # --- 永豐銀行 SinoPac ---
    CardRule("大戶卡", "永豐銀行", 0.01, 0.07, ["飯店", "航空", "電影", "旅行社"]),
    CardRule("Sport卡", "永豐銀行", 0.01, 0.07, ["Apple Pay", "Google Pay"]),
    CardRule("幣倍卡", "永豐銀行", 0.01, 0.03, ["外幣", "Foreign"]),
    CardRule("三井聯名卡", "永豐銀行", 0.01, 0.03, ["Mitsui", "三井"]),

    # --- 國泰世華 Cathay ---
    CardRule("CUBE卡", "國泰世華", 0.003, 0.03, []), # CUBE 方案多變，暫設 3%

    # --- 其他銀行 ---
    CardRule("iLEO卡", "第一銀行", 0.005, 0.02, ["Line Pay"]),
    CardRule("吉鶴卡", "聯邦銀行", 0.01, 0.025, ["日本"]),
    CardRule("LINE Pay卡", "中國信託", 0.01, 0.03, ["Hotels.com", "屈臣氏"]),
]

# 建立快速查找字典
CARD_MAP = {c.name: c for c in MY_CARDS_DB}

# ==========================================
# 2. 銀行帳單解析器 (Bank Parsers)
# ==========================================

def parse_dbs_pdf(full_text):
    """解析星展銀行 (格式: YYYY/MM/DD)"""
    transactions = []
    lines = full_text.split('\n')
    current_year = str(datetime.now().year)
    
    for line in lines:
        if any(x in line for x in ["本期應繳", "信用額度", "DBS", "繳款截止日", "帳單結帳日"]): continue
        if len(re.findall(r'\d{4}/\d{2}/\d{2}', line)) > 1: continue 

        match = re.search(r'(\d{4}/\d{2}/\d{2})\s+(.+?)\s+([0-9,]+)(?:\s|$)', line)
        if match:
            desc = match.group(2).strip()
            if re.match(r'\d{4}/\d{2}/\d{2}', desc): continue
            try:
                amt = float(match.group(3).replace(",", ""))
                # 星展通常不分卡顯示，若需分卡需依賴 CSV
                transactions.append({
                    "日期": match.group(1), 
                    "摘要": desc, 
                    "金額": amt, 
                    "卡別": "星展通用" # 預設
                })
            except: continue
    return transactions

def parse_esun_pdf(full_text):
    """
    解析玉山銀行 (依據截圖開發)
    特色：日期為 MM/DD，且有分卡區塊 (例如: 卡號：xxxx (Unicard-正卡))
    """
    transactions = []
    lines = full_text.split('\n')
    
    current_card_name = "玉山通用" # 預設卡名
    current_year = datetime.now().year # 玉山沒寫年份，暫用今年
    
    # 建立卡號關鍵字對應 (從 PDF 文字對應到資料庫卡片)
    # 當 PDF 出現 "Unicard" -> 對應資料庫的 "Unicard"
    keyword_map = {
        "Unicard": "Unicard",
        "U Bear": "Ubear卡",
        "Ubear": "Ubear卡",
        "Pi": "Pi拍錢包卡",
        "熊本熊": "熊本熊雙幣卡",
        "家樂福": "家樂福聯名卡",
        "統一時代": "統一時代聯名卡"
    }

    for line in lines:
        line = line.strip()
        
        # 1. 偵測卡片切換區塊
        # 截圖範例： "卡號：4323-XXXX-XXXX-6883 (Unicard-正卡)"
        if "卡號：" in line or "卡號:" in line:
            for key, db_name in keyword_map.items():
                if key.lower() in line.lower():
                    current_card_name = db_name
                    # st.write(f"🔍 偵測到卡片切換：{current_card_name}") # Debug用
                    break
            continue

        # 2. 排除雜訊
        if any(x in line for x in ["本期費用明細", "本期消費明細", "小計", "繳款", "e point", "折抵"]):
            continue

        # 3. 解析交易 (格式: MM/DD  MM/DD  摘要  幣別  金額)
        # Regex: (\d{2}/\d{2}) -> 日期
        #        \s+(\d{2}/\d{2}) -> 入帳日
        #        \s+(.+?) -> 摘要
        #        \s+(?:TWD|USD|JPY)? -> 幣別(可選)
        #        \s+([0-9,-]+)$ -> 金額(結尾)
        
        # 針對截圖優化的 Regex
        match = re.search(r'(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+(?:TWD|USD|JPY)?\s*(-?[0-9,]+)$', line)
        
        if match:
            desc = match.group(3).strip()
            # 再次過濾說明欄位中的雜訊
            if "退貨" in desc or "自動轉帳" in desc: continue
            
            try:
                amt_str = match.group(4).replace(",", "")
                amt = float(amt_str)
                
                # 排除負數 (退款或折抵通常不計算回饋)
                if amt < 0: continue

                transactions.append({
                    "日期": f"{current_year}/{match.group(1)}", 
                    "摘要": desc, 
                    "金額": amt,
                    "卡別": current_card_name # 標記這筆消費屬於哪張卡
                })
            except Exception as e:
                # st.write(f"解析失敗: {line} -> {e}")
                continue
                
    return transactions

# ==========================================
# 3. 主程式邏輯
# ==========================================

with st.sidebar:
    st.header("⚙️ 設定")
    pdf_password = st.text_input("🔒 PDF 密碼", type="password", help="星展: 身分證+生日後4碼 / 玉山: 身分證全碼")
    debug_mode = st.checkbox("🐞 開啟偵錯模式")
    
    st.divider()
    st.caption("支援銀行：星展、玉山 (自動切換多卡)")

uploaded_file = st.file_uploader("📂 上傳信用卡帳單 (PDF 推薦)", type=["pdf", "csv", "xlsx"])

if uploaded_file:
    df_tx = None
    transactions_raw = []
    
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
                    
                    if debug_mode:
                        st.text_area("Debug: PDF Content", full_text[:1000])

                    # 自動判斷銀行邏輯
                    if "星展" in full_text or "DBS" in full_text:
                        st.success("✅ 識別成功：星展銀行 (DBS)")
                        transactions_raw = parse_dbs_pdf(full_text)
                    elif "玉山" in full_text or "E.SUN" in full_text:
                        st.success("✅ 識別成功：玉山銀行 (E.Sun) - 支援多卡自動分流")
                        transactions_raw = parse_esun_pdf(full_text)
                    else:
                        st.warning("⚠️ 未偵測到支援的銀行格式，將嘗試通用解析。")
                        # 這裡可以加入通用解析器
                        
                if transactions_raw:
                    df_tx = pd.DataFrame(transactions_raw)
                else:
                    st.error("讀取失敗，找不到交易資料。請確認密碼或是否為電子帳單(非掃描檔)。")

            except Exception as e:
                st.error(f"PDF 讀取錯誤: {e}")

    # --- 處理 CSV (通用) ---
    else:
        # (CSV 處理邏輯保持簡單，略)
        try:
            if uploaded_file.name.endswith('.csv'): df_tx = pd.read_csv(uploaded_file)
            else: df_tx = pd.read_excel(uploaded_file)
            st.info("CSV 模式需手動對應欄位")
        except: pass

    # --- 開始計算回饋 (多卡版核心) ---
    if df_tx is not None and not df_tx.empty:
        st.divider()
        
        # 這裡很關鍵：我們將消費依據「卡別」分組計算
        # 玉山帳單會自動標記 Unicard, Ubear... 星展則標記通用
        
        grouped = df_tx.groupby("卡別")
        
        total_all_points = 0
        
        for card_name, group in grouped:
            st.subheader(f"💳 {card_name}")
            
            # 嘗試從資料庫找對應的卡片規則
            if card_name in CARD_MAP:
                rule = CARD_MAP[card_name]
            else:
                # 找不到就用預設卡 (例如星展通用 -> 預設用 LoL卡算，或讓使用者選)
                if "星展" in card_name: rule = CARD_MAP["英雄聯盟卡"]
                elif "玉山" in card_name: rule = CARD_MAP["Ubear卡"]
                else: rule = MY_CARDS_DB[0] # Fallback
                st.caption(f"⚠️ 自動對應規則：使用 **{rule.name}** 計算")

            results = []
            group_points = 0
            
            for idx, row in group.iterrows():
                points, rate, note = rule.calculate(str(row["摘要"]), float(row["金額"]))
                group_points += points
                results.append({
                    "日期": row["日期"],
                    "摘要": row["摘要"],
                    "金額": row["金額"],
                    "回饋率": f"{rate*100:.1f}%",
                    "預估點數": points,
                    "說明": note
                })
            
            total_all_points += group_points
            res_df = pd.DataFrame(results)
            
            # 顯示該卡片的小計
            c1, c2 = st.columns(2)
            c1.metric(f"{card_name} 消費", f"${res_df['金額'].sum():,.0f}")
            c2.metric(f"{card_name} 回饋", f"{group_points:,.0f} 點")
            
            with st.expander(f"查看 {card_name} 明細"):
                st.dataframe(res_df, use_container_width=True)
            
            st.divider()

        st.success(f"🏆 本期帳單總預估回饋： **{total_all_points:,.0f}** 點")
