import os
import re
import sys
import json
import hmac
import uuid
import hashlib
import datetime
import shutil
import sqlite3
import threading
import webbrowser
from urllib.parse import quote

import requests
import urllib3
import arabic_reshaper
from bidi.algorithm import get_display

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.button import Button
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.gridlayout import GridLayout
from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner, SpinnerOption
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.image import Image
from kivy.uix.behaviors import ButtonBehavior
from kivy.core.window import Window
from kivy.core.text import LabelBase, DEFAULT_FONT
from kivy.graphics import Color, RoundedRectangle, Rectangle, Line, Ellipse
from kivy.metrics import dp, sp
from kivy.utils import get_color_from_hex, platform
from kivy.clock import Clock

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ═════════════════════════════════════════════
# إعدادات قابلة للتعديل (غيّرها قبل النشر)
# ═════════════════════════════════════════════
APP_TITLE = "حاسبة الجمركة"
SECRET_KEY = b"CHANGE-THIS-SECRET-KEY-BEFORE-RELEASE"   # يجب أن يطابق keygen.py
WHATSAPP_NUMBER = "213553762791"                          # رقمك بصيغة دولية بدون +

TRIAL_DAILY_LIMIT = 10    # عدد الحسابات المختلفة المسموحة يومياً في النسخة المجانية
TRIAL_MAX_RESULTS = 15      # عدد نتائج البحث في النسخة المجانية
FULL_MAX_RESULTS = 50
CALC_VERSION = 2            # يزيد عند تغيير معادلات الحساب لإعادة حساب القيم المخزنة

# ─────────────────────────────────────────────
# الخط والألوان
# ─────────────────────────────────────────────
def find_asset(name):
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, name), os.path.join(os.getcwd(), name), name):
        if os.path.exists(p):
            return p
    return None


FONT_NAME = 'Roboto'
_font_path = find_asset('Sahel.ttf')
if _font_path:
    try:
        LabelBase.register(DEFAULT_FONT, _font_path)
        FONT_NAME = _font_path
    except Exception as _e:
        print("تنبيه: تعذّر تحميل الخط:", _e)
else:
    print("تنبيه: لم يتم العثور على خط Sahel.ttf (ضعه بجانب الملف).")


def ar_text(text):
    try:
        if not text:
            return ""
        return get_display(arabic_reshaper.reshape(str(text)))
    except Exception:
        return str(text)


C_BG = get_color_from_hex("#F5F2EA")
C_CARD = get_color_from_hex("#FFFFFF")
C_NAVY = get_color_from_hex("#0E3B43")
C_BLUE = get_color_from_hex("#127C84")
C_LIGHT_BLUE = get_color_from_hex("#EFEADF")
C_RED = get_color_from_hex("#D64545")
C_TEXT = get_color_from_hex("#2A2F36")
C_MUTED = get_color_from_hex("#7B7F86")
C_BORDER = get_color_from_hex("#E6E0D2")
C_GREEN = get_color_from_hex("#1F9D6B")
C_ORANGE = get_color_from_hex("#F2B84B")
C_WHITE = (1, 1, 1, 1)

# ─────────────────────────────────────────────
# أسعار الصرف وحساب الجمركة
# ─────────────────────────────────────────────
API_URL = (
    "https://alces.douane.gov.dz/api/public/com/main/selectFxrtList"
    "?searchCondition=&searchKeyword=&searchKeywordFrom=&searchKeywordTo="
    "&langCd=&pageIndex=0&pageUnit=10&pageSize=10&firstIndex=0&lastIndex=0"
    "&recordCountPerPage=10&totCnt=0&rnum=0&aplyBgnDtFrom=&aplyBgnDtTo="
    "&aplyBgnDt=&aplyEndDt=&aplyDt=&xtrnOfrYn=&xtrnOfrDttm=&xtrnOfrEmpId="
    "&currNm=&currCd=&fxrt=0&aprvId=&delYn=&frstRegstId=&frstRgsrDttm="
    "&lastChprId=&lastChgDttm=&locale=fr-FR&fxrtTpCd=&buyAmt=0&selAmt=0"
)

BASE_ADJUSTMENT_EUR = 600
BASE_ADJUSTMENT_USD = 1000

# نسب الرسوم
TVA = 0.19
TCS = 0.03          # الرسم التكميلي
TIC_DIESEL = 0.60   # الرسم الداخلي على الاستهلاك للديزل
DD_DIESEL_SMALL = 0.15   # حقوق جمركية للديزل حتى 2000 سم³
DD_DIESEL_BIG = 0.30     # حقوق جمركية للديزل فوق 2000 سم³
DIESEL_CYL_THRESHOLD = 2000


def tax_factor(dd, tic):
    """مجموع الحقوق والرسوم كنسبة من القيمة بالدينار."""
    a = 1 + dd + TCS
    return (a - 1) + a * tic + TVA * (a + a * tic)


# معاملات الديزل الجديد (مطابقة لحاسبة موقعك: 55.58% و153.23%)
F_DIESEL_NEW_SMALL = 0.5558   # ديزل أقل من 2000 سم³
F_DIESEL_NEW_BIG = 1.5323     # ديزل 2000 سم³ فأكثر


def safe_float(v):
    try:
        return float(v) if v else 0.0
    except Exception:
        return 0.0


def calc_fees(price_dzd, age, energie, cyl):
    """age: neuf / 1 / 2 / 3 ؛ تُرجع -1 إذا كان الاستيراد ممنوعاً."""
    if price_dzd <= 0:
        return 0.0
    is_diesel = energie == 'DSL'
    is_ess_big = energie == 'ESS' and cyl >= 1800

    if is_diesel:
        if age != 'neuf':
            return -1.0
        factor = F_DIESEL_NEW_SMALL if 0 < cyl < DIESEL_CYL_THRESHOLD else F_DIESEL_NEW_BIG
        return price_dzd * factor + 2500
    if is_ess_big:
        return price_dzd * (1.5323 if age == 'neuf' else 1.2258) + 2500
    return price_dzd * (0.4042 if age == 'neuf' else 0.2021) + 2500


