import base64
import os
import io
import time
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
import streamlit as st
import pandas as pd
from docx import Document
from pymongo import MongoClient
from PIL import Image


# ==================== DATABASE LAYER ====================

class DatabaseConnection:
    """MongoDB bağlantı yönetimi - Singleton pattern"""
    _instance = None
    _client = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    @property
    def client(self):
        if self._client is None:
            mongo_uri = st.secrets.get("MONGO_URI")
            if not mongo_uri:
                st.error("MONGO_URI secret'ı tanımlı değil.")
                st.stop()
            self._client = MongoClient(mongo_uri)
        return self._client
    
    @property
    def db(self):
        return self.client.get_database("organiser")


class BaseRepository(ABC):
    """Temel repository sınıfı"""
    def __init__(self):
        self.db = DatabaseConnection().db
    
    @abstractmethod
    def get_collection_name(self):
        pass
    
    @property
    def collection(self):
        return self.db.get_collection(self.get_collection_name())


class EserlerRepository(BaseRepository):
    """Eserler collection işlemleri"""
    def get_collection_name(self):
        return "eserler"
    
    def insert_many(self, eserler):
        """Toplu eser ekleme"""
        BATCH_SIZE = 5000
        for i in range(0, len(eserler), BATCH_SIZE):
            self.collection.insert_many(eserler[i:i + BATCH_SIZE])
    
    def search(self, query):
        """Eser arama"""
        return list(self.collection.find(query))
    
    def get_distinct_sanatcilar(self):
        """Benzersiz sanatçı listesi"""
        return sorted(self.collection.distinct("sanatci", {"sanatci": {"$ne": ""}}))


class AyarlarRepository(BaseRepository):
    """Ayarlar collection işlemleri"""
    def get_collection_name(self):
        return "ayarlar"
    
    def get_access_code(self):
        """Giriş şifresini al"""
        ayar = self.collection.find_one({"tip": "giris_kontrol"})
        if not ayar or "sifre" not in ayar:
            raise ValueError("Giriş şifresi MongoDB'de tanımlı değil.")
        return ayar["sifre"]


class LogRepository(BaseRepository):
    """Ziyaretçi logları collection işlemleri"""
    def get_collection_name(self):
        return "ziyaretci_loglari"
    
    def log_login_attempt(self, entered_code, success):
        """Giriş denemesini kaydet"""
        try:
            log_entry = {
                "ip_adresi": self._get_ip_address(),
                "girilen_sifre": entered_code,
                "basarili": success,
                "tarih_saat": datetime.now(),
                "session_id": self._get_session_id()
            }
            self.collection.insert_one(log_entry)
        except Exception:
            pass  # Loglama hatası uygulamayı durdurmamalı
    
    @staticmethod
    def _get_ip_address():
        """IP adresini al"""
        try:
            if hasattr(st, 'context') and hasattr(st.context, 'headers'):
                ip = st.context.headers.get("X-Forwarded-For", "unknown")
                if ip == "unknown":
                    ip = st.context.headers.get("X-Real-IP", "unknown")
                return ip
        except:
            pass
        return "unknown"
    
    @staticmethod
    def _get_session_id():
        """Session ID'yi al"""
        try:
            from streamlit.runtime.scriptrunner import get_script_run_ctx
            ctx = get_script_run_ctx()
            return ctx.session_id if ctx else "unknown"
        except:
            return "unknown"


# ==================== BUSINESS LOGIC LAYER ====================

class AuthenticationService:
    """Kimlik doğrulama servisi"""
    def __init__(self):
        self.ayarlar_repo = AyarlarRepository()
        self.log_repo = LogRepository()
    
    def verify_code(self, code):
        """Erişim kodunu doğrula ve logla"""
        if not code:
            return False
        
        try:
            correct_code = self.ayarlar_repo.get_access_code()
            is_correct = code == correct_code
            self.log_repo.log_login_attempt(code, is_correct)
            return is_correct
        except Exception as e:
            st.error(f"Doğrulama hatası: {e}")
            return False


