import numpy as np
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import os

# --- ŘEŠENÍ PRO FONT ---
# Možnost A: Zkuste vynutit promazání cache, pokud je nainstalován systémově
fm._load_fontmanager(try_read_cache=False)

# Možnost B (Nejbezpečnější): Zadejte přímou cestu k souboru fontu
# Změňte 'IoskeleyMono-Regular.ttf' na reálný název vašeho souboru s fontem
cesta_k_fontu = 'IoskeleyMono-Regular.ttf'

if os.path.exists(cesta_k_fontu):
    # Pokud soubor existuje vedle skriptu, načteme ho přímo
    prop = fm.FontProperties(fname=cesta_k_fontu)
    font_name = prop.get_name()
    plt.rcParams['font.family'] = font_name
else:
    # Pokud soubor vedle skriptu není, zkusíme ho volat jménem a doufáme v promazanou cache
    plt.rcParams['font.family'] = 'Ioskeley Mono'
# ------------------------

# Frekvenční rozsah (0.1 Hz až 100 MHz)
f = np.logspace(-1, 8, 2000)
s = 2j * np.pi * f

# Model OPA2192: DC zisk 140 dB, GBW = 10 MHz
AOL = 10**7 / (1 + s / (2 * np.pi * 1.0))
AOL_dB = 20 * np.log10(np.abs(AOL))

# Součástky
Cx = 100e-9
Rf = 100
Rg = 100

# --- 1. GRAF: NESTABILNÍ ---
omega_Z1 = 1 / (Rf * Cx)
f_Z1 = omega_Z1 / (2 * np.pi)
beta1_inv = 1 + s / (2 * np.pi * f_Z1)
beta1_inv_dB = 20 * np.log10(np.abs(beta1_inv))

plt.figure(figsize=(7, 6))
plt.semilogx(f, AOL_dB, 'b', linewidth=2, label='$A_{OL}$')
plt.semilogx(f, beta1_inv_dB, 'r', linewidth=2, label='$1/\\beta$')
plt.xlabel('Frekvence [Hz]', fontsize=11)
plt.ylabel('Zesílení [dB]', fontsize=11)
plt.ylim(-20, 150)
plt.xlim(0.1, 1e8)
plt.grid(True, which="both", ls="--", alpha=0.7)

plt.plot(f_Z1, 20 * np.log10(np.abs(1 + 2j * np.pi * f_Z1 / (2 * np.pi * f_Z1))), 'ro', markersize=6)
plt.text(f_Z1, -10, 'Nulový bod', ha='center', color='red', fontsize=11)

plt.legend(loc='upper right')
plt.tight_layout()
plt.savefig('lcr_uncompensated_exact.pdf')
plt.close()


# --- 2. GRAF: STABILNÍ ---
omega_Z2 = 1 / ((Rf + Rg) * Cx)
f_Z2 = omega_Z2 / (2 * np.pi)
omega_P2 = 1 / (Rg * Cx)
f_P2 = omega_P2 / (2 * np.pi)

beta2_inv = (1 + s / (2 * np.pi * f_Z2)) / (1 + s / (2 * np.pi * f_P2))
beta2_inv_dB = 20 * np.log10(np.abs(beta2_inv))

plt.figure(figsize=(7, 6))
plt.semilogx(f, AOL_dB, 'b', linewidth=2, label='$A_{OL}$')
plt.semilogx(f, beta2_inv_dB, 'r', linewidth=2, label='$1/\\beta$')
plt.xlabel('Frekvence [Hz]', fontsize=11)
plt.ylabel('Zesílení [dB]', fontsize=11)
plt.ylim(-20, 150)
plt.xlim(0.1, 1e8)
plt.grid(True, which="both", ls="--", alpha=0.7)

val_Z2 = 20 * np.log10(np.abs((1 + 1j) / (1 + 1j * f_Z2 / f_P2)))
plt.plot(f_Z2, val_Z2, 'ro', markersize=6)
plt.text(f_Z2, val_Z2 - 12, 'Nulový bod', ha='center', color='red', fontsize=11)

val_P2 = 20 * np.log10(np.abs((1 + 1j * f_P2 / f_Z2) / (1 + 1j)))
plt.plot(f_P2, val_P2, 'gx', markersize=8, mew=2)
plt.text(f_P2, val_P2 + 8, 'Pól', ha='center', color='green', fontsize=11)

plt.legend(loc='upper right')
plt.tight_layout()
plt.savefig('lcr_compensated_exact.pdf')
plt.close()
