# -*- coding: utf-8 -*-
"""First-run setup chooser kept outside the large plugin router."""

def choose_setup_path(dialog, tr=lambda x: x):
    labels = [
        tr('Nuvio — ربط الحساب والمزامنة'),
        tr('Stremio — استيراد الإضافات + متابعة المشاهدة'),
        tr('إعداد يدوي — Stremio / Plex / Emby بنفسي'),
    ]
    idx = dialog.select(tr('Quick Start • اختر طريقة الإعداد'), labels)
    if idx < 0:
        return None
    return ('nuvio', 'stremio', 'manual')[idx]