class SessionManager:
    """Oturum yönetimi"""
    TIMEOUT_HOURS = 1
    
    @staticmethod
    def initialize():
        """Session state'i başlat"""
        if "authenticated" not in st.session_state:
            st.session_state.authenticated = False
        if "login_time" not in st.session_state:
            st.session_state.login_time = None
    
    @staticmethod
    def is_authenticated():
        """Kullanıcı doğrulanmış mı?"""
        return st.session_state.get("authenticated", False)
    
    @classmethod
    def check_timeout(cls):
        """Timeout kontrolü"""
        if cls.is_authenticated() and st.session_state.login_time:
            elapsed = datetime.now() - st.session_state.login_time
            if elapsed > timedelta(hours=cls.TIMEOUT_HOURS):
                cls.logout()
                st.warning("Oturum süresi doldu. Lütfen tekrar giriş yapın.")
                st.rerun()
    
    @staticmethod
    def login():
        """Kullanıcıyı giriş yap"""
        st.session_state.authenticated = True
        st.session_state.login_time = datetime.now()
    
    @staticmethod
    def logout():
        """Kullanıcıyı çıkış yap"""
        st.session_state.authenticated = False
        st.session_state.login_time = None
    
    @classmethod
    def get_remaining_time(cls):
        """Kalan oturum süresi"""
        if not cls.is_authenticated() or not st.session_state.login_time:
            return timedelta(0)
        elapsed = datetime.now() - st.session_state.login_time
        return timedelta(hours=cls.TIMEOUT_HOURS) - elapsed


class WordParser:
    """Word dosyası parse işlemleri"""
    ALAN_ESLESME = {
        "eser": "eser_adi",
        "sanatçı": "sanatci",
        "sanatci": "sanatci",
        "sahip": "sahip",
        "kategori": "kategori",
        "depoda": "depoda",
        "detay": "detay",
    }
    
    @classmethod
    def parse(cls, paragraphs):
        """Word paragraflarını parse et"""
        text = "\n".join(p.strip() for p in paragraphs if p and p.strip())
        blocks = [b.strip() for b in text.split("---") if b.strip()]
        kayitlar = []
        
        for block in blocks:
            rec = cls._parse_block(block)
            if rec["eser_adi"]:
                kayitlar.append(rec)
        
        return kayitlar
    
    @classmethod
    def _parse_block(cls, block):
        """Tek bir bloğu parse et"""
        rec = {
            "eser_adi": "",
            "sanatci": "",
            "sahip": "",
            "kategori": "",
            "depoda": False,
            "detay": ""
        }
        
        for line in block.split("\n"):
            line = line.strip()
            if ":" not in line:
                continue
            
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            
            if key in cls.ALAN_ESLESME:
                db_key = cls.ALAN_ESLESME[key]
                if db_key == "depoda":
                    rec[db_key] = value.lower() in ("evet", "e", "var", "1", "true")
                else:
                    rec[db_key] = value
        
        return rec


class ImageProcessor:
    """Görsel işleme"""
    SIDEBAR_BG = (240, 242, 246)
    
    @classmethod
    def process_logo(cls, path, width=200):
        """Logo arka planını düzenle"""
        img = Image.open(path).convert("RGBA")
        w, h = img.size
        px = img.load()
        
        for y in range(h):
            for x in range(w):
                r, g, b, a = px[x, y]
                if a < 128 or (r > 215 and g > 215 and b > 215):
                    px[x, y] = (*cls.SIDEBAR_BG, 255)
        
        out = img.convert("RGB")
        buf = io.BytesIO()
        out.save(buf, format="PNG")
        buf.seek(0)
        return buf


# ==================== PRESENTATION LAYER ====================

