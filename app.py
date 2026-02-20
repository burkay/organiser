import base64
import os
import io
import re
import time
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
import streamlit as st
import pandas as pd
from docx import Document
from docx.oxml.ns import qn
from pymongo import MongoClient
from PIL import Image
import cloudinary
import cloudinary.uploader


# ==================== DATABASE LAYER ====================

class DatabaseConnection:
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
    def __init__(self):
        self.db = DatabaseConnection().db

    @abstractmethod
    def get_collection_name(self):
        pass

    @property
    def collection(self):
        return self.db.get_collection(self.get_collection_name())


class EserlerRepository(BaseRepository):
    def get_collection_name(self):
        return "eserler"

    def insert_many(self, eserler):
        BATCH_SIZE = 5000
        for i in range(0, len(eserler), BATCH_SIZE):
            self.collection.insert_many(eserler[i:i + BATCH_SIZE])

    def search(self, query):
        # satis_fiyati UI'da gösterilmez
        return list(self.collection.find(query, {"satis_fiyati": 0}))

    def get_distinct_sanatcilar(self):
        return sorted(self.collection.distinct("sanatci", {"sanatci": {"$ne": ""}}))


class AyarlarRepository(BaseRepository):
    def get_collection_name(self):
        return "ayarlar"

    def get_access_code(self):
        ayar = self.collection.find_one({"tip": "giris_kontrol"})
        if not ayar or "sifre" not in ayar:
            raise ValueError("Giriş şifresi MongoDB'de tanımlı değil.")
        return ayar["sifre"]


class LogRepository(BaseRepository):
    def get_collection_name(self):
        return "ziyaretci_loglari"

    def log_login_attempt(self, entered_code, success):
        try:
            self.collection.insert_one({
                "ip_adresi":     self._get_ip_address(),
                "girilen_sifre": entered_code,
                "basarili":      success,
                "tarih_saat":    datetime.now(),
                "session_id":    self._get_session_id(),
            })
        except Exception:
            pass

    @staticmethod
    def _get_ip_address():
        try:
            if hasattr(st, 'context') and hasattr(st.context, 'headers'):
                ip = st.context.headers.get("X-Forwarded-For", "unknown")
                if ip == "unknown":
                    ip = st.context.headers.get("X-Real-IP", "unknown")
                return ip
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def _get_session_id():
        try:
            from streamlit.runtime.scriptrunner import get_script_run_ctx
            ctx = get_script_run_ctx()
            return ctx.session_id if ctx else "unknown"
        except Exception:
            return "unknown"


# ==================== BUSINESS LOGIC LAYER ====================

class AuthenticationService:
    def __init__(self):
        self.ayarlar_repo = AyarlarRepository()
        self.log_repo = LogRepository()

    def verify_code(self, code):
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
    TIMEOUT_HOURS = 1

    @staticmethod
    def initialize():
        if "authenticated" not in st.session_state:
            st.session_state.authenticated = False
        if "login_time" not in st.session_state:
            st.session_state.login_time = None

    @staticmethod
    def is_authenticated():
        return st.session_state.get("authenticated", False)

    @classmethod
    def check_timeout(cls):
        if cls.is_authenticated() and st.session_state.login_time:
            if datetime.now() - st.session_state.login_time > timedelta(hours=cls.TIMEOUT_HOURS):
                cls.logout()
                st.warning("Oturum süresi doldu. Lütfen tekrar giriş yapın.")
                st.rerun()

    @staticmethod
    def login():
        st.session_state.authenticated = True
        st.session_state.login_time = datetime.now()

    @staticmethod
    def logout():
        st.session_state.authenticated = False
        st.session_state.login_time = None

    @classmethod
    def get_remaining_time(cls):
        if not cls.is_authenticated() or not st.session_state.login_time:
            return timedelta(0)
        return timedelta(hours=cls.TIMEOUT_HOURS) - (datetime.now() - st.session_state.login_time)


class CloudinaryService:
    """
    Cloudinary görsel yükleme servisi.
    Secrets: CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET
    """
    _configured = False

    @classmethod
    def _configure(cls):
        if cls._configured:
            return
        cloudinary.config(
            cloud_name = st.secrets["CLOUDINARY_CLOUD_NAME"],
            api_key    = st.secrets["CLOUDINARY_API_KEY"],
            api_secret = st.secrets["CLOUDINARY_API_SECRET"],
            secure     = True,
        )
        cls._configured = True

    @classmethod
    def upload(cls, image_bytes: bytes, public_id: str) -> str:
        """
        Görsel byte'larını Cloudinary'e yükle, URL döndür.
        public_id → tekrar yüklenirse üzerine yazar (idempotent).
        """
        cls._configure()
        result = cloudinary.uploader.upload(
            image_bytes,
            public_id      = public_id,
            overwrite      = True,
            resource_type  = "image",
            folder         = "muzayede",
            transformation = [{"width": 800, "crop": "limit", "quality": "auto"}],
        )
        return result["secure_url"]