def get_db_path():
    """يعيد مسار قاعدة البيانات. على أندرويد تُنسخ مرة واحدة إلى مجلد التطبيق الخاص
    (قابل للكتابة) ثم تُستخدم النسخة هناك."""
    db_name = "TechVault.db"
    script_dir = os.path.dirname(os.path.abspath(__file__))

    bundled = None
    for path in (os.path.join(script_dir, db_name), os.path.join(os.getcwd(), db_name)):
        if os.path.exists(path):
            bundled = path
            break

    private = os.environ.get("ANDROID_PRIVATE") or os.environ.get("ANDROID_APP_PATH")
    if private and os.path.isdir(private) and bundled:
        dest = os.path.join(private, db_name)
        try:
            if os.path.abspath(dest) != os.path.abspath(bundled):
                if (not os.path.exists(dest)) or os.path.getsize(dest) == 0:
                    shutil.copy2(bundled, dest)
            return dest
        except Exception:
            return bundled

    return bundled or os.path.join(script_dir, db_name)


DB_FILE = get_db_path()


CURRENT_RATES = {}          # آخر أسعار صرف معروفة {'EUR': .., 'USD': ..}
RATES_FILE_NAME = "rates.json"


def norm_devise(d):
    s = str(d or '').strip().upper()
    if 'USD' in s or '$' in s or 'DOLLAR' in s:
        return 'USD'
    if 'EUR' in s or '\u20ac' in s:
        return 'EUR'
    return ''


def norm_energie(e):
    s = str(e or '').strip().upper()
    if s in ('DSL', 'DIESEL', 'GASOIL', 'GO', 'D'):
        return 'DSL'
    if s in ('ESS', 'ESSENCE', 'PETROL', 'GAS'):
        return 'ESS'
    if s.startswith('HYB'):
        return 'HYB'
    if s.startswith('ELC') or s.startswith('ELE') or s == 'EV':
        return 'ELC'
    return s


def parse_cyl(cylindre):
    m = re.search(r'\d+(?:[.,]\d+)?', str(cylindre or ''))
    if not m:
        return 0.0
    try:
        cyl = float(m.group(0).replace(',', '.'))
    except Exception:
        return 0.0
    if 0 < cyl < 10:
        cyl *= 1000
    return cyl


def load_saved_rates():
    try:
        with open(os.path.join(get_storage_dir(), RATES_FILE_NAME), 'r', encoding='utf-8') as f:
            for k, v in json.load(f).items():
                if k in ('EUR', 'USD') and float(v) > 0:
                    CURRENT_RATES[k] = float(v)
    except Exception:
        pass


def save_rates():
    try:
        with open(os.path.join(get_storage_dir(), RATES_FILE_NAME), 'w', encoding='utf-8') as f:
            json.dump(CURRENT_RATES, f)
    except Exception:
        pass


def compute_row_fees(devise, prices, energie, cylindre, row_rate=None):
    """يحسب الرسوم مباشرة من السعر الخام وسعر الصرف. يُرجع None إذا تعذّر الحساب."""
    dev = norm_devise(devise)
    if not dev:
        return None
    taux = CURRENT_RATES.get(dev) or safe_float(row_rate)
    if taux <= 0:
        return None
    adj = BASE_ADJUSTMENT_EUR if dev == 'EUR' else BASE_ADJUSTMENT_USD

    p_neuf = safe_float(prices[0])
    if p_neuf <= 0:
        return None
    p_1 = safe_float(prices[1]) or p_neuf
    p_2 = safe_float(prices[2]) or p_1
    p_3 = safe_float(prices[3]) or p_2

    en = norm_energie(energie)
    cyl = parse_cyl(cylindre)

    bases = tuple((p + adj) * taux if p > 0 else 0.0 for p in (p_neuf, p_1, p_2, p_3))
    fees = tuple(calc_fees(b, a, en, cyl) for b, a in zip(bases, ('neuf', '1', '2', '3')))
    return {'taux': taux, 'dev': dev, 'adj': adj, 'fees': fees, 'bases': bases}


def update_database_rates_and_tva(new_rates):
    """يحفظ أسعار الصرف الجديدة ويحدّث القيم المخزنة في القاعدة (للاتساق فقط)."""
    CURRENT_RATES.update({k: v for k, v in new_rates.items() if v and v > 0})
    save_rates()
    conn = None
    updated = skipped = 0
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        cur.execute("SELECT id, devise, Prix_neuf, Prix_1, Prix_2, Prix_3, energie, cylindre, taux_dc FROM cars")
        for car_id, devise, p_neuf, p_1, p_2, p_3, energie, cylindre, row_rate in cur.fetchall():
            res = compute_row_fees(devise, (p_neuf, p_1, p_2, p_3), energie, cylindre, row_rate)
            if not res:
                skipped += 1
                continue
            taux = res['taux']
            neuf, an_1, an_2, an_3 = res['fees']
            cur.execute(
                "UPDATE cars SET taux_dc=?, neuf=?, an_1=?, an_2=?, an_3=? WHERE id=?",
                (taux, neuf, an_1, an_2, an_3, car_id),
            )
            updated += 1
        conn.commit()
    except Exception as e:
        return f"تم حفظ أسعار الصرف (تعذّر تحديث القاعدة: {e})"
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    msg = f"تم تحديث أسعار الجمركة ({updated} سيارة)"
    if skipped:
        msg += f" - تعذّر تحديث {skipped}"
    return msg


