import base64
import os
import io
import time
from datetime import datetime, timedelta
import streamlit as st
import pandas as pd
from docx import Document
from pymongo import MongoClient
from PIL import Image

# Sayfa ayarları
st.set_page_config(
    page_title="Müzayede Eser Havuzu",
    layout="wide",
    page_icon="favicon.png" if os.path.exists("favicon.png") else "logo.png",
)

# --- MONGODB ---
@st.cache_resource
def get_db():
    mongo_uri = st.secrets.get("MONGO_URI")
    if not mongo_uri:
        st.error("MONGO_URI secret'ı tanımlı değil. Streamlit Cloud'da ekleyin.")
        st.stop()
    return MongoClient(mongo_uri).get_database("organiser")

def get_eserler_collection():
    return get_db().get_collection("eserler")

# --- KİMLİK DOĞRULAMA ---
def check_auth():
    """Kimlik doğrulama kontrolü ve session timeout yönetimi"""
    
    # Session state başlatma
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "login_time" not in st.session_state:
        st.session_state.login_time = None
    
    # Timeout kontrolü (1 saat = 3600 saniye)
    if st.session_state.authenticated and st.session_state.login_time:
        elapsed = datetime.now() - st.session_state.login_time
        if elapsed > timedelta(hours=1):
            st.session_state.authenticated = False
            st.session_state.login_time = None
            st.warning("Oturum süresi doldu. Lütfen tekrar giriş yapın.")
            st.rerun()
    
    # Eğer doğrulanmamışsa login ekranı göster
    if not st.session_state.authenticated:
        show_login()
        return False
    
    return True

def show_login():
    """Giriş ekranı"""
    st.title("🔐 Müzayede Eser Havuzu - Giriş")
    st.markdown("---")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.subheader("Lütfen erişim kodunu girin")
        
        # Form kullanarak Enter tuşu desteği ekle
        with st.form(key="login_form", clear_on_submit=False):
            access_code = st.text_input(
                "Erişim Kodu",
                type="password",
                placeholder="Erişim kodunu girin ve Enter'a basın...",
                key="access_code_input"
            )
            
            submitted = st.form_submit_button("Giriş Yap", use_container_width=True)
            
            if submitted and access_code:
                if verify_access_code(access_code):
                    st.session_state.authenticated = True
                    st.session_state.login_time = datetime.now()
                    st.success("✅ Giriş başarılı!")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.error("❌ Hatalı erişim kodu!")
        
        st.caption("Erişim kodunu bilmiyorsanız, lütfen yönetici ile iletişime geçin.")

def verify_access_code(code):
    """MongoDB ayarlar collection'ından şifreyi kontrol et"""
    if not code:
        return False
    
    try:
        db = get_db()
        ayarlar_coll = db.get_collection("ayarlar")
        
        # ayarlar collection'ından şifreyi al
        ayar = ayarlar_coll.find_one({"tip": "giris_kontrol"})
        
        if not ayar or "sifre" not in ayar:
            st.error("Giriş şifresi MongoDB'de tanımlı değil. Lütfen 'ayarlar' collection'ına ekleyin.")
            return False
        
        # Girilen kod ile MongoDB'deki şifreyi karşılaştır
        is_correct = code == ayar["sifre"]
        
        # Giriş denemesini logla
        log_login_attempt(code, is_correct)
        
        return is_correct
        
    except Exception as e:
        st.error(f"Doğrulama hatası: {e}")
        return False

def log_login_attempt(entered_code, success):
    """Giriş denemelerini ziyaretci_loglari collection'ına kaydet"""
    try:
        db = get_db()
        logs_coll = db.get_collection("ziyaretci_loglari")
        
        # IP adresi almaya çalış
        ip_address = "unknown"
        try:
            # Streamlit Cloud headers'dan IP al
            if hasattr(st, 'context') and hasattr(st.context, 'headers'):
                ip_address = st.context.headers.get("X-Forwarded-For", "unknown")
                if ip_address == "unknown":
                    ip_address = st.context.headers.get("X-Real-IP", "unknown")
        except:
            pass
        
        # Session ID'yi al
        session_id = "unknown"
        try:
            from streamlit.runtime.scriptrunner import get_script_run_ctx
            ctx = get_script_run_ctx()
            if ctx:
                session_id = ctx.session_id
        except:
            pass
        
        # Log kaydı oluştur
        log_entry = {
            "ip_adresi": ip_address,
            "girilen_sifre": entered_code,
            "basarili": success,
            "tarih_saat": datetime.now(),
            "session_id": session_id
        }
        
        logs_coll.insert_one(log_entry)
        
    except Exception as e:
        # Loglama hatası uygulamayı durdurmamalı
        pass  # Sessizce devam et

