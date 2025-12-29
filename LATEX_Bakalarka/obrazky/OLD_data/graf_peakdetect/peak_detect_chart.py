import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np
import os

def generate_signal_chart(font_name='sans-serif'):
    # --- Font Handling Section ---
    # Try to find the font specifically, handling case sensitivity and system paths
    try:
        # 1. Check if Matplotlib already knows this font
        available_fonts = {f.name.lower() for f in fm.fontManager.ttflist}
        if font_name.lower() in available_fonts:
            plt.rcParams['font.family'] = font_name
            print(f"Using cached font: {font_name}")
        else:
            # 2. If not in cache, search system files directly (fixes 'installed but not found' issues)
            print(f"Font '{font_name}' not in cache, searching system files...")
            system_fonts = fm.findSystemFonts(fontpaths=None, fontext='ttf')

            # Normalize search string (remove spaces, lowercase)
            target_clean = font_name.lower().replace(" ", "")

            # Collect all candidates first
            candidates = []
            for path in system_fonts:
                filename = os.path.basename(path).lower()
                # Check if filename contains the font name (e.g. "ioskeleymono.ttf")
                if target_clean in filename.replace("-", "").replace("_", ""):
                    candidates.append(path)

            # Select the best candidate (prioritizing Normal/Regular over Italic/Bold)
            found_path = None
            if candidates:
                # First pass: Look for a "clean" version (no bold/italic in filename)
                for path in candidates:
                    fname = os.path.basename(path).lower()
                    if "italic" not in fname and "bold" not in fname and "oblique" not in fname:
                        found_path = path
                        break

                # Second pass: If no clean version, just take the first one found
                if not found_path:
                    found_path = candidates[0]

            if found_path:
                # Add the font specifically
                fm.fontManager.addfont(found_path)
                prop = fm.FontProperties(fname=found_path)
                plt.rcParams['font.family'] = prop.get_name()
                print(f"Found and loaded font from: {found_path}")
            else:
                print(f"Warning: Font '{font_name}' could not be found. Using default.")
                plt.rcParams['font.family'] = 'sans-serif'

    except Exception as e:
        print(f"Font warning: {e}. Falling back to default.")
        plt.rcParams['font.family'] = 'sans-serif'

    # --- Signal Generation Section ---
    # Parameters
    f_signal = 100e3        # 100 kHz
    f_noise_bw = 2e6        # 2 MHz (approximate bandwidth of the noise)
    v_pp_signal = 5.0       # 5V peak-to-peak
    v_pp_noise = 0.5        # 0.5V peak-to-peak

    # Calculate periods to determine duration
    period_signal = 1 / f_signal
    total_duration = 2 * period_signal  # Display two periods

    # Generate time data
    t = np.linspace(0, total_duration, 10000)

    # Generate Signal (Amplitude is Vpp / 2)
    signal_wave = (v_pp_signal / 2) * np.sin(2 * np.pi * f_signal * t)

    # Generate "Real" Noise
    num_noise_points = int(total_duration * f_noise_bw * 3)
    t_noise_grid = np.linspace(0, total_duration, num_noise_points)
    noise_samples = np.random.uniform(-v_pp_noise/2, v_pp_noise/2, num_noise_points)
    noise_wave = np.interp(t, t_noise_grid, noise_samples)

    # Combine
    voltage = signal_wave + noise_wave

    # Generate Peak Detector Signal
    peak_detector = np.maximum.accumulate(voltage)

    # --- Plotting Section ---
    # figsize=(6, 4) ensures the chart has a 3:2 aspect ratio
    fig, ax = plt.subplots(figsize=(6, 4))

    # Convert time to microseconds
    t_us = t * 1e6

    # Colors for academic paper (High contrast, colorblind safe)
    # Dark Blue for signal
    color_signal = '#0055AA'
    # Vermilion/Dark Red for detector
    color_peak = '#CC3311'

    # Plot original signal
    # r'$...$' denotes a raw string for LaTeX-style math rendering
    ax.plot(t_us, voltage, color=color_signal, linewidth=0.7, label=r'$U_{in}$')

    # Plot peak detector
    ax.plot(t_us, peak_detector, color=color_peak, linewidth=1.5, linestyle='--', label=r'$U_{šp}$')

    # Axis configuration
    ax.set_ylabel("Napětí [U]", fontsize=12)
    ax.set_xlabel("Čas [μs]", fontsize=12)
    ax.set_ylim(-3, 3)
    ax.set_xlim(0, 20)
    ax.set_title("")
    ax.grid(True, linestyle='--', alpha=0.7)

    # Add Legend
    ax.legend(loc='upper right', frameon=True, fontsize=11)

    plt.tight_layout()

    output_filename = "signal_chart.svg"
    plt.savefig(output_filename, format='svg')
    print(f"Chart saved successfully as '{output_filename}'")

if __name__ == "__main__":
    # Tried to set the font you requested
    generate_signal_chart(font_name='Ioskeley Mono')