def search_customs_value(car_name="", origin="", limit=FULL_MAX_RESULTS):
    results = []
    if not os.path.exists(DB_FILE):
        return results, "قاعدة البيانات غير موجودة"
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE)
        cur = conn.cursor()
        query = ("SELECT marque, modele, cylindre, pays_origine, neuf, an_1, an_2, an_3, energie, "
                 "devise, Prix_neuf, Prix_1, Prix_2, Prix_3, taux_dc FROM cars WHERE 1=1")
        params = []
        if car_name.strip():
            query += " AND LOWER(marque || ' ' || modele || ' ' || IFNULL(cylindre, '')) LIKE ?"
            params.append(f"%{car_name.strip().lower()}%")
        if origin == "Europe":
            query += " AND (LOWER(pays_origine) LIKE '%europ%' OR LOWER(pays_origine) LIKE '%eur%')"
        elif origin == "Outside":
            query += " AND (LOWER(pays_origine) NOT LIKE '%europ%' AND LOWER(pays_origine) NOT LIKE '%eur%')"
        query += f" ORDER BY marque, modele LIMIT {int(limit)}"
        cur.execute(query, params)

        for row in cur.fetchall():
            marque = str(row[0]) if row[0] else ''
            modele = str(row[1]) if row[1] else ''
            cyl_raw = str(row[2]) if row[2] else ''
            energie = str(row[8]).strip().upper() if row[8] else ''

            cyl_fmt = ""
            if cyl_raw.strip():
                try:
                    c = float(cyl_raw.replace(',', '.'))
                    if c >= 800:
                        c /= 1000.0
                    cyl_fmt = f"{c:.1f}"
                except Exception:
                    cyl_fmt = cyl_raw.strip()

            full_name = f"{marque} {modele}".strip()
            if energie or cyl_fmt:
                full_name += " -"
                if energie:
                    full_name += f" {energie}"
                if cyl_fmt:
                    full_name += f" {cyl_fmt}"

            # الحساب المباشر من الأسعار الخام؛ وإلا نستعمل القيم المخزنة
            live = compute_row_fees(row[9], (row[10], row[11], row[12], row[13]), row[8], row[2], row[14])
            if live:
                neuf, an_1, an_2, an_3 = live['fees']
            else:
                neuf, an_1, an_2, an_3 = (safe_float(row[4]), safe_float(row[5]),
                                          safe_float(row[6]), safe_float(row[7]))

            results.append({
                'nom': full_name.strip(),
                'origine': str(row[3]) if row[3] else '---',
                'neuf': neuf, 'an_1': an_1, 'an_2': an_2, 'an_3': an_3,
                'live': bool(live),
                'bases': live['bases'] if live else None,
                'taux': live['taux'] if live else 0.0,
                'dev': live['dev'] if live else '',
                'adj': live['adj'] if live else 0,
            })
        return results, None
    except Exception as e:
        return results, str(e)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# ═════════════════════════════════════════════
# نظام التفعيل
# ═════════════════════════════════════════════
def get_storage_dir():
    """أول مجلد قابل للكتابة (يعمل في Pydroid 3 وفي الـ APK وعلى الكمبيوتر)."""
    bases = (os.path.dirname(os.path.abspath(__file__)), os.path.expanduser('~'), os.getcwd())
    for base in bases:
        try:
            test = os.path.join(base, '.kc_write_test')
            with open(test, 'w') as f:
                f.write('1')
            os.remove(test)
            return base
        except Exception:
            continue
    return os.getcwd()


def _is_pydroid():
    probe = ' '.join([sys.executable or '', sys.prefix or '', os.path.abspath(__file__), os.getcwd()]).lower()
    return 'pydroid' in probe or 'iiec' in probe


def _raw_device_id(folder):
    # داخل APK حقيقي: نحاول ANDROID_ID (ولا نلمس jnius داخل Pydroid لأنه قد يغلق التطبيق)
    if platform == 'android' and not _is_pydroid():
        try:
            from jnius import autoclass
            activity = autoclass('org.kivy.android.PythonActivity').mActivity
            secure = autoclass('android.provider.Settings$Secure')
            val = secure.getString(activity.getContentResolver(), secure.ANDROID_ID)
            if val:
                return str(val)
        except BaseException:
            pass
    # بديل: معرّف عشوائي يُحفظ في عدة أماكن
    paths = [os.path.join(folder, ".kc_device")] + [os.path.join(d, ".kc_dev") for d in _extra_dirs(folder)]
    val = ""
    for p in paths:
        try:
            with open(p, 'r') as f:
                val = f.read().strip()
            if val:
                break
        except Exception:
            continue
    if not val:
        val = uuid.uuid4().hex
    for p in paths:
        try:
            with open(p, 'w') as f:
                f.write(val)
        except Exception:
            pass
    return val


def get_device_id(folder):
    h = hashlib.sha256(_raw_device_id(folder).encode()).hexdigest()[:12].upper()
    return f"{h[0:4]}-{h[4:8]}-{h[8:12]}"


def _signature(device_id, days):
    msg = f"{device_id}|{int(days):04d}".encode()
    return hmac.new(SECRET_KEY, msg, hashlib.sha256).hexdigest()[:12].upper()


def verify_code(code, device_id):
    """الصيغة: DDDD-XXXX-XXXX-XXXX ؛ DDDD = عدد أيام الصلاحية (0000 = دائم)."""
    try:
        code = code.strip().upper().replace(' ', '')
        parts = code.split('-')
        if len(parts) != 4 or len(parts[0]) != 4 or not parts[0].isdigit():
            return False, 0
        days = int(parts[0])
        sig = ''.join(parts[1:])
        return hmac.compare_digest(sig, _signature(device_id, days)), days
    except Exception:
        return False, 0


def _extra_dirs(folder):
    """مجلدات إضافية لحفظ نسخ احتياطية من حالة الترخيص (حتى لا يكفي حذف ملف واحد)."""
    dirs = []
    home = os.path.expanduser('~')
    if home and os.path.abspath(home) != os.path.abspath(folder) and os.path.isdir(home) \
            and os.access(home, os.W_OK):
        dirs.append(home)
    if platform == 'android' and not _is_pydroid():
        try:
            from jnius import autoclass
            activity = autoclass('org.kivy.android.PythonActivity').mActivity
            ext = activity.getExternalFilesDir(None)
            if ext:
                dirs.append(str(ext.getAbsolutePath()))
        except BaseException:
            pass
    return dirs


def _sign(obj):
    msg = json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return hmac.new(SECRET_KEY, b'state|' + msg, hashlib.sha256).hexdigest()[:20]


def _read_signed(path):
    """يرجع (الحالة, البيانات): missing / ok / bad (ملف معدَّل أو تالف)."""
    if not os.path.exists(path):
        return 'missing', None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            wrapper = json.load(f)
        data = wrapper['d']
        if hmac.compare_digest(str(wrapper['s']), _sign(data)):
            return 'ok', data
    except Exception:
        pass
    return 'bad', None


