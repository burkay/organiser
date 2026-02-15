import streamlit as st
import pandas as pd
from docx import Document
from pymongo import MongoClient

# Sayfa ayarları
st.set_page_config(page_title="Word Veri Düzenleyici", layout="wide")
st.title("📂 Word Dosya İşleyici ve Arama")

# --- MONGODB BAĞLANTISI ---
def get_mongo_collection():
    """Streamlit secret'tan MongoDB bağlantısı kurar, collection döndürür."""
    mongo_uri = st.secrets.get("MONGO_URI")
    if not mongo_uri:
        st.error("MONGO_URI secret'ı tanımlı değil. Streamlit Cloud'da ekleyin.")
        st.stop()
    client = MongoClient(mongo_uri)
    db = client.get_database("organiser")
    return db.get_collection("documents")

# --- DOSYA YÜKLEME BÖLÜMÜ ---
st.sidebar.header("Dosya Yükleme")
uploaded_file = st.sidebar.file_uploader("Bir Word dosyası seçin", type=["docx"])

if uploaded_file is not None:
    doc = Document(uploaded_file)
    full_text = [para.text for para in doc.paragraphs if para.text.strip() != ""]

    if st.sidebar.button("Verileri Veritabanına Ekle"):
        try:
            coll = get_mongo_collection()
            documents = [
                {"dosya_adi": uploaded_file.name, "icerik": text}
                for text in full_text
            ]
            coll.insert_many(documents)
            st.sidebar.success(f"{len(full_text)} satır veri veritabanına eklendi!")
        except Exception as e:
            st.sidebar.error(f"Veritabanı hatası: {e}")

# --- ARAMA VE GÖRÜNTÜLEME BÖLÜMÜ ---
st.subheader("🔍 Veritabanında Ara")
search_query = st.text_input("Aramak istediğiniz anahtar kelimeyi yazın...")

try:
    coll = get_mongo_collection()
    if search_query:
        cursor = coll.find({"icerik": {"$regex": search_query, "$options": "i"}})
    else:
        cursor = coll.find({})
    items = list(cursor)
    for item in items:
        item["_id"] = str(item["_id"])
except Exception as e:
    st.error(f"Veritabanı okuma hatası: {e}")
    items = []

if items:
    df = pd.DataFrame(items)
    st.write(f"{len(df)} sonuç bulundu.")
    st.dataframe(df, use_container_width=True)
else:
    st.info("Henüz veri yok veya arama sonucu bulunamadı. Sol taraftan Word dosyası yükleyip ekleyin.")
