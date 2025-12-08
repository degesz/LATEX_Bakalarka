import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np
import os
from pathlib import Path  # Added for robust Linux path handling
from scipy.signal import butter, filtfilt # Added for signal processing

# --- 1. Font Handling Section (Linux Fix) ---
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


# --- 2. Data Setup ---
freq_khz = 100
freq_mhz = freq_khz / 1000.0   # 0.1 MHz
amplitude = 2.5                # 5Vpp = 2.5V Amplitude
noise_std_dev = 0.35           # Increased raw noise level to compensate for filtering loss

# Time vector (centered around 5us)
t = np.linspace(2.5, 7.5, 1000)

# Calculate Sampling Frequency (Fs) for the filter
# Time is in microseconds, so Fs will be in MHz
dt = t[1] - t[0]
fs = 1 / dt  # ~200 MHz

# --- Noise Filter Design ---
noise_cutoff_mhz = 10.0  # Cutoff frequency for the noise (Bandwidth limit)
nyquist = 0.5 * fs
b, a = butter(N=4, Wn=noise_cutoff_mhz / nyquist, btype='low')


# Generate 12 signals with normally distributed phase shifts
np.random.seed(42)
num_signals = 5
mean_crossing = 5.0
std_crossing = 0.2
crossings = np.random.normal(mean_crossing, std_crossing, num_signals)


# --- 3. Create Plot ---
fig, ax = plt.subplots(figsize=(8, 6))

# Enumerate crossings to get index for color selection
for i, cross in enumerate(crossings):
    # 1. Calculate Pure Signal: A * sin(2*pi*f * (t - phase))
    pure_signal = amplitude * np.sin(2 * np.pi * freq_mhz * (t - cross))

    # 2. Generate White Gaussian Noise
    white_noise = np.random.normal(0, noise_std_dev, t.shape)

    # 3. Limit Bandwidth: Filter the noise
    # filtfilt applies the filter forward and backward to avoid phase shift
    colored_noise = filtfilt(b, a, white_noise)

    # 4. Superimpose Noise
    voltage = pure_signal + colored_noise

    # --- 5. Find Actual Zero Crossing (New Logic) ---
    # Find indices where sign changes from negative to positive
    # (voltage[i] < 0 and voltage[i+1] >= 0)
    sign_change_indices = np.where((voltage[:-1] < 0) & (voltage[1:] >= 0))[0]

    actual_crossing = cross # Fallback to theoretical if no crossing found (unlikely)

    if len(sign_change_indices) > 0:
        # Calculate precise crossing times using linear interpolation
        candidates = []
        for idx in sign_change_indices:
            v1 = voltage[idx]
            v2 = voltage[idx+1]
            t1 = t[idx]
            t2 = t[idx+1]

            # Linear interpolation for t where v=0
            # t_zero = t1 + (0 - v1) / slope
            # slope = (v2 - v1) / (t2 - t1)
            if v2 != v1:
                t_zero = t1 - v1 * (t2 - t1) / (v2 - v1)
                candidates.append(t_zero)
            else:
                candidates.append(t1) # Should not happen with float noise

        # Select the crossing closest to the theoretical 'cross' to avoid
        # picking up spurious noise crossings far from the main edge
        candidates = np.array(candidates)
        idx_closest = np.argmin(np.abs(candidates - cross))
        actual_crossing = candidates[idx_closest]

    # Select a unique color for this signal from the 'tab20' colormap
    color = plt.cm.tab20(i)

    # Plot the Noisy Signal with the unique color
    ax.plot(t, voltage, color=color, linewidth=0.7, zorder=2, alpha=0.8)

    # Plot Zero Crossing Marker (Using the calculated actual_crossing)
    ax.vlines(x=actual_crossing, ymin=-0.2, ymax=0.2,
              colors='#00D138', linewidth=0.7, zorder=3)


# --- 4. Styling ---
ax.grid(True, linestyle='--', linewidth=0.5, color='gray', alpha=0.5, zorder=0)
ax.set_axisbelow(True)

for spine in ax.spines.values():
    spine.set_edgecolor('black')
    spine.set_linewidth(1)

ax.set_xlim(2.5, 7.5)
ax.set_ylim(-3.5, 3.5) # Expanded slightly to account for noise peaks
ax.axhline(0, color='black', linewidth=0.8, zorder=1)

# Labels
ax.set_xlabel('Čas [μs]', fontsize=12)
ax.set_ylabel('Napětí [V]', fontsize=12)
#ax.set_title(f'Signal Analysis: {target_font_str}', fontsize=14)


# --- 5. Save ---
output_filename = 'noisy_signal_chart.svg'
plt.tight_layout()
plt.savefig(output_filename, format='svg')

print(f"Chart saved successfully as {output_filename}")
plt.show()