def _write_signed(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump({'d': data, 's': _sign(data)}, f)
    except Exception:
        pass


class LicenseManager:
    def __init__(self, folder):
        self.folder = folder
        self.paths = [os.path.join(folder, "license.json")] + \
                     [os.path.join(d, ".kc_state") for d in _extra_dirs(folder)]
        self.path = self.paths[0]
        self.device_id = get_device_id(folder)
        self.data = self._load()
        self.save()     # يعيد كتابة كل النسخ فتُستعاد أي نسخة محذوفة

    def _load(self):
        states = [_read_signed(p) for p in self.paths]
        oks = [d for s, d in states if s == 'ok']
        if oks:
            return self._merge(oks)
        if any(s == 'bad' for s, _ in states):
            # ملف موجود لكنه معدَّل/تالف: نعتبر التجربة منتهية
            return {'tampered': True}
        return {}

    def _merge(self, items):
        today = self._today().isoformat()
        merged = dict(max(items, key=lambda d: d.get('last_seen', '')))
        best = []
        for d in items:
            u = d.get('usage', {})
            if u.get('date') == today and len(u.get('keys', [])) > len(best):
                best = list(u['keys'])
        merged['usage'] = {'date': today, 'keys': best}
        merged['last_seen'] = max((d.get('last_seen', '') for d in items), default='')
        if any(d.get('tampered') for d in items):
            merged['tampered'] = True
        for d in sorted(items, key=lambda d: d.get('activated_at', ''), reverse=True):
            if d.get('code'):
                merged['code'] = d['code']
                merged['activated_at'] = d.get('activated_at')
                break
        return merged

    def save(self):
        for p in self.paths:
            _write_signed(p, self.data)

    @staticmethod
    def _today():
        return datetime.date.today()

    def _clock_ok(self):
        try:
            last = datetime.date.fromisoformat(self.data.get('last_seen', '2000-01-01'))
        except Exception:
            last = datetime.date(2000, 1, 1)
        if self._today() < last - datetime.timedelta(days=1):
            return False
        if self._today() > last:
            self.data['last_seen'] = self._today().isoformat()
            self.save()
        return True

    def is_active(self):
        code = self.data.get('code')
        if not code:
            return False
        ok, days = verify_code(code, self.device_id)
        if not ok or not self._clock_ok():
            return False
        if days == 0:
            return True
        try:
            start = datetime.date.fromisoformat(self.data['activated_at'])
        except Exception:
            return False
        return self._today() <= start + datetime.timedelta(days=days)

    def days_left(self):
        ok, days = verify_code(self.data.get('code', ''), self.device_id)
        if not ok:
            return None
        if days == 0:
            return 0
        try:
            start = datetime.date.fromisoformat(self.data['activated_at'])
            return max((start + datetime.timedelta(days=days) - self._today()).days, 0)
        except Exception:
            return None

    def activate(self, code):
        ok, days = verify_code(code, self.device_id)
        if not ok:
            return False, "كود التفعيل غير صحيح أو مخصص لجهاز آخر"
        self.data.pop('tampered', None)
        self.data['code'] = code.strip().upper()
        self.data['activated_at'] = self._today().isoformat()
        self.data['last_seen'] = self._today().isoformat()
        self.save()
        return True, ("تم التفعيل بنجاح (اشتراك دائم)" if days == 0
                      else f"تم التفعيل بنجاح لمدة {days} يوماً")

    # ── حدود النسخة المجانية ──
    def _usage_keys(self):
        if self.data.get('tampered'):
            return ['_'] * TRIAL_DAILY_LIMIT      # ملف معدَّل: لا محاولات
        today = self._today().isoformat()
        usage = self.data.get('usage', {})
        if usage.get('date') != today:
            usage = {'date': today, 'keys': []}
            self.data['usage'] = usage
        return usage['keys']

    def remaining_free(self):
        return max(TRIAL_DAILY_LIMIT - len(self._usage_keys()), 0)

    def locked(self):
        """True إذا انتهت المحاولات المجانية والنسخة غير مفعّلة."""
        return (not self.is_active()) and self.remaining_free() <= 0

    def try_consume(self, key):
        if self.is_active():
            return True
        if key == getattr(self, '_last_key', None):
            return True                     # إعادة عرض النتيجة نفسها
        keys = self._usage_keys()
        if len(keys) >= TRIAL_DAILY_LIMIT:
            return False                    # قفل صارم بعد انتهاء المحاولات
        if key not in keys:
            keys.append(key)
            self.save()
        self._last_key = key
        return True


# ═════════════════════════════════════════════
# عناصر الواجهة (تخطيط نسبي يتكيف مع حجم الشاشة)
# ═════════════════════════════════════════════
C_HERO = get_color_from_hex("#0E3B43")
C_ACCENT = get_color_from_hex("#F2B84B")
C_ON_HERO = get_color_from_hex("#9CC3C7")
C_SOFT_RED = get_color_from_hex("#FF9E9E")
C_SPINNER = get_color_from_hex("#FFF3D6")
C_INK = get_color_from_hex("#12161B")          # لون النص داخل الحقول (تباين عالٍ)
C_HINT = get_color_from_hex("#8A8F96")

UI_SCALE = 1.0      # يُحسب عند التشغيل حسب أبعاد الشاشة


def fs(x):
    """حجم خط متكيّف مع الشاشة."""
    return sp(x * UI_SCALE)


def add_rtl(layout, *widgets):
    """يضيف العناصر بحيث يظهر أولها في أقصى اليمين (اتجاه عربي)."""
    for w in reversed(widgets):
        layout.add_widget(w)


def make_label(text, size=13, color=C_MUTED, weight=None, bold=False, halign='right'):
    kw = dict(text=ar_text(text), color=color, font_size=fs(size), bold=bold,
              halign=halign, valign='middle')
    if weight:
        kw['size_hint_y'] = weight
    lbl = Label(**kw)
    lbl.bind(size=lambda i, s: setattr(i, 'text_size', (s[0], s[1])))
    return lbl


class CardLayout(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(0, 0, 0, 0.06)
            self.shadow = RoundedRectangle(pos=(self.x, self.y - dp(4)), size=self.size, radius=[dp(30)])
            Color(*C_CARD)
            self.rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(30)])
            Color(*C_BORDER)
            self.line = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(30)), width=1)
        self.bind(pos=self._update, size=self._update)

    def _update(self, *args):
        self.shadow.pos = (self.x, self.y - dp(4))
        self.shadow.size = self.size
        self.rect.pos = self.pos
        self.rect.size = self.size
        self.line.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(30))