class MuzayedeParser:
    """
    Müzayede kataloğu .docx parse işlemleri.

    Her eser bloğunun yapısı (XML sırası):
      <p>  →  Görsel (w:drawing içeren paragraf)
      <p>  →  Galeri / Sahip    ("Antik" veya "5966- Levent Gürel")
      <p>  →  Sanatçı + yıl     ("Ertuğrul Ateş (1954)")
      <p>  →  Eser adı
      <p>  →  Teknik detaylar
      <p>  →  (opsiyonel) Satış fiyatı  →  sadece DB'ye, UI'da gizli
    """

    @staticmethod
    def _is_sahip(text: str) -> bool:
        if re.match(r'^\d{3,6}-\s*.+', text):
            return True
        if text.strip() in ("Antik", "Galeri", "Özel Koleksiyon"):
            return True
        return False

    @staticmethod
    def _is_fiyat(text: str) -> bool:
        return bool(re.search(r'\d[\d\.,]+\s*(TL|₺)', text, re.IGNORECASE))

    @staticmethod
    def _extract_image_bytes(para_elem, doc_part) -> bytes | None:
        """Paragraf elementinden embed görsel byte'larını çıkar."""
        blips = para_elem.findall('.//' + qn('a:blip'))
        if not blips:
            return None
        rId = blips[0].get(
            '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed'
        )
        if not rId or rId not in doc_part.rels:
            return None
        try:
            return doc_part.rels[rId].target_part.blob
        except Exception:
            return None

    @classmethod
    def parse(cls, doc: Document, upload_images: bool = False) -> list[dict]:
        """
        Document nesnesini parse et; eser listesi döndür.

        upload_images=True → görselleri Cloudinary'e yükler ve gorsel_url ekler.
        upload_images=False → gorsel_url alanı boş kalır (hızlı önizleme için).
        """
        body_children = list(doc.element.body)
        doc_part      = doc.part

        # Body child'larını (paragraf XML elementleri) tara
        # Her child'ın metnini ve görsel içerip içermediğini tespit et
        nodes = []
        for child in body_children:
            texts  = child.findall('.//' + qn('w:t'))
            text   = ''.join(t.text or '' for t in texts).strip()
            is_img = bool(child.findall('.//' + qn('w:drawing')))
            nodes.append({"elem": child, "text": text, "is_img": is_img})

        artworks     = []
        lot_counter  = 0
        i            = 0

        while i < len(nodes):
            node = nodes[i]

            # Görsel paragrafı bul → hemen arkasından sahip satırı gelmeli
            if node["is_img"]:
                img_elem = node["elem"]
                # Sonraki dolu text node'u bul
                j = i + 1
                while j < len(nodes) and not nodes[j]["text"]:
                    j += 1

                if j < len(nodes) and cls._is_sahip(nodes[j]["text"]):
                    lot_counter += 1
                    sahip = nodes[j]["text"]

                    # Devamındaki satırları topla
                    lines = []
                    k = j + 1
                    while k < len(nodes) and len(lines) < 6:
                        t = nodes[k]["text"]
                        if t:
                            if nodes[k]["is_img"] or cls._is_sahip(t):
                                break
                            lines.append(t)
                        else:
                            if k + 1 < len(nodes) and not nodes[k + 1]["text"]:
                                break
                        k += 1

                    sanatci  = lines[0] if len(lines) > 0 else ""
                    eser_adi = lines[1] if len(lines) > 1 else ""
                    detay    = lines[2] if len(lines) > 2 else ""

                    satis_fiyati = ""
                    for ln in reversed(lines[2:]):
                        if cls._is_fiyat(ln):
                            satis_fiyati = ln
                            break

                    # Görsel yükleme
                    gorsel_url = ""
                    if upload_images:
                        img_bytes = cls._extract_image_bytes(img_elem, doc_part)
                        if img_bytes:
                            public_id = f"lot_{lot_counter}"
                            try:
                                gorsel_url = CloudinaryService.upload(img_bytes, public_id)
                            except Exception as e:
                                st.warning(f"Lot {lot_counter} görseli yüklenemedi: {e}")

                    artworks.append({
                        "lot_no":       lot_counter,
                        "sahip":        sahip,
                        "sanatci":      sanatci,
                        "eser_adi":     eser_adi,
                        "detay":        detay,
                        "gorsel_url":   gorsel_url,
                        "satis_fiyati": satis_fiyati,
                    })
                    i = k
                    continue

            i += 1

        return artworks


