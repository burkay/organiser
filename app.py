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

def get_auth_codes_from_db():
    """Şifreleri MongoDB'deki 'ayarlar' koleksiyonundan çeker."""
    try:
        db = get_db()
        ayarlar = db.get_collection("ayarlar").find_one({"tip": "giris_kontrol"})
        return ayarlar if ayarlar else {}
    except:
        return {}

def log_ip_to_mongodb(ip, country, status="Başarılı"):
    try:
        db = get_db()
        logs_coll = db.get_collection("ziyaretci_loglari")
        logs_coll.insert_one({
            "ip": ip,
            "ulke": country,
            "tarih": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "durum": status
        })
    except:
        pass

# --- GÜVENLİK VE KONUM KONTROLÜ ---
def get_user_info():
    try:
        data = requests.get('https://ipapi.co/json/').json()
        return {"ip": data.get("ip"), "country": data.get("country_code")}
    except:
        return {"ip": "0.0.0.0", "country": "UNKNOWN"}

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    user_info = get_user_info()
    user_ip = user_info["ip"]
    user_country = user_info["country"]
    
    izin_verilenler = ["TR", "BG"]

    if user_country not in izin_verilenler:
        st.error(f"Erişim Engellendi: Bölgeniz ({user_country}) yetkili değil.")
        st.stop()

    st.markdown("<br><br>", unsafe_allow_html=True)
    col_l, col_c, col_r = st.columns([1, 2, 1])
    with col_c:
        if os.path.exists("logo.png"):
            st.image("logo.png", width=150)
        st.title("Güvenli Giriş")
        st.info(f"📍 Bölge: {user_country} | 🌐 IP: {user_ip}")
        
        girilen_kod = st.text_input("Giriş Kodunu Yazın:", type="password")
        
        if st.button("Sisteme Eriş"):
            # Şifreleri DB'den anlık çekiyoruz
            db_codes = get_auth_codes_from_db()
            beklenen_kod = db_codes.get(user_country)
            
            if beklenen_kod and girilen_kod == beklenen_kod:
                log_ip_to_mongodb(user_ip, user_country, "Başarılı")
                st.session_state.authenticated = True
                st.rerun()
            else:
                log_ip_to_mongodb(user_ip, user_country, "Hatalı Şifre")
                st.error("Kod geçersiz!")
    st.stop()

# --- ANA UYGULAMA (Giriş Sonrası) ---
col_logo, col_title = st.columns([1, 8])
with col_logo:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=100)
with col_title:
    st.title("🏛️ Müzayede Eser Havuzu")

# ... (Geri kalan Word işleme ve listeleme kodların buraya gelecek) ...
st.success("Sisteme başarıyla giriş yapıldı. Kediniz sizi bekliyor! 🐾")