class HeroCard(BoxLayout):
    """بطاقة النتيجة الداكنة."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        with self.canvas.before:
            Color(*C_HERO)
            self._bg = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(34)])
            Color(1, 1, 1, 0.05)
            self._circle = Ellipse(pos=self.pos, size=(dp(90), dp(90)))
            Color(*C_ACCENT)
            self._bar = RoundedRectangle(pos=self.pos, size=(dp(40), dp(5)), radius=[dp(2.5)])
            Color(1, 1, 1, 0.14)
            self._line = Rectangle(pos=self.pos, size=(self.width, dp(1)))
        self.bind(pos=self._update, size=self._update)

    def _update(self, *args):
        self._bg.pos = self.pos
        self._bg.size = self.size
        self._circle.pos = (self.x + dp(16), self.top - dp(90) - dp(12))
        self._bar.pos = (self.right - dp(24) - dp(40), self.top - dp(18))
        self._line.pos = (self.x + dp(22), self.y + self.height * 0.20 + dp(8))
        self._line.size = (max(self.width - dp(44), 0), dp(1))


class RoundButton(Button):
    """زر على شكل قرص (نهاياته مستديرة بالكامل)."""

    def __init__(self, bg=C_BLUE, pill=True, **kwargs):
        kwargs.setdefault('color', C_WHITE)
        kwargs.setdefault('bold', True)
        super().__init__(background_normal='', background_down='', background_color=(0, 0, 0, 0), **kwargs)
        self._pill = pill
        with self.canvas.before:
            self._c = Color(*bg)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(18)])
        self.bind(pos=self._update, size=self._update)

    def _update(self, *args):
        self._r.pos = self.pos
        self._r.size = self.size
        if self._pill:
            self._r.radius = [self.height / 2.0]
        # توسيط النص عمودياً داخل الحقل
        try:
            ph = max((self.height - self.line_height) / 2.0, 0)
            self.padding = [dp(20), ph, dp(20), ph]
        except Exception:
            pass

    def set_bg(self, color):
        self._c.rgba = color


class SegButton(ToggleButton):
    """زر اختيار على شكل قرص (يعمل كخيار ضمن مجموعة)."""

    def __init__(self, **kwargs):
        kwargs.setdefault('font_size', fs(13))
        super().__init__(background_normal='', background_down='', background_color=(0, 0, 0, 0), **kwargs)
        with self.canvas.before:
            self._c = Color(*C_LIGHT_BLUE)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(18)])
        self.bind(pos=self._update, size=self._update, state=self._on_state)
        self._on_state()

    def _update(self, *args):
        self._r.pos = self.pos
        self._r.size = self.size
        self._r.radius = [self.height / 2.0]

    def _on_state(self, *args):
        if self.state == 'down':
            self._c.rgba = C_NAVY
            self.color = C_WHITE
            self.bold = True
        else:
            self._c.rgba = C_LIGHT_BLUE
            self.color = C_TEXT
            self.bold = False


class RoundInput(TextInput):
    """حقل إدخال بحواف مستديرة."""

    def __init__(self, bg=C_LIGHT_BLUE, **kwargs):
        kwargs.setdefault('background_normal', '')
        kwargs.setdefault('background_active', '')
        super().__init__(background_color=(0, 0, 0, 0), **kwargs)
        # الخلفية المستديرة تُرسم في الحاوية (حتى لا تغطي النص)
        self.bind(size=self._update)

    def _update(self, *args):
        # توسيط النص عمودياً داخل الحقل
        try:
            ph = max((self.height - self.line_height) / 2.0, 0)
            self.padding = [dp(20), ph, dp(20), ph]
        except Exception:
            pass


class PillOption(SpinnerOption):
    """عنصر في القائمة المنسدلة: خلفية فاتحة ونص داكن واضح."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.background_normal = ''
        self.background_down = ''
        self.background_color = get_color_from_hex("#FBF7EC")
        self.color = C_INK
        self.font_size = fs(14)
        self.size_hint_y = None
        self.height = dp(46 * UI_SCALE)


class PillSpinner(Spinner):
    """قائمة منسدلة بحواف مستديرة."""

    def __init__(self, bg=C_SPINNER, **kwargs):
        super().__init__(background_normal='', background_color=(0, 0, 0, 0), **kwargs)
        with self.canvas.before:
            Color(*bg)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(20)])
            Color(*C_ACCENT)
            self._edge = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(20)), width=1.2)
        self.bind(pos=self._update, size=self._update)

    def _update(self, *args):
        rad = self.height / 2.0
        self._r.pos = self.pos
        self._r.size = self.size
        self._r.radius = [rad]
        self._edge.rounded_rectangle = (self.x, self.y, self.width, self.height, rad)


