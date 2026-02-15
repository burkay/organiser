import streamlit as st
import pandas as pd
from docx import Document
import io

# Sayfa ayarları
st.set_page_config(page_title="Word Veri Düzenleyici", layout="wide")
st.title("📂 Word Dosya İşleyici ve Arama")

# --- VERİ TABANI SİMÜLASYONU ---
# Deneysel olduğu için şimdilik veriyi session_state (oturum hafızası) içinde tutalım
if 'database' not in st.session_state:
    st.session_state.database = []

# --- DOSYA YÜKLEME BÖLÜMÜ ---
st.sidebar.header("Dosya Yükleme")
uploaded_file = st.sidebar.file_uploader("Bir Word dosyası seçin", type=["docx"])

if uploaded_file is not None:
    # Word dosyasını oku
    doc = Document(uploaded_file)
    full_text = [para.text for para in doc.paragraphs if para.text.strip() != ""]
    
    # Basit bir gruplama/temizleme örneği
    if st.sidebar.button("Verileri Veritabanına Ekle"):
        for text in full_text:
            st.session_state.database.append({
                "dosya_adi": uploaded_file.name,
                "icerik": text
            })
        st.sidebar.success(f"{len(full_text)} satır veri eklendi!")

# --- ARAMA VE GÖRÜNTÜLEME BÖLÜMÜ ---
st.subheader("🔍 Veritabanında Ara")
search_query = st.text_input("Aramak istediğiniz anahtar kelimeyi yazın...")

if st.session_state.database:
    df = pd.DataFrame(st.session_state.database)
    
    if search_query:
        # Arama filtresi
        filtered_df = df[df['icerik'].str.contains(search_query, case=False)]
        st.write(f"{len(filtered_df)} sonuç bulundu.")
        st.table(filtered_df)
    else:
        st.write("Tüm veriler:")
        st.table(df)
else:
    st.info("Henüz veri yüklenmemiş. Lütfen sol taraftan bir Word dosyası yükleyin.")