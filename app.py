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
        st.error("MONGO_URI secret'ı tanımlı değil. Streamlit Cloud'da ekleyin.")
        st.stop()
    return MongoClient(mongo_uri).get_database("organiser")

def get_auth_password_from_db():
    """Şifreyi MongoDB'den çeker."""
    try:
        db = get_db()
        ayarlar = db.get_collection("ayarlar").find_one({"tip": "giris_kontrol"})
        return ayarlar.get("sifre") if ayarlar else "drysele" # DB'de yoksa yedek şifre
    except:
        return "drysele"

def log_ip_to_mongodb(ip, status="Başarılı"):
    """Giriş yapan IP'yi kaydeder."""
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

# --- IP TESPİTİ (SADECE LOGLAMA İÇİN) ---
def get_user_ip():
    try:
        # Sadece IP almak için hızlı bir servis
        return requests.get('https://api.ipify.org?format=json', timeout=5).json().get("ip")
    except:
        return "0.0.0.0"

# --- GÜVENLİK GİRİŞİ ---
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    user_ip = get_user_ip()
    
    st.markdown("<br><br>", unsafe_allow_html=True)
    col_l, col_c, col_r = st.columns([1, 2, 1])
    
    with col_c:
        if os.path.exists("logo.png"):
            st.image("logo.png", width=150)
        st.title("Güvenli Giriş")
        st.info(f"🌐 IP Adresiniz: {user_ip}")
        
        girilen_kod = st.text_input("Lütfen Giriş Kodunu Yazın:", type="password")
        
        if st.button("Sisteme Eriş"):
            dogru_sifre = get_auth_password_from_db()
            
            if girilen_kod == dogru_sifre:
                log_ip_to_mongodb(user_ip, "Başarılı")
                st.session_state.authenticated = True
                st.rerun()
            else:
                log_ip_to_mongodb(user_ip, "Hatalı Şifre Denemesi")
                st.error("Kod yanlış, erişim engellendi.")
    st.stop()

# --- BURADAN AŞAĞISI ANA UYGULAMA (Giriş başarılıysa yüklenir) ---

# Logo ve Başlık
col_logo, col_title = st.columns([1, 8])
with col_logo:
    if os.path.exists("logo.png"):
        st.image("logo.png", width=100)
with col_title:
    st.title("🏛️ Müzayede Eser Havuzu")

# Sidebar ve diğer fonksiyonlarını (parse_word_eserler vb.) buraya olduğu gibi ekleyebilirsin.
st.success(f"Hoş geldiniz! IP adresiniz ({user_ip}) güvenlik amacıyla kaydedilmiştir.")