class CalculatorUI(BoxLayout):
    def __init__(self, user_dir, **kwargs):
        super().__init__(orientation='vertical', padding=[dp(14), dp(10), dp(14), dp(12)], spacing=dp(12))
        with self.canvas.before:
            Color(*C_BG)
            self.bg = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=lambda w, v: setattr(self.bg, 'pos', v), size=lambda w, v: setattr(self.bg, 'size', v))

        self.license = LicenseManager(user_dir)
        self.current_results = []
        self.car_map = {}
        self._popup = None
        self._limit_popup_shown = False
        self._silent = False
        self.origin_value = "Europe"
        self.age_key = "neuf"

        load_saved_rates()

        self.build_ui()
        self.refresh_license_ui()
        self._do_search(0, silent=True)     # تعبئة القائمة دون استهلاك أي محاولة
        Clock.schedule_once(lambda dt: self.start_api_update(None, auto=True), 0.5)

    # ── البناء: كل الأقسام بأوزان نسبية فتملأ الشاشة مهما كان حجمها ──
    def build_ui(self):
        self.add_widget(self._build_header())
        self.add_widget(self._build_hero())
        self.add_widget(self._build_form())

        self.status_label = Label(text=ar_text("جاري التحقق من أسعار الصرف..."), color=C_MUTED,
                                  font_size=fs(12), size_hint_y=0.05)
        self.add_widget(self.status_label)

    def _build_header(self):
        header = BoxLayout(orientation='horizontal', size_hint_y=0.09, spacing=dp(8))
        # الشارة هي زر التفعيل
        self.badge = RoundButton(bg=C_ACCENT, color=C_NAVY, text=ar_text("تفعيل"),
                                 size_hint=(None, 0.66), width=dp(104 * UI_SCALE),
                                 font_size=fs(14), pos_hint={'center_y': 0.5})
        self.badge.bind(on_release=lambda *_: self.open_activation())
        header.add_widget(self.badge)
        header.add_widget(make_label(APP_TITLE, size=26, color=C_NAVY, bold=True))
        return header

    def _build_hero(self):
        hero = HeroCard(orientation='vertical', size_hint_y=0.33,
                        padding=[dp(22), dp(24), dp(22), dp(10)], spacing=dp(0))
        hero.bind(on_touch_down=self._hero_touch)

        self.hero_car = make_label("اختر سيارة", size=14, color=C_ON_HERO, weight=0.15)
        self.hero_car.shorten = True
        hero.add_widget(self.hero_car)

        self.result_value = Label(text="0.00", color=C_WHITE, font_size=fs(54), bold=True, size_hint_y=0.45)
        hero.add_widget(self.result_value)

        self.result_title = Label(text=ar_text("تكلفة الجمركة التقريبية بالمليون سنتيم"),
                                  color=C_ACCENT, font_size=fs(13.5), size_hint_y=0.14)
        hero.add_widget(self.result_title)

        bottom = BoxLayout(size_hint_y=0.26, padding=[0, dp(10), 0, 0])
        self.rate_label = make_label("", size=12.5, color=C_ON_HERO, halign='right')
        self.result_note = make_label("", size=12.5, color=C_ON_HERO, halign='left')
        add_rtl(bottom, self.rate_label, self.result_note)
        hero.add_widget(bottom)
        return hero

    def _hero_touch(self, widget, touch):
        # عند القفل: لمس بطاقة النتيجة يفتح نافذة التفعيل
        if widget.collide_point(*touch.pos) and self.license.locked():
            self.open_activation()
            return True
        return False

    def _build_form(self):
        card = CardLayout(orientation='vertical', size_hint_y=0.53,
                          padding=[dp(18), dp(16)], spacing=dp(10))

        search_box = FloatLayout(size_hint_y=0.17)
        self.search_input = RoundInput(
            multiline=False, size_hint=(1, 1), pos_hint={'x': 0, 'y': 0},
            foreground_color=C_INK, cursor_color=C_BLUE,
            font_name=FONT_NAME, font_size=fs(14.5), halign='right')
        self.search_input.bind(text=self.on_input_change)
        # نص إرشادي كعنصر مستقل (أضمن ظهوراً من hint_text)
        self.search_hint = Label(
            text=ar_text("ابحث عن سيارة: النوع أو الموديل (مثال: Clio)"),
            color=C_HINT, font_size=fs(14), size_hint=(1, 1), pos_hint={'x': 0, 'y': 0},
            halign='right', valign='middle')
        self.search_hint.bind(size=lambda i, v: setattr(i, 'text_size', (v[0] - dp(40), v[1])))
        self.search_input.bind(text=lambda i, v: setattr(self.search_hint, 'opacity', 0 if v else 1))
        with search_box.canvas.before:
            Color(*C_LIGHT_BLUE)
            _bg = RoundedRectangle(pos=search_box.pos, size=search_box.size, radius=[dp(20)])
        search_box.bind(pos=lambda w, v: setattr(_bg, 'pos', v),
                        size=lambda w, v: (setattr(_bg, 'size', v), setattr(_bg, 'radius', [v[1] / 2.0])))
        search_box.add_widget(self.search_input)
        search_box.add_widget(self.search_hint)
        card.add_widget(search_box)

        card.add_widget(make_label("مكان الصنع", size=12.5, weight=0.07))
        origin_row = BoxLayout(size_hint_y=0.14, spacing=dp(8))
        origin_btns = []
        for key, label in (("All", "الكل"), ("Europe", "أوروبا"), ("Outside", "خارج أوروبا")):
            b = SegButton(text=ar_text(label), group='origin', allow_no_selection=False,
                          state='down' if key == self.origin_value else 'normal', font_size=fs(14))
            b.bind(on_release=lambda inst, k=key: self.set_origin(k))
            origin_btns.append(b)
        add_rtl(origin_row, *origin_btns)
        card.add_widget(origin_row)

        card.add_widget(make_label("السيارة", size=12.5, weight=0.07))
        self.car_spinner = PillSpinner(
            text=ar_text("-- اضغط هنا لاختيار السيارة --"), values=(),
            size_hint_y=0.17, color=C_INK, bold=True, font_size=fs(15), text_autoupdate=True,
            option_cls=PillOption)
        self.car_spinner.bind(text=self.calculate_price)
        card.add_widget(self.car_spinner)

        card.add_widget(make_label("عمر السيارة", size=12.5, weight=0.07))
        age_row = BoxLayout(size_hint_y=0.14, spacing=dp(6))
        age_btns = []
        for key, label in (("neuf", "جديدة"), ("an_1", "أقل من سنة"),
                           ("an_2", "1 - 2 سنة"), ("an_3", "2 - 3 سنوات")):
            b = SegButton(text=ar_text(label), group='age', allow_no_selection=False,
                          state='down' if key == self.age_key else 'normal', font_size=fs(12))
            b.bind(on_release=lambda inst, k=key: self.set_age(k))
            age_btns.append(b)
        add_rtl(age_row, *age_btns)
        card.add_widget(age_row)
        return card

    # ── حالة الترخيص ──
    def refresh_license_ui(self):
        if self.license.is_active():
            left = self.license.days_left()
            self.badge.text = ar_text("مفعّلة")
            self.badge.set_bg(C_GREEN)
            self.badge.color = C_WHITE
            self.result_note.text = ar_text("" if not left else f"متبقي {left} يوماً")
        else:
            self.badge.text = ar_text("تفعيل")
            self.badge.set_bg(C_ACCENT)
            self.badge.color = C_NAVY
            left = self.license.remaining_free()
            if left <= 0:
                self.result_note.text = ar_text("انتهت المحاولات - اضغط للتفعيل")
            else:
                self.result_note.text = ar_text(f"متبقي {left} عملية اليوم")

    def open_activation(self):
        if self._popup:
            return
        box = BoxLayout(orientation='vertical', padding=dp(16), spacing=dp(10))

        def lbl(text, color=C_TEXT, size=13, h=26):
            l = Label(text=ar_text(text), color=color, font_size=sp(size), size_hint_y=None,
                      height=dp(h), halign='right')
            l.bind(size=lambda i, s: setattr(i, 'text_size', (s[0], None)))
            return l

        if self.license.is_active():
            box.add_widget(lbl("✓ النسخة مفعّلة على هذا الجهاز", C_GREEN, 15, 30))
        else:
            box.add_widget(lbl("أرسل معرّف الجهاز إلينا لنزوّدك بكود التفعيل:", C_TEXT, 13, 26))

        id_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        copy_btn = RoundButton(bg=C_BLUE, text=ar_text("نسخ"), size_hint_x=0.28, font_size=sp(13))
        def do_copy(*_):
            try:
                from kivy.core.clipboard import Clipboard
                Clipboard.copy(self.license.device_id)
                status.color = C_GREEN
                status.text = ar_text("تم نسخ المعرّف")
            except Exception:
                status.color = C_ORANGE
                status.text = ar_text("تعذّر النسخ، انسخ المعرّف يدوياً")

        copy_btn.bind(on_release=do_copy)
        dev = Label(text=self.license.device_id, color=C_NAVY, font_size=sp(18), bold=True)
        id_row.add_widget(copy_btn)
        id_row.add_widget(dev)
        box.add_widget(id_row)

        box.add_widget(lbl("أدخل كود التفعيل:", C_TEXT, 13, 26))
        code_input = TextInput(multiline=False, size_hint_y=None, height=dp(44), hint_text="0000-XXXX-XXXX-XXXX",
                               background_normal='', background_active='', background_color=C_LIGHT_BLUE,
                               foreground_color=C_TEXT, cursor_color=C_BLUE, padding=[dp(12), dp(12)],
                               write_tab=False)
        box.add_widget(code_input)

        status = Label(text="", color=C_RED, font_size=sp(12), size_hint_y=None, height=dp(24))
        box.add_widget(status)

        btns = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        wa_btn = RoundButton(bg=C_GREEN, text=ar_text("اطلب الكود"), font_size=sp(13))
        def open_whatsapp(*_):
            text = (f"السلام عليكم، أريد تفعيل تطبيق {APP_TITLE}\n"
                    f"معرّف الجهاز: {self.license.device_id}")
            webbrowser.open(f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(text)}")

        wa_btn.bind(on_release=open_whatsapp)
        act_btn = RoundButton(bg=C_BLUE, text=ar_text("تفعيل"), font_size=sp(14))

        def do_activate(*_):
            ok, msg = self.license.activate(code_input.text)
            status.color = C_GREEN if ok else C_RED
            status.text = ar_text(msg)
            if ok:
                self.refresh_license_ui()
                Clock.schedule_once(lambda dt: popup.dismiss(), 1.2)

        act_btn.bind(on_release=do_activate)
        btns.add_widget(wa_btn)
        btns.add_widget(act_btn)
        box.add_widget(btns)

        feats = ("النسخة المجانية: %d عمليات حساب يومياً ونتائج بحث محدودة" % TRIAL_DAILY_LIMIT)
        box.add_widget(lbl(feats, C_MUTED, 11, 22))

        popup = Popup(title=ar_text("تفعيل التطبيق"), title_font=FONT_NAME, title_align='center',
                      content=box, size_hint=(0.92, None), height=dp(400), auto_dismiss=True,
                      separator_color=C_BLUE)

        def on_dismiss(*_):
            self._popup = None
            self.refresh_license_ui()
            if self.license.is_active():
                self._limit_popup_shown = False
                self._do_search(0)

        popup.bind(on_dismiss=on_dismiss)
        self._popup = popup
        popup.open()

    # ── البحث والحساب ──
    def set_origin(self, key):
        self.origin_value = key
        self._do_search(0)

    def set_age(self, key):
        self.age_key = key
        self.calculate_price()

    def on_input_change(self, *args):
        # تأخير بسيط حتى لا يُحسب كل حرف يُكتب ويستهلك محاولة
        Clock.unschedule(self._do_search)
        Clock.schedule_once(self._do_search, 0.6)

    def _show_idle(self):
        self.result_value.text = "0.00"
        self.result_value.color = C_WHITE
        self.result_value.font_size = fs(54)
        self.result_title.text = ar_text("تكلفة الجمركة التقريبية بالمليون سنتيم")
        self.hero_car.text = ar_text("اختر سيارة")
        self.rate_label.text = ""

    def _do_search(self, dt, silent=False):
        db_origin = self.origin_value if self.origin_value in ("Europe", "Outside") else ""

        limit = FULL_MAX_RESULTS if self.license.is_active() else TRIAL_MAX_RESULTS
        results, err = search_customs_value(self.search_input.text, db_origin, limit)
        self.current_results = results
        self.car_map = {ar_text(r['nom']): r for r in results}

        if err:
            self.result_title.text = ar_text(err)
            self.car_spinner.values = []
            self.car_spinner.text = ar_text("-- خطأ في قاعدة البيانات --")
            return

        if not results:
            self.car_spinner.values = []
            self._silent = True
            self.car_spinner.text = ar_text("-- لا توجد نتائج --")
            self._silent = False
            self._show_idle()
            self.hero_car.text = ar_text("لا توجد نتائج")
            return

        names = list(self.car_map.keys())
        self.car_spinner.values = names
        self._silent = True
        if silent:
            # عند فتح التطبيق: لا نحسب ولا نستهلك محاولة
            self.car_spinner.text = ar_text("-- اضغط هنا لاختيار السيارة --")
            self._silent = False
            self._show_idle()
        else:
            self.car_spinner.text = names[0]
            self._silent = False
            self.calculate_price()

    def _set_rate(self, car):
        if car and car.get('live') and car.get('taux'):
            self.rate_label.text = ar_text(f"سعر الصرف: {car['taux']:.2f}")
        else:
            self.rate_label.text = ""

    def calculate_price(self, *args):
        if self._silent:
            return
        car = self.car_map.get(self.car_spinner.text)
        self._show_idle()
        if not car:
            return

        self.hero_car.text = self.car_spinner.text
        price = car.get(self.age_key, 0.0)
        if price == 0.0:
            return

        # قفل صارم: بعد انتهاء المحاولات لا يُسمح بأي نتيجة جديدة
        if not self.license.try_consume(f"{car['nom']}|{self.age_key}"):
            self.result_value.text = ar_text("مقفل")
            self.result_value.font_size = fs(40)
            self.result_value.color = C_SOFT_RED
            self.result_title.text = ar_text("انتهت المحاولات المجانية - اضغط للتفعيل")
            self.refresh_license_ui()
            if not self._limit_popup_shown:
                self._limit_popup_shown = True
                Clock.schedule_once(lambda dt: self.open_activation(), 0.2)
            return

        if price == -1.0:
            self.result_value.text = ar_text("ممنوع")
            self.result_value.font_size = fs(38)
            self.result_value.color = C_SOFT_RED
            self.result_title.text = ar_text("سيارات الديزل المستعملة ممنوعة من الاستيراد")
        else:
            self.result_value.text = f"{price / 10000.0:,.2f}"
            self._set_rate(car)

        self.refresh_license_ui()
        if not car.get('live', True):
            self.result_note.text = ar_text("قيمة مخزنة قديمة")

    # ── تحديث الأسعار ──
    def start_api_update(self, instance, auto=False):
        if not auto:
            self.status_label.text = ar_text("جاري جلب أسعار الصرف والتحديث...")
            self.status_label.color = C_BLUE
        threading.Thread(target=self._fetch_and_update_task, args=(auto,), daemon=True).start()

    def _fetch_and_update_task(self, auto):
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(API_URL, headers=headers, verify=False, timeout=5 if auto else 15)
            if response.status_code != 200:
                msg = "لا يوجد اتصال: تم استخدام آخر أسعار مسجلة" if auto else f"خطأ في الاتصال: {response.status_code}"
                Clock.schedule_once(lambda dt: self._set_status(msg, C_ORANGE if auto else C_RED))
                return

            new_rates = {}
            for item in response.json().get("resultList", []):
                if item.get("currCd") in ("EUR", "USD"):
                    new_rates[item["currCd"]] = float(item.get("selAmt", 0))

            if not new_rates:
                Clock.schedule_once(lambda dt: self._set_status("لم يتم العثور على بيانات الصرف", C_ORANGE))
                return

            msg = update_database_rates_and_tva(new_rates)
            Clock.schedule_once(lambda dt: self._set_status(msg, C_GREEN))
            Clock.schedule_once(lambda dt: self._refresh_after_update())
        except Exception:
            msg = "لا يوجد إنترنت: يتم استخدام آخر أسعار مسجلة" if auto else "فشل التحديث: يرجى التحقق من الإنترنت"
            Clock.schedule_once(lambda dt: self._set_status(msg, C_ORANGE))

    def _set_status(self, msg, color):
        self.status_label.text = ar_text(msg)
        self.status_label.color = color

    def _refresh_after_update(self):
        """بعد التحديث التلقائي للأسعار: نجدد البيانات دون استهلاك أي محاولة تجريبية."""
        cur = self.car_spinner.text
        db_origin = self.origin_value if self.origin_value in ("Europe", "Outside") else ""
        limit = FULL_MAX_RESULTS if self.license.is_active() else TRIAL_MAX_RESULTS
        results, err = search_customs_value(self.search_input.text, db_origin, limit)
        if err or not results:
            return
        self.current_results = results
        self.car_map = {ar_text(r['nom']): r for r in results}
        self.car_spinner.values = list(self.car_map.keys())
        car = self.car_map.get(cur)
        if car:
            key = f"{car['nom']}|{self.age_key}"
            if self.license.is_active() or key == getattr(self.license, '_last_key', None):
                self.calculate_price()


