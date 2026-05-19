import math
import argparse

class MeasurementLineSVG:
    def __init__(self, filename):
        self.filename = filename
        self.vw = 1300
        self.vh = 80

        self.center_y = 54
        self.top_y = 32
        self.bottom_y = 76

        # Calculate exactly a 55-degree angle for the pointed ends
        # tan(theta) = opposite / adjacent => adjacent = opposite / tan(theta)
        dy = self.center_y - self.top_y
        angle_rad = math.radians(55)
        self.margin_x = dy / math.tan(angle_rad)

        self.plot_w = self.vw - (2 * self.margin_x)

        # Colors
        self.color_nom = "#448aff"   # Blue (brighter)
        self.color_meas = "#ff1744"  # Red (brighter)
        self.color_ref = "#00c853"   # Green (brighter)
        self.color_line = "#000000"  # Black
        self.color_minor = "#888888" # Grey

    def _val_to_x(self, val, min_val, max_val):
        val = max(min(val, max_val), min_val)
        ratio = (val - min_val) / (max_val - min_val)
        return self.margin_x + (ratio * self.plot_w)

    def generate(self, min_val, max_val, major_step, minor_step,
                 nominal, measured, reference, unit="kΩ", label=""):

        svg_elements = []

        # 1. Setup SVG header (Fixed at 130mm width)
        svg_elements.append(
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="130mm" viewBox="0 0 {self.vw} {self.vh}" '
            f'style="font-family: \'Ioskeley Mono\', monospace; background-color: white;">'
        )

        # Component label at top left (with optional subscript after _)
        if label:
            parts = label.split("_", 1)
            main = parts[0]
            sub = parts[1] if len(parts) > 1 else ""
            if sub:
                txt = (f'<text x="12" y="26" font-size="28" fill="#000000" font-weight="regular">'
                       f'{main}<tspan baseline-shift="sub" font-size="0.65">{sub}</tspan></text>')
            else:
                 txt = (f'<text x="12" y="18" font-size="40" fill="#000000" font-weight="regular">'
                       f'{main}</text>')
            svg_elements.append(txt)

        # 2. Draw Tolerance Bands (non-overlapping)
        bands = [
            (0,  1,  "#1d7634"),
            (1,  5,  "#e6b800"),
            (5,  10, "#e67e22"),
            (10, 20, "#b02626"),
        ]
        for inner, outer, color in bands:
            x_left_outer  = self._val_to_x(reference * (1 - outer/100.0), min_val, max_val)
            x_left_inner  = self._val_to_x(reference * (1 - inner/100.0), min_val, max_val)
            x_right_inner = self._val_to_x(reference * (1 + inner/100.0), min_val, max_val)
            x_right_outer = self._val_to_x(reference * (1 + outer/100.0), min_val, max_val)
            for x1, x2 in [(x_left_outer, x_left_inner), (x_right_inner, x_right_outer)]:
                w = x2 - x1
                if w > 0:
                    svg_elements.append(
                        f'<rect x="{x1}" y="{self.top_y + 1}" '
                        f'width="{w}" height="{self.bottom_y - self.top_y - 2}" '
                        f'fill="{color}" fill-opacity="0.35" />'
                    )

        # 3. Draw Minor Ticks
        curr_val = min_val
        while curr_val <= max_val + (minor_step * 0.1):
            x = self._val_to_x(curr_val, min_val, max_val)
            svg_elements.append(
                f'<line x1="{x}" y1="{self.center_y - 5}" x2="{x}" y2="{self.center_y + 5}" '
                f'stroke="{self.color_minor}" stroke-width="2" />'
            )
            curr_val += minor_step

        # 4. Draw Major Ticks and Labels
        curr_val = min_val
        while curr_val <= max_val + (major_step * 0.1):
            x = self._val_to_x(curr_val, min_val, max_val)

            svg_elements.append(
                f'<line x1="{x}" y1="{self.top_y}" x2="{x}" y2="{self.bottom_y}" '
                f'stroke="{self.color_line}" stroke-width="3" />'
            )

            label_text = f"{curr_val:g} {unit}"
            text_anchor = "middle"
            if abs(curr_val - min_val) < 1e-6:
                text_anchor = "start"
            elif abs(curr_val - max_val) < 1e-6:
                text_anchor = "end"

            svg_elements.append(
                f'<text x="{x}" y="{self.bottom_y + 38}" text-anchor="{text_anchor}" '
                f'font-size="38" fill="black">{label_text}</text>'
            )
            curr_val += major_step

        # 5. Draw the Main Box Outline & Pointed Ends
        left_tip_x = 0
        right_tip_x = self.vw

        outline_path = (
            f"M {self.margin_x} {self.top_y} "
            f"L {self.vw - self.margin_x} {self.top_y} "
            f"L {right_tip_x} {self.center_y} "
            f"L {self.vw - self.margin_x} {self.bottom_y} "
            f"L {self.margin_x} {self.bottom_y} "
            f"L {left_tip_x} {self.center_y} Z"
        )
        svg_elements.append(
            f'<path d="{outline_path}" fill="none" stroke="{self.color_line}" stroke-width="3" />'
        )

        svg_elements.append(
            f'<line x1="{left_tip_x}" y1="{self.center_y}" x2="{right_tip_x}" y2="{self.center_y}" '
            f'stroke="{self.color_line}" stroke-width="3" />'
        )

# 6. Draw Markers (symmetric, extend 1mm/10px beyond box)
        ext = 10  # 1mm = 10 viewBox units
        def draw_marker(val, color):
            x = self._val_to_x(val, min_val, max_val)
            top = f"{x-8},{self.top_y - ext} {x+8},{self.top_y - ext} {x},{self.center_y}"
            bot = f"{x-8},{self.bottom_y + ext} {x+8},{self.bottom_y + ext} {x},{self.center_y}"

            # Added stroke="white" and stroke-width="1.5" for the outline
            svg_elements.append(f'<polygon points="{top}" fill="{color}" stroke="white" stroke-width="1.5" />')
            svg_elements.append(f'<polygon points="{bot}" fill="{color}" stroke="white" stroke-width="1.5" />')
        draw_marker(reference, self.color_ref)
        draw_marker(measured, self.color_meas)
        draw_marker(nominal, self.color_nom)

        # 7. Close SVG and write to file
        svg_elements.append('</svg>')

        with open(self.filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join(svg_elements))
        print(f"Successfully generated: {self.filename}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate measurement line SVG.")
    parser.add_argument("-o", "--output", default="output.svg", help="Output filename (e.g., plot.svg)")
    parser.add_argument("--min", type=float, required=True, help="Minimum axis value")
    parser.add_argument("--max", type=float, required=True, help="Maximum axis value")
    parser.add_argument("--major", type=float, required=True, help="Major tick step size")
    parser.add_argument("--minor", type=float, required=True, help="Minor tick step size")
    parser.add_argument("--nom", type=float, required=True, help="Nominal component value (blue marker)")
    parser.add_argument("--meas", type=float, required=True, help="Measured value (red marker)")
    parser.add_argument("--ref", type=float, required=True, help="Reference value (green marker)")
    parser.add_argument("-u", "--unit", type=str, required=True, help="Unit string (e.g., 'kΩ', 'µF', 'mH')")

    args = parser.parse_args()

    generator = MeasurementLineSVG(args.output)
    generator.generate(
        min_val=args.min,
        max_val=args.max,
        major_step=args.major,
        minor_step=args.minor,
        nominal=args.nom,
        measured=args.meas,
        reference=args.ref,
        unit=args.unit
    )