class LoginView:
    """Giriş ekranı görünümü"""
    def __init__(self, auth_service):
        self.auth_service = auth_service
    
    def render(self):
        """Giriş ekranını göster"""
        # Logo göster (eğer varsa)
        logo_path = "logo.png"
        if os.path.exists(logo_path):
            # Logo'yu base64'e çevir
            with open(logo_path, "rb") as f:
                logo_data = base64.b64encode(f.read()).decode()
            
            st.markdown(
                f'''
                <style>
                    .logo-container {{
                        text-align: center;
                        margin-bottom: 30px;
                    }}
                    .logo-container img {{
                        max-width: 300px;
                        width: 100%;
                        height: auto;
                        pointer-events: none;
                        display: inline-block;
                        /* Damalı arka planı gizle */
                        background: var(--background-color);
                    }}
                </style>
                <div class="logo-container">
                    <img src="data:image/png;base64,{logo_data}" alt="Logo" />
                </div>
                ''',
                unsafe_allow_html=True
            )
        
        st.title("🔐 Müzayede Eser Havuzu - Giriş")
        st.markdown("---")
        
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.subheader("Lütfen erişim kodunu girin")
            
            with st.form(key="login_form", clear_on_submit=False):
                access_code = st.text_input(
                    "Erişim Kodu",
                    type="password",
                    placeholder="Erişim kodunu girin ve Enter'a basın...",
                    key="access_code_input"
                )
                
                submitted = st.form_submit_button("Giriş Yap", use_container_width=True)
                
                if submitted and access_code:
                    if self.auth_service.verify_code(access_code):
                        SessionManager.login()
                        st.success("✅ Giriş başarılı!")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error("❌ Hatalı erişim kodu!")
            
            st.caption("Erişim kodunu bilmiyorsanız, lütfen yönetici ile iletişime geçin.")