def log_crash(text):
    try:
        path = os.path.join(get_storage_dir(), "crash_log.txt")
        with open(path, 'a', encoding='utf-8') as f:
            f.write(datetime.datetime.now().isoformat() + "\n" + text + "\n\n")
    except Exception:
        pass


class MainApp(App):
    def build(self):
        Window.clearcolor = C_BG
        if platform not in ('android', 'ios'):
            Window.size = (420, 780)
        self.title = APP_TITLE
        global UI_SCALE
        try:
            w_dp = Window.width / dp(1)
            h_dp = Window.height / dp(1)
            UI_SCALE = max(0.9, min(1.3, min(w_dp / 360.0, h_dp / 700.0)))
        except Exception:
            UI_SCALE = 1.0
        try:
            return CalculatorUI(user_dir=get_storage_dir())
        except Exception:
            import traceback
            err = traceback.format_exc()
            log_crash(err)
            print(err)
            # نعرض الخطأ على الشاشة بدل الخروج الصامت
            sv = ScrollView()
            lbl = TextInput(text=err, readonly=True, background_color=(1, 1, 1, 1),
                            foreground_color=(0.7, 0, 0, 1), font_size=sp(12))
            sv.add_widget(lbl)
            return lbl


if __name__ == '__main__':
    try:
        MainApp().run()
    except Exception:
        import traceback
        log_crash(traceback.format_exc())
        raise