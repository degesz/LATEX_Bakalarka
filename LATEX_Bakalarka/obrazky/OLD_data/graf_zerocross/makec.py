import numpy as np
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from pathlib import Path

# --- 1. Font Handling Section (Linux Fix) ---
# Copied and adapted from your request
target_font_str = "IoskeleyMono_Regular"  # Substring to search for in filename
font_loaded = False

# 1. Define standard Linux font directories (System + User)
# specific focus on .local/share/fonts where user fonts live on Linux
search_dirs = [
    Path.home() / ".local/share/fonts",
    Path.home() / ".fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts")
]

print(f"Searching for font files matching '{target_font_str}'...")

# 2. Walk through directories to find the physical file
found_file = None
for directory in search_dirs:
    if not directory.exists():
        continue

    # Recursively search for .ttf or .otf files
    # We look for the 'target_font_str' in the filename (case insensitive)
    for file_path in directory.rglob("*.[ot]tf"):
        if target_font_str.lower() in file_path.name.lower():
            # Filter out Bold/Italic if we want the regular version
            if "bold" not in file_path.name.lower() and "italic" not in file_path.name.lower():
                found_file = file_path
                break
    if found_file:
        break

# 3. Load the font if found
if found_file:
    try:
        # Add font to Matplotlib's manager
        fm.fontManager.addfont(str(found_file))

        # Create a property object to get the exact internal name
        prop = fm.FontProperties(fname=str(found_file))
        internal_name = prop.get_name()

        # Set as default
        plt.rcParams['font.family'] = internal_name
        print(f"SUCCESS: Loaded '{internal_name}' from {found_file}")
        font_loaded = True
    except Exception as e:
        print(f"Error loading font file: {e}")

if not font_loaded:
    print(f"WARNING: Could not find a file matching '{target_font_str}'. Using Monospace fallback.")
    plt.rcParams['font.family'] = 'monospace'


# --- 2. Signal Generation ---

# Constants
f = 100e3  # 100 kHz
T = 1/f    # Period (10 microseconds)
duration = 3 * T  # Show 3 periods
fs = 100 * f      # Sampling frequency

# Time vector
t = np.arange(0, duration, 1/fs)
t_us = t * 1e6  # Time in microseconds

def generate_signals(phase_deg):
    phase_rad = np.deg2rad(phase_deg)
    # Sine wave: 5Vpp -> Amplitude 2.5V
    sine_wave = 2.5 * np.sin(2 * np.pi * f * t - phase_rad)
    # Square wave: 0-3.3V
    # Logic: High when sine >= 0
    square_wave = np.where(sine_wave >= 0, 3.3, 0)
    return sine_wave, square_wave

# Data Generation
# Chart 1: 0 degrees
sin1, sq1 = generate_signals(0)

# Chart 2: 45 degrees shift
sin2, sq2 = generate_signals(45)

# Chart 3: XOR of the two square waves
# Logic: High (3.3V) if inputs are different
xor_bool = np.logical_xor(sq1 > 1.65, sq2 > 1.65)
xor_sig = np.where(xor_bool, 3.3, 0)
xor_mean = np.mean(xor_sig)


# --- 3. Plotting ---

# Offsets for vertical stacking
# Signals are roughly ~6V tall (-2.5 to 3.3)
# We use 10V slots to provide good spacing
offset1 = 14  # Top
offset2 = 7  # Middle
offset3 = 0   # Bottom

fig, ax = plt.subplots(figsize=(8, 8)) # 1:1 Aspect Ratio

# Group 1 (Top) - Offset 20
# Sine (0°): Dark Blue
ax.plot(t_us, sin1 + offset1, label=r'$U_{napětí}$', color='darkred', linewidth=1)
# Square (0°): Teal
ax.plot(t_us, sq1 + offset1, label=r'$U_{napětí - pulz}$', color='teal', linestyle='-', linewidth=1)
#ax.axhline(offset1, color='darkgray', linestyle='-', alpha=1, linewidth=0.5)

# Group 2 (Middle) - Offset 10
# Sine (45°): Dark Orange
ax.plot(t_us, sin2 + offset2, label=r'$U_{proud}$', color='darkorange', linewidth=1)
# Square (45°): Dark Green
ax.plot(t_us, sq2 + offset2, label=r'$U_{proud - pulz}$', color='darkgreen', linestyle='-', linewidth=1)
#ax.axhline(offset2, color='darkgray', linestyle='-', alpha=1, linewidth=0.5)

# Group 3 (Bottom) - Offset 0
# XOR Signal: Black
ax.plot(t_us, xor_sig + offset3, label=r'$U_{XOR}$', color='black', linewidth=1)
# Mean value: Black Dashed Line
ax.plot(t_us, np.full_like(t_us, xor_mean) + offset3, color='black', linestyle='--', linewidth=1, label=r'$U_{XOR - stř}$')
#ax.axhline(offset3, color='darkgray', linestyle='-', alpha=1, linewidth=0.5)

# Formatting
ax.set_xlabel('Čas [μs]')
ax.set_ylabel('Napětí [U]')

# Grid going all the way through
ax.grid(True, which='both', linestyle='--', alpha=0.55, linewidth=1)

# Adjust limits
ax.set_ylim(-1.5, 18.5)
ax.set_xlim(t_us[0], t_us[-1])

# Legend - De-duplicate labels
handles, labels = ax.get_legend_handles_labels()
by_label = dict(zip(labels, handles))
ax.legend(by_label.values(), by_label.keys(), loc='upper right')

plt.tight_layout()

# Save as SVG
output_filename = 'czech_signals_chart.svg'
plt.savefig(output_filename, format='svg')
print(f"Chart saved to {output_filename}")
