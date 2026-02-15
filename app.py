import streamlit as st
import pandas as pd
from docx import Document
import sqlite3
import os
from pathlib import Path
from datetime import datetime

# Sayfa ayarları
st.set_page_config(page_title="Word Veri Düzenleyici", layout="wide")
st.title("📂 Word Dosya İşleyici ve Arama")

# --- VERİTABANI KURULUMU ---
DB_PATH = "organiser.db"

def init_database():
    """SQLite veritabanını oluştur"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode = WAL')  # Çoklu yazma için optimize
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def get_all_documents():
    """Tüm belgeleri getir"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        df = pd.read_sql_query(
            "SELECT id, filename, content, created_at FROM documents ORDER BY created_at DESC",
            conn
        )
        conn.close()
        return df
    except Exception as e:
        st.error(f"❌ Veri okuma hatası: {e}")
        return pd.DataFrame()

def add_documents(filename, contents):
    """Veritabanına belge ekle"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('PRAGMA journal_mode = WAL')
        cursor = conn.cursor()
        
        for content in contents:
            cursor.execute(
                "INSERT INTO documents (filename, content) VALUES (?, ?)",
                (filename, content)
            )
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"❌ Veri ekleme hatası: {e}")
        return False

def search_documents(query):
    """Belgelerde ara (case-insensitive)"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        df = pd.read_sql_query(
            "SELECT id, filename, content, created_at FROM documents WHERE LOWER(content) LIKE LOWER(?) ORDER BY created_at DESC",
            conn,
            params=(f"%{query}Buddy Tone}%",)
        )
        conn.close()
        return df
    except Exception as e:
        st.error(f"❌ Arama hatası: {e}")
        return pd.DataFrame()

def delete_all_documents():
    """Tüm belgeleri sil"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM documents")
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        st.error(f"❌ Silme hatası: {e}")
        return False

def get_db_size():
    """Veritabanı boyutunu al"""
    try:
        size = os.path.getsize(DB_PATH)
        return f"{size / 1024:.2f} KB"
    except:
        return "N/A"

# Veritabanını başlat
init_database()

# --- DOSYA YÜKLEME BÖLÜMÜ ---
st.sidebar.header("📤 Dosya Yükleme")
uploaded_file = st.sidebar.file_uploader("Bir Word dosyası seçin", type=["docx"])

if uploaded_file is not None:
    try:
        # Word dosyasını oku
        doc = Document(uploaded_file)
        full_text = [para.text for para in doc.paragraphs if para.text.strip() != ""]
        
        if full_text:
            st.sidebar.write(f"📄 **Dosya:** {uploaded_file.name}")
            st.sidebar.write(f"📋 **Toplam satır:** {len(full_text)}")
            
            if st.sidebar.button("✅ Verileri Veritabanına Ekle", use_container_width=True):
                if add_documents(uploaded_file.name, full_text):
                    st.sidebar.success(f"✨ {len(full_text)} satır veri eklendi!")
                    st.rerun()
                else:
                    st.sidebar.error("❌ Veri eklenemedi")
        else:
            st.sidebar.warning("⚠️ Dosya boş veya geçerli metin yok")
    except Exception as e:
        st.sidebar.error(f"❌ Dosya okuma hatası: {str(e)}")

# --- ARAMA VE GÖRÜNTÜLEME BÖLÜMÜ ---
st.subheader("🔍 Veritabanında Ara")
search_query = st.text_input(
    "Aramak istediğiniz anahtar kelimeyi yazın...",
    placeholder="Örn: müşteri, ürün, tarih..."
)

# Veritabanı istatistikleri
df_all = get_all_documents()
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("📊 Toplam Kayıt", len(df_all))
with col2:
    st.metric("📁 Dosya Sayısı", df_all['filename'].nunique() if len(df_all) > 0 else 0)
with col3:
    if len(df_all) > 0:
        latest = df_all.iloc[0]['created_at']
        st.metric("🕐 Son Ekleme", latest[:10] if latest else "N/A")
    else:
        st.metric("🕐 Son Ekleme", "Veri yok")
with col4:
    st.metric("💾 DB Boyutu", get_db_size())

# Arama ve sonuçları göster
if len(df_all) > 0:
    if search_query:
        filtered_df = search_documents(search_query)
        st.write(f"**{len(filtered_df)} sonuç bulundu:**")
        
        if len(filtered_df) > 0:
            # Görüntüleme seçeneği
            col_view1, col_view2 = st.columns(2)
            with col_view1:
                display_option = st.radio(
                    "Gösterim türü:",
                    ["Tablo", "Detaylı"],
                    horizontal=True
                )
            
            if display_option == "Tablo":
                # Sütunları düzenle
                display_df = filtered_df.copy()
                display_df.columns = ['ID', 'Dosya Adı', 'İçerik', 'Tarih']
                st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True
                )
            else:
                for idx, row in filtered_df.iterrows():
                    with st.expander(f"📄 {row['filename']} - {row['created_at'][:10]}"):
                        st.write(row['content'])
                        st.caption(f"ID: {row['id']}")
        else:
            st.info("❌ Arama sonucu bulunamadı")
    else:
        st.write(f"**Tüm Veriler ({len(df_all)} kayıt):**")
        display_df = df_all.copy()
        display_df.columns = ['ID', 'Dosya Adı', 'İçerik', 'Tarih']
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True
        )
else:
    st.info("📭 Henüz veri yüklenmemiş. Lütfen sol taraftan bir Word dosyası yükleyin.")

# --- İLERİ ÖZELLIKLER ---
with st.sidebar.expander("⚙️ Yönetim Paneli"):
    st.write("**Veritabanı Kontrol**")
    
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("🔄 Yenile", use_container_width=True):
            st.rerun()
    
    with col_btn2:
        if st.button("💾 İndir", use_container_width=True):
            if os.path.exists(DB_PATH):
                with open(DB_PATH, 'rb') as f:
                    st.download_button(
                        label="📥 DB İndir",
                        data=f.read(),
                        file_name=f"organiser_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
                        mime="application/octet-stream"
                    )
    
    st.divider()
    st.write("**Tehlikeli İşlemler**")
    
    if st.button("🗑️ Tüm Verileri Sil", use_container_width=True):
        if st.checkbox("⚠️ Evet, tüm verileri silmek istiyorum"):
            if st.button("🔴 Veri Sil", use_container_width=True):
                if delete_all_documents():
                    st.success("✅ Tüm veriler silindi!")
                    st.rerun()
                else:
                    st.error("❌ Veri silinirken hata oluştu")

# --- FOOTER ---
st.divider()
st.caption("🔐 Verileriniz SQLite veritabanında kalıcı olarak saklanmaktadır.")