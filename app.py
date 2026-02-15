import base64
import os
import io
import time
import requests
import streamlit as st
import pandas as pd
from datetime import datetime
from docx import Document
from pymongo import MongoClient
from PIL import Image

# --- SAYFA AYARLARI ---
st.set_page_config(
    page_title="Müzayede Eser Havuzu",
    layout="wide",
    page_icon="logo.png" if os.path.exists("logo.png") else "🏛️",
)

# --- MONGODB BAĞLANTISI ---
def get_db():
    mongo_uri = st.secrets.get("MONGO_URI")
    if not mongo_uri:
        st.error("MONGO_URI bulunamadı! Lütfen Secrets ayarlarına ekleyin.")
        st.stop()
    return MongoClient(mongo_uri).get_database("organiser")

def get_auth_password_from_db():
    """Şifreyi MongoDB'den çeker."""
    try:
        db = get_db()
        ayarlar = db.get_collection("ayarlar").find_one({"tip": "giris_kontrol"})
        return ayarlar.get("sifre") if ayarlar else "drysele"
    except:
        return "drysele"

def log_ip_to_mongodb(ip, status="Başarılı"):
    """Giriş denemelerini MongoDB'ye kaydeder."""
    try:
        db = get_db()
        logs_coll = db.get_collection("ziyaretci_loglari")
        logs_coll.insert_one({
            "ip": ip,
            "tarih": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "durum": status
        })
    except:
        pass

def get_user_ip():
    try:
        return requests.get('https://api.ipify.org?format=json', timeout=5).json().get("ip")
    except:
        return "0.0.0.0"

# --- GÜVENLİK GİRİŞİ ---
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False
if 'user_ip' not in st.session_state:
    st.session_state.user_ip = "0.0.0.0"

if not st.session_state.authenticated:
    st.session_state.user_ip = get_user_ip()
    
    st.markdown("<br><br>", unsafe_allow_html=True)
    col_l, col_c, col_r = st.columns([1, 2, 1])
    
    with col_c:
        if os.path.exists("logo.png"):
            st.image("logo.png", width=150)
        st.title("Güvenli Giriş")
        st.info(f"🌐 IP Adresiniz: {st.session_state.user_ip}")
        
        girilen_kod = st.text_input("Lütfen Giriş Kodunu Yazın:", type="password")
        
        if st.button("Sisteme Eriş"):
            dogru_sifre = get_auth_password_from_db()
            if girilen_kod == dogru_sifre:
                # Başarılı girişi kaydet
                log_ip_to_mongodb(st.session_state.user_ip, "Başarılı")
                st.session_state.authenticated = True
                st.rerun()
            else:
                # Hatalı denemeyi kaydet
                log_ip_to_mongodb(st.session_state.user_ip, "Hatalı Şifre")
                st.error("Kod yanlış!")
    st.stop()

# --- BURADAN AŞAĞISI ANA UYGULAMA (Giriş sonrası) ---
col_logo, col_title = st.columns([1, 8])
with col_logo:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=100)
with col_title:
    st.title("🏛️ Müzayede Eser Havuzu")

st.success(f"Hoş geldiniz! IP adresiniz ({st.session_state.user_ip}) kaydedilmiştir.")

# --- WORD PARSER VE VERİTABANI İŞLEMLERİ ---
ALAN_ESLESME = {
    "eser": "eser_adi", "sanatçı": "sanatci", "sanatci": "sanatci",
    "sahip": "sahip", "kategori": "kategori", "depoda": "depoda", "detay": "detay"
}

def parse_word_eserler(paragraphs):
    text = "\n".join(p.strip() for p in paragraphs if p and p.strip())
    blocks = [b.strip() for b in text.split("---") if b.strip()]
    kayitlar = []
    for block in blocks:
        rec = {"eser_adi": "", "sanatci": "", "sahip": "", "kategori": "", "depoda": False, "detay": ""}
        for line in block.split("\n"):
            if ":" not in line: continue
            key, _, val = line.partition(":")
            k_clean, v_clean = key.strip().lower(), val.strip()
            if k_clean in ALAN_ESLESME:
                db_k = ALAN_ESLESME[k_clean]
                if db_k == "depoda": rec[db_k] = v_clean.lower() in ("evet", "1", "true")
                else: rec[db_k] = v_clean
        if rec["eser_adi"]: kayitlar.append(rec)
    return kayitlar

# SIDEBAR: Dosya yükleme
st.sidebar.header("📤 Eser Dosyası Yükleme")
uploaded_file = st.sidebar.file_uploader("Word dosyası seçin", type=["docx"])

if uploaded_file:
    doc = Document(uploaded_file)
    kayitlar = parse_word_eserler([p.text for p in doc.paragraphs])
    if kayitlar and st.sidebar.button("Veritabanına Ekle"):
        coll = get_db().get_collection("eserler")
        coll.insert_many(kayitlar)
        st.sidebar.success(f"{len(kayitlar)} eser eklendi.")

# ANA ALAN: Listeleme
st.subheader("🔍 Eserlerde Ara")
search = st.text_input("Arama yapın...")
coll = get_db().get_collection("eserler")
query = {"$or": [{"eser_adi": {"$regex": search, "$options": "i"}}, {"sanatci": {"$regex": search, "$options": "i"}}]} if search else {}

items = list(coll.find(query).limit(100))
if items:
    df = pd.DataFrame(items).drop(columns=["_id"], errors="ignore")
    st.dataframe(df, use_container_width=True)
else:
    st.info("Gösterilecek eser bulunamadı.")