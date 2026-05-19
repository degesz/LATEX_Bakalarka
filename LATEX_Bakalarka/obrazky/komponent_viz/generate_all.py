import os
import sys
import subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from numline import MeasurementLineSVG

OUTDIR = os.path.dirname(os.path.abspath(__file__))

components = [
    {
        "file": "R_DCR.svg",
        "label": "Rezistor",
        "min": 1.40, "max": 1.55, "major": 0.05, "minor": 0.01,
        "nom": 1.5, "ref": 1.454, "meas": 1.478,
        "unit": "kΩ",
    },
    {
        "file": "L_Toroid_1kHz.svg",
        "label": "Induktor toroidní",
        "min": 100, "max": 115, "major": 5, "minor": 1,
        "nom": None, "ref": 107, "meas": 109.42,
        "unit": "μH",
    },
    {
        "file": "L_SMD_1kHz.svg",
        "label": "Induktor SMD",
        "min": 11, "max": 14, "major": 0.5, "minor": 0.1,
        "nom": 12, "ref": 12.8, "meas": 13.22,
        "unit": "μH",
    },
    {
        "file": "C_Elektrolyt_1kHz.svg",
        "label": "Kondenzátor elektrolytický",
        "min": 80, "max": 160, "major": 20, "minor": 5,
        "nom": 150, "ref": 106, "meas": 126.56,
        "unit": "μF",
    },
    {
        "file": "C_10n_1kHz.svg",
        "label": "Kondenzátor polypropylenový 10n",
        "min": 9.0, "max": 10.5, "major": 0.5, "minor": 0.1,
        "nom": 10, "ref": 9.71, "meas": 9.78,
        "unit": "nF",
    },
    {
        "file": "C_470n_1kHz.svg",
        "label": "Kondenzátor polypropylenový 470n",
        "min": 440, "max": 480, "major": 10, "minor": 2,
        "nom": 470, "ref": 455, "meas": 452.94,
        "unit": "nF",
    },
]

for c in components:
    outpath = os.path.join(OUTDIR, c["file"])
    gen = MeasurementLineSVG(outpath)
    nom = c["nom"] if c["nom"] is not None else c["ref"]
    gen.generate(
        min_val=c["min"],
        max_val=c["max"],
        major_step=c["major"],
        minor_step=c["minor"],
        nominal=nom,
        measured=c["meas"],
        reference=c["ref"],
        unit=c["unit"],
        label=c["label"],
    )

for f in os.listdir(OUTDIR):
    if f.endswith(".svg"):
        svg_path = os.path.join(OUTDIR, f)
        pdf_path = svg_path.replace(".svg", ".pdf")
        subprocess.run(["inkscape", svg_path, "-o", pdf_path],
                       capture_output=True)
