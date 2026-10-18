[app]

# ---- المعلومات الأساسية
title = Douane Calculator
package.name = douanecalculator
package.domain = org.douane

# ---- الملفات (يجب أن يكون الملف الرئيسي باسم main.py)
source.dir = .
source.include_exts = py,png,ttf,db
source.exclude_dirs = bin, .buildozer, venv, __pycache__, .git, .github
source.exclude_patterns = *.pem, *.csv, *.log

version = 1.0

# ---- المكتبات (keygen لا يحتاج requests ولا غيرها)
requirements = python3,kivy==2.3.0,arabic-reshaper==3.0.1,python-bidi==0.4.2,six,future

# ---- الواجهة
orientation = portrait
fullscreen = 0

icon.filename = %(source.dir)s/icon.png
presplash.filename = %(source.dir)s/splash.png
presplash.color = #0E2623

# ---- أندرويد (نفس الإعدادات التي اشتغلت في التطبيق السابق)
android.api = 33
android.minapi = 24
android.ndk = 25b
android.ndk_api = 24
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.allow_backup = False

# ---- python-for-android
p4a.branch = v2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
