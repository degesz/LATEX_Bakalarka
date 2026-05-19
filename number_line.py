#!/usr/bin/env python3
"""SVG number line with color-coded markers and tolerance indicators."""

import argparse
import math

SI_PREFIXES = [
    (1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"),
    (1, ""),
    (1e-3, "m"), (1e-6, "μ"), (1e-9, "n"), (1e-12, "p"),
]

UNIT_SYMBOLS = {"resistance": "Ω", "inductance": "H", "capacitance": "F"}
MARKER_COLORS = {"nominal": "#2166ac", "reference": "#1b7837", "measured": "#b2182b"}


def fmt(val, unit_type):
    unit = UNIT_SYMBOLS[unit_type]
    av = abs(val)
    for factor, prefix in SI_PREFIXES:
        if av >= factor:
            d = val / factor
            if abs(d) >= 100:
                d = round(d)
            elif abs(d) >= 10:
                d = round(d, 1)
            elif abs(d) >= 1:
                d = round(d, 2)
            else:
                d = round(d, 3)
            return f"{d} {prefix}{unit}"
    return f"{val} {unit}"


def _nice(x, up=True):
    e = math.floor(math.log10(x))
    f = x / 10 ** e
    if up:
        n = 1 if f <= 1 else 2 if f <= 2 else 5 if f <= 5 else 10
    else:
        n = 1 if f < 1.5 else 2 if f < 3.5 else 5 if f < 7.5 else 10
    return n * 10 ** e


def ticks(vmin, vmax, n_max=10):
    r = vmax - vmin
    if r == 0:
        r = abs(vmin) * 0.1 if vmin != 0 else 1
    step = _nice(r / n_max, up=True)
    start = math.floor(vmin / step) * step
    end = math.ceil(vmax / step) * step
    out = []
    v = start
    while v <= end + step * 0.01:
        if v >= vmin - r * 0.01 and v <= vmax + r * 0.01:
            out.append(v)
        v += step
    if len(out) < 3:
        step = _nice((vmax - vmin) / 6, up=True)
        start = math.floor(vmin / step) * step
        end = math.ceil(vmax / step) * step
        out = []
        v = start
        while v <= end + step * 0.01:
            out.append(v)
            v += step
    return out, step


def gen_svg(nominal, reference, measured, unit_type,
            part_tolerance=None, ref_tolerance=None, output=None):
    if part_tolerance is not None:
        part_lo = nominal * (1 - part_tolerance / 100)
        part_hi = nominal * (1 + part_tolerance / 100)
    if ref_tolerance is not None:
        ref_lo = reference * (1 - ref_tolerance / 100)
        ref_hi = reference * (1 + ref_tolerance / 100)

    vals = [nominal, reference, measured]
    if part_tolerance is not None:
        vals.extend([part_lo, part_hi])
    if ref_tolerance is not None:
        vals.extend([ref_lo, ref_hi])

    lo, hi = min(vals), max(vals)
    r = hi - lo
    if r == 0:
        r = abs(lo) * 0.1 if lo != 0 else 1
    pad = r * 0.2
    pmin, pmax = lo - pad, hi + pad

    tks, step = ticks(pmin, pmax)

    sub_step = step / 5
    sub_tks = []
    v = tks[0]
    while v <= tks[-1] + step * 0.01:
        for i in range(1, 5):
            sv = v + i * sub_step
            if sv > tks[-1] - 0.001:
                break
            sub_tks.append(sv)
        v += step

    S = 10
    W_mm = 140
    W = W_mm * S
    show_tol = part_tolerance is not None or ref_tolerance is not None
    H_mm = 16 if show_tol else 14
    H = H_mm * S

    LY = 60
    LX_L = 14
    LX_R = W - 14
    PW = LX_R - LX_L

    ARROW_HT = 35
    arr_base_y = LY - ARROW_HT

    TOL_ZONE_TOP = 14
    TOL_ZONE_BOT = arr_base_y

    def xp(v):
        return LX_L + (v - pmin) / (pmax - pmin) * PW

    L = []
    L.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W_mm}mm" height="{H_mm}mm" viewBox="0 0 {W} {H}">')
    L.append('  <style>')
    L.append('    text { font-family: "Ioskeley Mono", monospace; font-size: 32; }')
    L.append('  </style>')

    if ref_tolerance is not None:
        x1 = xp(ref_lo)
        x2 = xp(ref_hi)
        w = x2 - x1
        if w > 0:
            L.append(f'  <rect x="{x1}" y="{TOL_ZONE_TOP}" width="{w}" height="{TOL_ZONE_BOT - TOL_ZONE_TOP}" '
                     f'fill="#1b7837" fill-opacity="0.15" stroke="none"/>')

    if part_tolerance is not None:
        x1 = xp(part_lo)
        x2 = xp(part_hi)
        w = x2 - x1
        if w > 0:
            mid_y = (TOL_ZONE_TOP + TOL_ZONE_BOT) / 2
            L.append(f'  <line x1="{x1}" y1="{mid_y}" x2="{x2}" y2="{mid_y}" '
                     f'stroke="#2166ac" stroke-width="4" stroke-linecap="round"/>')

    L.append(f'  <line x1="{LX_L}" y1="{LY}" x2="{LX_R}" y2="{LY}" stroke="#000" stroke-width="2.5"/>')

    ch_horiz = 14
    ch_vert = 7
    L.append(f'  <line x1="0" y1="{LY}" x2="{LX_L}" y2="{LY - ch_vert}" stroke="#000" stroke-width="2.5"/>')
    L.append(f'  <line x1="0" y1="{LY}" x2="{LX_L}" y2="{LY + ch_vert}" stroke="#000" stroke-width="2.5"/>')
    L.append(f'  <line x1="{W}" y1="{LY}" x2="{LX_R}" y2="{LY - ch_vert}" stroke="#000" stroke-width="2.5"/>')
    L.append(f'  <line x1="{W}" y1="{LY}" x2="{LX_R}" y2="{LY + ch_vert}" stroke="#000" stroke-width="2.5"/>')

    for sv in sub_tks:
        x = xp(sv)
        L.append(f'  <line x1="{x}" y1="{LY}" x2="{x}" y2="{LY + 15}" stroke="#999" stroke-width="2"/>')

    for tv in tks:
        x = xp(tv)
        L.append(f'  <line x1="{x}" y1="{LY}" x2="{x}" y2="{LY + 35}" stroke="#000" stroke-width="2.5"/>')
        L.append(f'  <text x="{x}" y="{LY + 56}" text-anchor="middle" fill="#000">{fmt(tv, unit_type)}</text>')

    items = [("nominal", nominal), ("reference", reference), ("measured", measured)]
    for key, val in items:
        x = xp(val)
        c = MARKER_COLORS[key]
        L.append(f'  <line x1="{x}" y1="{arr_base_y}" x2="{x}" y2="{LY}" stroke="{c}" '
                 f'stroke-dasharray="3,4" stroke-width="2.5"/>')

    hw = 4
    for key, val in items:
        x = xp(val)
        c = MARKER_COLORS[key]
        pts = f"{x},{LY} {x - hw},{arr_base_y} {x + hw},{arr_base_y}"
        L.append(f'  <polygon points="{pts}" fill="{c}" stroke="none"/>')

    L.append('</svg>')
    svg = '\n'.join(L)

    if output:
        with open(output, 'w') as f:
            f.write(svg)
        print(f"Saved → {output}")
    else:
        print(svg)


def main():
    ap = argparse.ArgumentParser(description="Generate SVG number line with measurement markers")
    ap.add_argument("nominal", type=float, help="Nominal component value")
    ap.add_argument("reference", type=float, help="Reference meter measurement")
    ap.add_argument("measured", type=float, help="Your meter measurement")
    ap.add_argument("-u", "--unit", choices=["resistance", "inductance", "capacitance"],
                    default="resistance", help="Unit type (default: resistance)")
    ap.add_argument("-o", "--output", help="Output SVG file")
    ap.add_argument("--part-tolerance", type=float, default=None,
                    help="Part tolerance in percent (e.g. 5 for ±5%)")
    ap.add_argument("--ref-tolerance", type=float, default=None,
                    help="Reference meter tolerance in percent (e.g. 1 for ±1%)")
    args = ap.parse_args()
    gen_svg(args.nominal, args.reference, args.measured, args.unit,
            args.part_tolerance, args.ref_tolerance, args.output)


if __name__ == "__main__":
    main()
