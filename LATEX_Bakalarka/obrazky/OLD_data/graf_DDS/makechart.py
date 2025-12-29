import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.font_manager as fm
from matplotlib.lines import Line2D
from pathlib import Path

# --- 1. Font Handling Section (Linux Fix) ---
target_font_str = "IoskeleyMono_Regular"
font_loaded = False

search_dirs = [
    Path.home() / ".local/share/fonts",
    Path.home() / ".fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts")
]

print(f"Searching for font files matching '{target_font_str}'...")

found_file = None
for directory in search_dirs:
    if not directory.exists():
        continue

    for file_path in directory.rglob("*.[ot]tf"):
        if target_font_str.lower() in file_path.name.lower():
            if "bold" not in file_path.name.lower() and "italic" not in file_path.name.lower():
                found_file = file_path
                break
    if found_file:
        break

if found_file:
    try:
        fm.fontManager.addfont(str(found_file))
        prop = fm.FontProperties(fname=str(found_file))
        internal_name = prop.get_name()
        plt.rcParams['font.family'] = internal_name
        print(f"SUCCESS: Loaded '{internal_name}' from {found_file}")
        font_loaded = True
    except Exception as e:
        print(f"Error loading font file: {e}")

if not font_loaded:
    print(f"WARNING: Could not find a file matching '{target_font_str}'. Using Monospace fallback.")
    plt.rcParams['font.family'] = 'monospace'


# --- 2. Data Generation ---

N_LUT = 17
addresses = np.arange(N_LUT)
voltage_values = np.sin(2 * np.pi * addresses / (N_LUT - 1))
n_periods_to_show = 2

voltage_one_period = voltage_values[:-1]
plot_voltages = np.tile(voltage_one_period, n_periods_to_show)
plot_voltages = np.append(plot_voltages, voltage_values[-1])
plot_addresses = np.arange(len(plot_voltages))

t_smooth = np.linspace(0, plot_addresses[-1], 1000)
sine_smooth = np.sin(2 * np.pi * t_smooth / (N_LUT - 1))


# --- 3. Plotting ---

fig = plt.figure(figsize=(14, 10))

# --- PART A: Memory Address Block (Top Subplot) ---
ax_mem = plt.subplot2grid((2, 1), (0, 0))
ax_mem.set_title('Vyhledávací tabulka', fontsize=18) # Increased title size slightly

# Změna limitu osy X, aby se vešly popisky vlevo (z -1 na -2.5)
ax_mem.set_xlim(-2.5, N_LUT)
ax_mem.set_ylim(-1.0, 1.5)
ax_mem.axis('off')

box_width = 0.8
# Zmenšeno z 0.8 na 0.5 pro menší výšku a menší mezeru mezi řádky
box_height = 0.5
y_base = 0.2

# Definice Y souřadnic pro řádky textu
y_row_addr = y_base + box_height * 0.75
y_row_val = y_base + box_height * 0.25

# -- ZMĚNA: Přidání popisků vlevo před boxy --
# Umístíme je na souřadnici X = -0.6 (těsně před první box, který začíná na -0.4)
# Increased fontsize to 14
ax_mem.text(-0.6, y_row_addr, "Adresa:",
            ha='right', va='center', fontsize=14, fontweight='bold')
ax_mem.text(-0.6, y_row_val, "Hodnota:",
            ha='right', va='center', fontsize=14, fontweight='bold')

# Draw Boxes and Text
for i in range(N_LUT):
    rect = patches.Rectangle((i - box_width/2, y_base), box_width, box_height,
                             edgecolor='black', facecolor='#e6e6e6', alpha=1.0)
    ax_mem.add_patch(rect)

    # -- ZMĚNA: Uvnitř boxů jen čísla --
    # Horní řádek: Index (Adresa) - Increased fontsize to 12
    ax_mem.text(i, y_row_addr, f"{i}",
                ha='center', va='center', fontsize=12, fontweight='bold')

    # Spodní řádek: Hodnota napětí - Increased fontsize to 11
    ax_mem.text(i, y_row_val, f"{voltage_values[i]:.2f}",
                ha='center', va='center', fontsize=11, color='blue')

# Draw Cycling Arrows (Sequential)
for i in range(N_LUT - 1):
    style = patches.ConnectionPatch(
        xyA=(i, y_base), xyB=(i+1, y_base),
        coordsA="data", coordsB="data",
        axesA=ax_mem, axesB=ax_mem,
        arrowstyle="-|>", connectionstyle="arc3,rad=0.5",
        color='black', lw=0.7
    )
    ax_mem.add_artist(style)

# Draw Return Arrow (Loop back)
return_arrow = patches.ConnectionPatch(
    xyA=(N_LUT-1, y_base), xyB=(0, y_base),
    coordsA="data", coordsB="data",
    axesA=ax_mem, axesB=ax_mem,
    arrowstyle="-|>", connectionstyle="arc3,rad=0.3",
    color='black', lw=0.7, linestyle='--'
)
ax_mem.add_artist(return_arrow)


# --- PART B: DAC Output (Bottom Subplot) ---
ax_plot = plt.subplot2grid((2, 1), (1, 0))
ax_plot.set_title('Výstup DAC:', fontsize=18) # Increased title size
# Increased axis labels fontsize to 14
ax_plot.set_xlabel('Čas [vzorky]', fontsize=14)
ax_plot.set_ylabel('Napětí [V]', fontsize=14)
ax_plot.grid(True, linestyle=':', alpha=0.6)
ax_plot.axhline(0, color='black', linewidth=1)

# Set tick label size
ax_plot.tick_params(axis='both', which='major', labelsize=12)

# 1. Plot Unfiltered Output (Zero-Order Hold)
ax_plot.step(plot_addresses, plot_voltages, 'b:', where='post', label='Nefiltrovaný výstup DAC', linewidth=1, alpha=1)

# 2. Plot Ideal Filtered Output
ax_plot.plot(t_smooth, sine_smooth, 'r--', label='Filtrovaný výstup', linewidth=1, alpha=1)

# 3. Draw the arrows from the centerline (Samples)
for i, (addr, volt) in enumerate(zip(plot_addresses, plot_voltages)):
    color = 'black'
    arrow_style = dict(facecolor=color, edgecolor=color, width=1, headwidth=5)

    if abs(volt) > 0.01:
        ax_plot.annotate('', xy=(addr, volt), xytext=(addr, 0), arrowprops=arrow_style, zorder=5)
    else:
        ax_plot.plot(addr, 0, 'ko', markersize=6, zorder=5)

#ax_plot.set_xlim(-0.5, plot_addresses[-1] + 0.5)
ax_plot.set_xlim(0, 32)
ax_plot.set_ylim(-1.3, 1.3)

# Add custom arrow entry to legend
arrow_proxy = Line2D([0], [0], color='black', linewidth=1.5, marker='^', markersize=6, label='Hodnota vzorku')
handles, labels = ax_plot.get_legend_handles_labels()
handles.append(arrow_proxy)
labels.append("Hodnota vzorku")

ax_plot.legend(handles=handles, labels=labels, loc='upper right', fontsize=12)

plt.tight_layout()
plt.savefig('dds_final.svg')
plt.show()