# --- ANA UYGULAMA ---
def main_app():
    """Ana uygulama - sadece doğrulanmış kullanıcılar için"""
    
    # Logout butonu (sağ üst köşede)
    col1, col2, col3 = st.columns([6, 1, 1])
    with col2:
        remaining_time = timedelta(hours=1) - (datetime.now() - st.session_state.login_time)
        st.caption(f"⏱️ Kalan: {int(remaining_time.total_seconds()//60)} dk")
    with col3:
        if st.button("🚪 Çıkış"):
            st.session_state.authenticated = False
            st.session_state.login_time = None
            st.rerun()
    
    st.title("🏛️ Müzayede Eser Havuzu")
    
    # --- WORD PARSER ---
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
    SIDEBAR_BG = (240, 242, 246)

    def logo_arka_planli(path, width, bg_rgb=SIDEBAR_BG):
        """Şeffaf veya damalı arka planı tek renk (sidebar rengi) yapar."""
        img = Image.open(path).convert("RGBA")
        w, h = img.size
        px = img.load()
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                # Şeffaf veya açık damalı (beyaz/açık gri) pikselleri arka plan rengi yap
                if a < 128 or (r > 215 and g > 215 and b > 215):
                    px[x, y] = (*bg_rgb, 255)
        out = img.convert("RGB")
        buf = io.BytesIO()
        out.save(buf, format="PNG")
        buf.seek(0)
        return buf

    if os.path.exists(LOGO_PATH):
        buf = logo_arka_planli(LOGO_PATH, 200)
        b64 = base64.b64encode(buf.read()).decode()
        st.sidebar.markdown(
            f'<img src="data:image/png;base64,{b64}" width="200" style="pointer-events:none;max-width:100%;height:auto;" />',
            unsafe_allow_html=True,
        )
    st.sidebar.header("📤 Eser Dosyası Yükleme")
    st.sidebar.caption("Word dosyasında her eser '---' ile ayrılmış blokta olmalı. Alanlar: Eser:, Sanatçı:, Sahip:, Kategori:, Depoda: (Evet/Hayır), Detay:")
    st.sidebar.caption("**Dosyayı buraya sürükleyip bırakın** veya **Dosyalara göz at** ile seçin. En fazla 200 MB, .docx.")
    uploaded_file = st.sidebar.file_uploader("Word dosyası seçin (.docx)", type=["docx"], help="Dosya başına en fazla 200 MB. Sadece .docx kabul edilir.")

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
                    t0 = time.perf_counter()
                    BATCH = 5000
                    for i in range(0, len(kayitlar), BATCH):
                        coll.insert_many(kayitlar[i : i + BATCH])
                    sure = time.perf_counter() - t0
                    st.sidebar.success(f"{len(kayitlar)} eser {sure:.2f} saniyede veritabanina eklendi.")
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
        t0 = time.perf_counter()
        items = list(coll.find(sorgu))
        sure_db = time.perf_counter() - t0
    except Exception as e:
        st.error(f"Veritabanı hatası: {e}")
        items = []
        sure_db = 0

    GOSTERIM_LIMITI = 2000

    if items:
        t1 = time.perf_counter()
        df = pd.DataFrame(items).drop(columns=["_id"], errors="ignore")
        sutunlar = ["eser_adi", "sanatci", "sahip", "kategori", "depoda", "detay", "dosya_adi"]
        df = df[[c for c in sutunlar if c in df.columns]]
        df["depoda"] = df["depoda"].map(lambda x: "Evet" if x else "Hayır")
        df = df.rename(columns={
            "eser_adi": "Eser Adı",
            "sanatci": "Sanatçı",
            "sahip": "Sahip",
            "kategori": "Kategori",
            "depoda": "Depoda",
            "detay": "Detay",
            "dosya_adi": "Dosya Adı",
        })
        sure_islem = time.perf_counter() - t1
        toplam = len(df)
        gosterilen = min(toplam, GOSTERIM_LIMITI)
        toplam_sure = sure_db + sure_islem

        # Sonuc getirme performansi
        st.markdown("---")
        perf1, perf2, perf3, perf4 = st.columns(4)
        with perf1:
            st.metric("Sonuç getirme süresi", f"{toplam_sure:.2f} sn", help="Toplam: veritabanı + tabloya hazırlama")
        with perf2:
            st.metric("Veritabanı (MongoDB)", f"{sure_db:.2f} sn", help="find() + list() – arama/filtre sorgusu")
        with perf3:
            st.metric("Tabloya hazırlama", f"{sure_islem:.2f} sn", help="DataFrame + sütun düzeni")
        with perf4:
            st.metric("Sonuç sayısı", f"{toplam:,}", help="Eşleşen kayıt sayısı")
        st.markdown("---")

        st.caption("Her satır bir eseri temsil eder.")
        if toplam > GOSTERIM_LIMITI:
            st.info(f"Tabloda ilk **{gosterilen}** kayıt gösteriliyor (toplam {toplam}).")
            df = df.head(GOSTERIM_LIMITI)
        st.dataframe(df, use_container_width=True)
    else:
        st.info(
            "Eser listesi boş. Sol taraftan standart formatta Word yükleyip "
            "'Eserleri Veritabanına Ekle' ile havuzu doldurun. (Her eser tabloda tek satırda görünür.)"
        )

# --- UYGULAMA AKIŞI ---
if check_auth():
    main_app()