class MainView:
    """Ana uygulama görünümü"""
    GOSTERIM_LIMITI = 2000
    
    def __init__(self, eserler_repo):
        self.eserler_repo = eserler_repo
    
    def render(self):
        """Ana ekranı göster"""
        self._render_header()
        self._render_sidebar()
        self._render_search()
    
    def _render_header(self):
        """Üst başlık ve çıkış butonu"""
        col1, col2, col3 = st.columns([6, 1, 1])
        with col2:
            remaining = SessionManager.get_remaining_time()
            st.caption(f"⏱️ Kalan: {int(remaining.total_seconds()//60)} dk")
        with col3:
            if st.button("🚪 Çıkış"):
                SessionManager.logout()
                st.rerun()
        
        st.title("🏛️ Müzayede Eser Havuzu")
    
    def _render_sidebar(self):
        """Yan panel - dosya yükleme"""
        st.sidebar.header("📤 Eser Dosyası Yükleme")
        st.sidebar.caption(
            "Word dosyasında her eser '---' ile ayrılmış blokta olmalı. "
            "Alanlar: Eser:, Sanatçı:, Sahip:, Kategori:, Depoda: (Evet/Hayır), Detay:"
        )
        
        uploaded_file = st.sidebar.file_uploader(
            "Word dosyası seçin (.docx)",
            type=["docx"],
            help="Dosya başına en fazla 200 MB. Sadece .docx formatı kabul edilir."
        )
        
        if uploaded_file:
            self._handle_file_upload(uploaded_file)
    
    def _handle_file_upload(self, uploaded_file):
        """Dosya yükleme işlemi"""
        try:
            doc = Document(uploaded_file)
            paragraphs = [p.text for p in doc.paragraphs]
            kayitlar = WordParser.parse(paragraphs)
            
            if kayitlar:
                st.sidebar.success(
                    f"Toplam {len(kayitlar)} eser bulundu. Eklemek için butona tıklayın."
                )
                if st.sidebar.button("Eserleri Veritabanına Ekle"):
                    try:
                        for k in kayitlar:
                            k["dosya_adi"] = uploaded_file.name
                        
                        t0 = time.perf_counter()
                        self.eserler_repo.insert_many(kayitlar)
                        sure = time.perf_counter() - t0
                        
                        st.sidebar.success(
                            f"{len(kayitlar)} eser {sure:.2f} saniyede veritabanına eklendi."
                        )
                    except Exception as e:
                        st.sidebar.error(f"Hata: {e}")
            else:
                st.sidebar.warning(
                    "Bu dosyada geçerli eser bloğu bulunamadı. "
                    "Format: Eser: ... , Sanatçı: ... , bloklar '---' ile ayrılmalı."
                )
        except Exception as e:
            st.sidebar.error(f"Dosya okuma hatası: {e}")
    
    def _render_search(self):
        """Arama ve filtreleme bölümü"""
        st.subheader("🔍 Eserlerde Ara ve Filtrele")
        
        # Filtreler
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            search_query = st.text_input(
                "Anahtar kelime (eser, sanatçı, sahip, detay)",
                placeholder="Örn. yağlı boya, Ahmet..."
            )
        with col2:
            sadece_depoda = st.checkbox("Sadece depodakiler", value=False)
        with col3:
            sanatci_liste = [""] + self.eserler_repo.get_distinct_sanatcilar()
            sanatci_filtre = st.selectbox("Sanatçıya göre", sanatci_liste)
        
        # Sorgu oluştur
        sorgu = self._build_query(search_query, sadece_depoda, sanatci_filtre)
        
        # Sonuçları getir ve göster
        self._show_results(sorgu)
    
    def _build_query(self, search_query, sadece_depoda, sanatci_filtre):
        """MongoDB sorgusu oluştur"""
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
        
        return sorgu
    
    def _show_results(self, sorgu):
        """Sonuçları göster"""
        try:
            t0 = time.perf_counter()
            items = self.eserler_repo.search(sorgu)
            sure_db = time.perf_counter() - t0
        except Exception as e:
            st.error(f"Veritabanı hatası: {e}")
            items = []
            sure_db = 0
        
        if items:
            self._display_results(items, sure_db)
        else:
            st.info(
                "Eser listesi boş. Sol taraftan standart formatta Word yükleyip "
                "'Eserleri Veritabanına Ekle' ile havuzu doldurun."
            )
    
    def _display_results(self, items, sure_db):
        """Sonuçları tablo olarak göster"""
        t1 = time.perf_counter()
        
        # DataFrame hazırla
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
        
        # Performans metrikleri
        self._show_metrics(sure_db, sure_islem, toplam)
        
        # Tablo
        st.caption("Her satır bir eseri temsil eder.")
        if toplam > self.GOSTERIM_LIMITI:
            st.info(f"Tabloda ilk **{self.GOSTERIM_LIMITI}** kayıt gösteriliyor (toplam {toplam}).")
            df = df.head(self.GOSTERIM_LIMITI)
        
        st.dataframe(df, use_container_width=True)
    
    def _show_metrics(self, sure_db, sure_islem, toplam):
        """Performans metriklerini göster"""
        st.markdown("---")
        perf1, perf2, perf3, perf4 = st.columns(4)
        
        with perf1:
            st.metric(
                "Sonuç getirme süresi",
                f"{sure_db + sure_islem:.2f} sn",
                help="Toplam: veritabanı + tabloya hazırlama"
            )
        with perf2:
            st.metric(
                "Veritabanı (MongoDB)",
                f"{sure_db:.2f} sn",
                help="find() + list() – arama/filtre sorgusu"
            )
        with perf3:
            st.metric(
                "Tabloya hazırlama",
                f"{sure_islem:.2f} sn",
                help="DataFrame + sütun düzeni"
            )
        with perf4:
            st.metric(
                "Sonuç sayısı",
                f"{toplam:,}",
                help="Eşleşen kayıt sayısı"
            )
        
        st.markdown("---")


# ==================== APPLICATION ====================

class Application:
    """Ana uygulama sınıfı"""
    def __init__(self):
        self._setup_page()
        SessionManager.initialize()
        
        self.auth_service = AuthenticationService()
        self.eserler_repo = EserlerRepository()
        
        self.login_view = LoginView(self.auth_service)
        self.main_view = MainView(self.eserler_repo)
    
    @staticmethod
    def _setup_page():
        """Sayfa ayarları"""
        st.set_page_config(
            page_title="Müzayede Eser Havuzu",
            layout="wide",
            page_icon="favicon.png" if os.path.exists("favicon.png") else "logo.png",
        )
    
    def run(self):
        """Uygulamayı çalıştır"""
        SessionManager.check_timeout()
        
        if SessionManager.is_authenticated():
            self.main_view.render()
        else:
            self.login_view.render()


# ==================== ENTRY POINT ====================

if __name__ == "__main__":
    app = Application()
    app.run()