# ==================== PRESENTATION LAYER ====================

class LoginView:
    def __init__(self, auth_service):
        self.auth_service = auth_service

    def render(self):
        logo_path = "logo.png"
        if os.path.exists(logo_path):
            try:
                logo_img = Image.open(logo_path).convert("RGBA")
                background = Image.new("RGBA", logo_img.size, (14, 17, 23, 255))
                background.paste(logo_img, (0, 0), logo_img)
                final_logo = background.convert("RGB")
                buffered = io.BytesIO()
                final_logo.save(buffered, format="PNG")
                logo_data = base64.b64encode(buffered.getvalue()).decode()
            except Exception:
                with open(logo_path, "rb") as f:
                    logo_data = base64.b64encode(f.read()).decode()

            st.markdown(
                f'''<style>
                    .logo-wrapper {{ text-align:center; margin-bottom:30px; padding:40px; }}
                    .logo-wrapper img {{ max-width:300px; width:100%; height:auto;
                                        pointer-events:none; display:inline-block; border-radius:10px; }}
                </style>
                <div class="logo-wrapper">
                    <img src="data:image/png;base64,{logo_data}" alt="Logo" />
                </div>''',
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
    GOSTERIM_LIMITI = 2000
    KART_KOLONLARI  = 4   # Arama sonuçlarında yan yana kaç kart

    def __init__(self, eserler_repo):
        self.eserler_repo = eserler_repo

    def render(self):
        self._render_header()
        self._render_sidebar()
        self._render_search()

    def _render_header(self):
        col1, col2, col3 = st.columns([6, 1, 1])
        with col2:
            remaining = SessionManager.get_remaining_time()
            st.caption(f"⏱️ Kalan: {int(remaining.total_seconds() // 60)} dk")
        with col3:
            if st.button("🚪 Çıkış"):
                SessionManager.logout()
                st.rerun()
        st.title("🏛️ Müzayede Eser Havuzu")

    def _render_sidebar(self):
        st.sidebar.header("📤 Eser Dosyası Yükleme")
        st.sidebar.caption(
            "Müzayede kataloğu .docx dosyası yükleyin. "
            "Her sayfa bir eser: Sahip · Sanatçı · Eser Adı · Detay · (Fiyat)"
        )

        uploaded_file = st.sidebar.file_uploader(
            "Word dosyası seçin (.docx)",
            type=["docx"],
            help="Sadece .docx formatı kabul edilir."
        )

        gorsel_yukle = st.sidebar.checkbox(
            "Görselleri Cloudinary'e yükle",
            value=True,
            help="İşaretli değilse görseller atlanır, yükleme hızlanır."
        )

        if uploaded_file:
            self._handle_file_upload(uploaded_file, gorsel_yukle)

    def _handle_file_upload(self, uploaded_file, gorsel_yukle: bool):
        try:
            doc = Document(uploaded_file)
            kayitlar = MuzayedeParser.parse(doc, upload_images=False)  # önizleme

            if not kayitlar:
                st.sidebar.warning(
                    "Bu dosyada geçerli eser bloğu bulunamadı. "
                    "Her eser bir Sahip/Galeri satırıyla başlamalı."
                )
                return

            gorsel_sayisi = sum(1 for k in kayitlar if k.get("gorsel_url") == "")
            st.sidebar.success(
                f"Toplam **{len(kayitlar)}** eser bulundu. "
                f"Eklemek için butona tıklayın."
            )

            if st.sidebar.button("Eserleri Veritabanına Ekle"):
                try:
                    # Görselleri yükleyerek tekrar parse et
                    with st.sidebar:
                        with st.spinner("Görseller Cloudinary'e yükleniyor..."):
                            doc.element  # zaten bellekte
                            kayitlar = MuzayedeParser.parse(doc, upload_images=gorsel_yukle)

                    for k in kayitlar:
                        k["dosya_adi"] = uploaded_file.name

                    t0 = time.perf_counter()
                    self.eserler_repo.insert_many(kayitlar)
                    sure = time.perf_counter() - t0

                    gorsel_eklenen = sum(1 for k in kayitlar if k.get("gorsel_url"))
                    st.sidebar.success(
                        f"{len(kayitlar)} eser {sure:.2f} sn'de eklendi. "
                        f"({gorsel_eklenen} görsel Cloudinary'e yüklendi)"
                    )
                except Exception as e:
                    st.sidebar.error(f"Hata: {e}")

        except Exception as e:
            st.sidebar.error(f"Dosya okuma hatası: {e}")

    def _render_search(self):
        st.subheader("🔍 Eserlerde Ara ve Filtrele")

        col1, col2, col3 = st.columns([3, 1, 1])
        with col1:
            search_query = st.text_input(
                "Anahtar kelime (eser adı, sanatçı, sahip, detay)",
                placeholder="Örn. Ertuğrul Ateş, yağlıboya, Levent Gürel..."
            )
        with col2:
            lot_no_query = st.text_input("Lot No", placeholder="Örn. 37")
        with col3:
            sanatci_liste = [""] + self.eserler_repo.get_distinct_sanatcilar()
            sanatci_filtre = st.selectbox("Sanatçıya göre filtrele", sanatci_liste)

        sorgu = self._build_query(search_query, lot_no_query, sanatci_filtre)
        self._show_results(sorgu)

    def _build_query(self, search_query, lot_no_query, sanatci_filtre):
        sorgu = {}

        if lot_no_query.strip():
            try:
                sorgu["lot_no"] = int(lot_no_query.strip())
            except ValueError:
                pass

        if search_query:
            sorgu["$or"] = [
                {"eser_adi": {"$regex": search_query, "$options": "i"}},
                {"sanatci":  {"$regex": search_query, "$options": "i"}},
                {"sahip":    {"$regex": search_query, "$options": "i"}},
                {"detay":    {"$regex": search_query, "$options": "i"}},
            ]

        if sanatci_filtre:
            sorgu["sanatci"] = sanatci_filtre

        return sorgu

    def _show_results(self, sorgu):
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
                "Sonuç bulunamadı. Sol taraftan .docx dosyası yükleyip "
                "'Eserleri Veritabanına Ekle' ile havuzu doldurun."
            )

    def _display_results(self, items, sure_db):
        t1 = time.perf_counter()
        toplam = len(items)

        if toplam > self.GOSTERIM_LIMITI:
            st.info(f"İlk **{self.GOSTERIM_LIMITI}** kayıt gösteriliyor (toplam {toplam}).")
            items = items[:self.GOSTERIM_LIMITI]

        sure_islem = time.perf_counter() - t1
        self._show_metrics(sure_db, sure_islem, toplam)

        # Görsel olan ve olmayan eserler: kart görünümü
        self._render_cards(items)

    def _render_cards(self, items):
        """Her eseri görsel + bilgi kartı olarak göster."""
        cols = st.columns(self.KART_KOLONLARI)
        for idx, item in enumerate(items):
            col = cols[idx % self.KART_KOLONLARI]
            with col:
                gorsel_url = item.get("gorsel_url", "")
                if gorsel_url:
                    st.image(gorsel_url, use_container_width=True)
                else:
                    st.markdown(
                        "<div style='height:160px;background:#f0f0f0;border-radius:6px;"
                        "display:flex;align-items:center;justify-content:center;"
                        "color:#aaa;font-size:2rem;'>🖼</div>",
                        unsafe_allow_html=True
                    )

                lot  = item.get("lot_no", "")
                ad   = item.get("eser_adi") or "—"
                san  = item.get("sanatci") or "—"
                sah  = item.get("sahip") or "—"
                det  = item.get("detay") or ""
                dosya = item.get("dosya_adi") or ""

                st.markdown(
                    f"**Lot {lot} · {ad}**  \n"
                    f"*{san}*  \n"
                    f"<small style='color:#888'>{sah}</small>  \n"
                    f"<small>{det}</small>  \n"
                    f"<small style='color:#bbb'>{dosya}</small>",
                    unsafe_allow_html=True
                )
                st.markdown("---")

    def _show_metrics(self, sure_db, sure_islem, toplam):
        st.markdown("---")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Toplam süre", f"{sure_db + sure_islem:.2f} sn",
                      help="Veritabanı + render")
        with c2:
            st.metric("Veritabanı (MongoDB)", f"{sure_db:.2f} sn", help="find() sorgusu")
        with c3:
            st.metric("Hazırlama", f"{sure_islem:.2f} sn")
        with c4:
            st.metric("Sonuç sayısı", f"{toplam:,}")
        st.markdown("---")


# ==================== APPLICATION ====================

class Application:
    def __init__(self):
        self._setup_page()
        SessionManager.initialize()
        self.auth_service = AuthenticationService()
        self.eserler_repo = EserlerRepository()
        self.login_view = LoginView(self.auth_service)
        self.main_view = MainView(self.eserler_repo)

    @staticmethod
    def _setup_page():
        st.set_page_config(
            page_title="Müzayede Eser Havuzu",
            layout="wide",
            page_icon="favicon.png" if os.path.exists("favicon.png") else "logo.png",
        )

    def run(self):
        SessionManager.check_timeout()
        if SessionManager.is_authenticated():
            self.main_view.render()
        else:
            self.login_view.render()


# ==================== ENTRY POINT ====================

if __name__ == "__main__":
    app = Application()
    app.run()