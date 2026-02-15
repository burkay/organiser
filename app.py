import os
import io
import streamlit as st
import pandas as pd
from docx import Document
from pymongo import MongoClient
from PIL import Image

# Sayfa ayarları
st.set_page_config(page_title="Müzayede Eser Havuzu", layout="wide")
st.title("🏛️ Müzayede Eser Havuzu")

# --- MONGODB ---
def get_db():
    mongo_uri = st.secrets.get("MONGO_URI")
    if not mongo_uri:
        st.error("MONGO_URI secret'ı tanımlı değil. Streamlit Cloud'da ekleyin.")
        st.stop()
    return MongoClient(mongo_uri).get_database("organiser")

def get_eserler_collection():
    return get_db().get_collection("eserler")

# --- WORD PARSER (Demo format: bloklar "---" ile ayrılır, her blokta Eser:, Sanatçı:, vb.) ---
ALAN_ESLESME = {
    "eser": "eser_adi",
    "sanatçı": "sanatci",
    "sanatci": "sanatci",
    "sahip": "sahip",
    "kategori": "kategori",
    "depoda": "depoda",
    "detay": "detay",
}

def parse_word_eserler(paragraphs):
    """Word paragraflarını okuyup '---' ile ayrılmış bloklardan eser kayıtları üretir."""
    text = "\n".join(p.strip() for p in paragraphs if p and p.strip())
    blocks = [b.strip() for b in text.split("---") if b.strip()]
    kayitlar = []
    for block in blocks:
        rec = {"eser_adi": "", "sanatci": "", "sahip": "", "kategori": "", "depoda": False, "detay": ""}
        for line in block.split("\n"):
            line = line.strip()
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key in ALAN_ESLESME:
                db_key = ALAN_ESLESME[key]
                if db_key == "depoda":
                    rec[db_key] = value.lower() in ("evet", "e", "var", "1", "true")
                else:
                    rec[db_key] = value
        if rec["eser_adi"]:
            kayitlar.append(rec)
    return kayitlar

# --- SIDEBAR: Logo + Dosya yükleme ---
LOGO_PATH = "logo.png"
SIDEBAR_BG = (240, 242, 246)  # Streamlit sidebar rengine uyumlu açık gri

def logo_arka_planli(path, width, bg_rgb=SIDEBAR_BG):
    """Şeffaf logoyu düz arka plan rengiyle birleştirir (damalı görünümü kaldırır)."""
    img = Image.open(path).convert("RGBA")
    arka = Image.new("RGBA", img.size, (*bg_rgb, 255))
    arka.paste(img, (0, 0), img)
    arka = arka.convert("RGB")
    buf = io.BytesIO()
    arka.save(buf, format="PNG")
    buf.seek(0)
    return buf

if os.path.exists(LOGO_PATH):
    st.sidebar.image(logo_arka_planli(LOGO_PATH, 140), width=140, caption="")
st.sidebar.header("📤 Eser Dosyası Yükleme")
st.sidebar.caption("Word dosyasında her eser '---' ile ayrılmış blokta olmalı. Alanlar: Eser:, Sanatçı:, Sahip:, Kategori:, Depoda: (Evet/Hayır), Detay:")
uploaded_file = st.sidebar.file_uploader("Word dosyası seçin (.docx)", type=["docx"])

if uploaded_file is not None:
    doc = Document(uploaded_file)
    paragraphs = [p.text for p in doc.paragraphs]
    kayitlar = parse_word_eserler(paragraphs)

    if kayitlar:
        st.sidebar.success(f"Toplam {len(kayitlar)} eser bulundu. Eklemek için butona tıklayın.")
        if st.sidebar.button("Eserleri Veritabanına Ekle"):
            try:
                coll = get_eserler_collection()
                for k in kayitlar:
                    k["dosya_adi"] = uploaded_file.name
                coll.insert_many(kayitlar)
                st.sidebar.success(f"{len(kayitlar)} eser veritabanına eklendi!")
            except Exception as e:
                st.sidebar.error(f"Hata: {e}")
    else:
        st.sidebar.warning("Bu dosyada geçerli eser bloğu bulunamadı. Format: Eser: ... , Sanatçı: ... , bloklar '---' ile ayrılmalı.")

# --- ANA ALAN: Arama ve filtreler ---
st.subheader("🔍 Eserlerde Ara ve Filtrele")

coll = get_eserler_collection()

# Filtreler
col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    search_query = st.text_input("Anahtar kelime (eser, sanatçı, sahip, detay)", placeholder="Örn. yağlı boya, Ahmet...")
with col2:
    sadece_depoda = st.checkbox("Sadece depodakiler", value=False)
with col3:
    sanatci_liste = [""] + sorted(coll.distinct("sanatci", {"sanatci": {"$ne": ""}}))
    sanatci_filtre = st.selectbox("Sanatçıya göre", sanatci_liste)

# Sorgu
sorgu = {}
if search_query:
    sorgu["$or"] = [
        {"eser_adi": {"$regex": search_query, "$options": "i"}},
        {"sanatci": {"$regex": search_query, "$options": "i"}},
        {"sahip": {"$regex": search_query, "$options": "i"}},
        {"kategori": {"$regex": search_query, "$options": "i"}},
        {"detay": {"$regex": search_query, "$options": "i"}},
    ]
if sadece_depoda:
    sorgu["depoda"] = True
if sanatci_filtre:
    sorgu["sanatci"] = sanatci_filtre

try:
    items = list(coll.find(sorgu))
except Exception as e:
    st.error(f"Veritabanı hatası: {e}")
    items = []

if items:
    df = pd.DataFrame(items).drop(columns=["_id"], errors="ignore")
    sutunlar = ["eser_adi", "sanatci", "sahip", "kategori", "depoda", "detay", "dosya_adi"]
    df = df[[c for c in sutunlar if c in df.columns]]
    df["depoda"] = df["depoda"].map(lambda x: "Evet" if x else "Hayır")
    # Her satır = 1 eser. Türkçe başlıklar.
    df = df.rename(columns={
        "eser_adi": "Eser Adı",
        "sanatci": "Sanatçı",
        "sahip": "Sahip",
        "kategori": "Kategori",
        "depoda": "Depoda",
        "detay": "Detay",
        "dosya_adi": "Dosya Adı",
    })
    st.caption("Her satır bir eseri temsil eder.")
    st.write(f"**{len(df)}** eser bulundu.")
    st.dataframe(df, use_container_width=True)
else:
    st.info(
        "Eser listesi boş. Sol taraftan standart formatta Word yükleyip "
        "'Eserleri Veritabanına Ekle' ile havuzu doldurun. (Her eser tabloda tek satırda görünür.)"
    )
