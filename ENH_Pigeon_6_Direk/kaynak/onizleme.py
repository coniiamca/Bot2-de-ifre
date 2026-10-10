# -*- coding: utf-8 -*-
"""DXF paftalarini PDF/PNG olarak cizer (kontrol ve telefon goruntuleme icin)."""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import ezdxf  # noqa: E402
from ezdxf.addons.drawing import RenderContext, Frontend  # noqa: E402
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend  # noqa: E402
from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy, ColorPolicy, LineweightPolicy  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.dirname(HERE)
DXF = os.path.join(OUTDIR, "ENH_Pigeon_AER_6_Direk_Proje.dxf")


def ciz(dxf_path, pdf_path, png_prefix=None, dpi=110, bolge=None):
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    from ezdxf.lldxf.encoding import decode_dxf_unicode
    for e in msp.query("TEXT"):
        e.dxf.text = decode_dxf_unicode(e.dxf.text)
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK,
                        lineweight_policy=LineweightPolicy.ABSOLUTE, lineweight_scaling=0.6)
    paftalar = [(0, 0, 1189, 841), (1250, 0, 2439, 841)]
    with PdfPages(pdf_path) as pdf:
        for i, (x0, y0, x1, y1) in enumerate(paftalar):
            fig = plt.figure(figsize=(46.81, 33.11))           # A0 inç
            ax = fig.add_axes([0, 0, 1, 1])
            ctx = RenderContext(doc)
            be = MatplotlibBackend(ax)
            Frontend(ctx, be, config=cfg).draw_layout(msp, finalize=True,
                                                     filter_func=lambda e: _icinde(e, x0, y0, x1, y1))
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)
            ax.set_aspect("equal")
            ax.axis("off")
            pdf.savefig(fig)
            if png_prefix:
                fig.savefig(f"{png_prefix}_{i + 1}.png", dpi=dpi)
                if bolge:
                    for j, (bx0, by0, bx1, by1) in enumerate(bolge.get(i, [])):
                        ax.set_xlim(x0 + bx0, x0 + bx1)
                        ax.set_ylim(y0 + by0, y0 + by1)
                        fig.set_size_inches((bx1 - bx0) / 25.4, (by1 - by0) / 25.4)
                        fig.savefig(f"{png_prefix}_{i + 1}_z{j + 1}.png", dpi=150)
            plt.close(fig)


def _icinde(e, x0, y0, x1, y1):
    try:
        from ezdxf import bbox
        b = bbox.extents([e], fast=True)
        if not b.has_data:
            return True
        cx, cy = (b.extmin.x + b.extmax.x) / 2, (b.extmin.y + b.extmax.y) / 2
        return x0 - 5 <= cx <= x1 + 5 and y0 - 5 <= cy <= y1 + 5
    except Exception:
        return True


if __name__ == "__main__":
    pdf = os.path.join(OUTDIR, "ENH_Pigeon_AER_6_Direk_Proje_Paftalar.pdf")
    pref = sys.argv[1] if len(sys.argv) > 1 else None
    bolge = None
    if pref:
        bolge = {0: [(20, 560, 500, 831), (20, 120, 720, 545), (480, 420, 1179, 831), (700, 10, 1179, 520),
                     (20, 10, 700, 210)],
                 1: [(20, 480, 420, 831), (20, 200, 420, 500), (410, 560, 1179, 831), (410, 120, 1179, 580)]}
    ciz(DXF, pdf, pref, bolge=bolge)
    print("PDF:", pdf)
