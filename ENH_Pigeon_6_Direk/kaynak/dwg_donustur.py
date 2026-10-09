# -*- coding: utf-8 -*-
"""DXF (R2000) -> DWG (AC1015) donusumu, LibreDWG dxf2dwg ile.

R2000 DWG'de karsiligi olmayan MATERIAL / MLEADERSTYLE / VISUALSTYLE nesneleri
donusumden once ayiklanir (aksi halde tutamac cakismasi olusuyor).
Kullanim: python3 dwg_donustur.py <libredwg_bin_klasoru>
"""
import os
import subprocess
import sys
import tempfile
from collections import Counter

import ezdxf

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.dirname(HERE)
DXF = os.path.join(OUTDIR, "ENH_Pigeon_AER_6_Direk_Proje.dxf")
DWG = os.path.join(OUTDIR, "ENH_Pigeon_AER_6_Direk_Proje.dwg")


def ayikla(doc):
    for key in ("ACAD_MATERIAL", "ACAD_MLEADERSTYLE", "ACAD_VISUALSTYLE"):
        if key in doc.rootdict:
            d = doc.rootdict[key]
            for name, h in list(d.items()):
                obj = h if not isinstance(h, str) else doc.entitydb.get(h)
                d.discard(name)
                if obj is not None and obj.is_alive:
                    doc.objects.delete_entity(obj)
            doc.rootdict.remove(key)


def main(bin_dir):
    doc = ezdxf.readfile(DXF)
    ayikla(doc)
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "kaynak.dxf")
        doc.saveas(src)
        r = subprocess.run([os.path.join(bin_dir, "dxf2dwg"), "-y", "-o", DWG, src], capture_output=True, text=True)
        hatalar = [ln for ln in (r.stdout + r.stderr).splitlines() if "ERROR" in ln]
        print("dxf2dwg:", "temiz" if not hatalar else hatalar)
        geri = os.path.join(td, "geri.dxf")
        subprocess.run([os.path.join(bin_dir, "dwg2dxf"), "-y", "-o", geri, DWG], capture_output=True)
        a = Counter(e.dxftype() for e in ezdxf.readfile(DXF).modelspace())
        b = Counter(e.dxftype() for e in ezdxf.readfile(geri).modelspace())
        print("varlik sayilari ayni:", a == b, dict(b))
    with open(DWG, "rb") as f:
        print("DWG surumu:", f.read(6).decode())


if __name__ == "__main__":
    main(sys.argv[1])
