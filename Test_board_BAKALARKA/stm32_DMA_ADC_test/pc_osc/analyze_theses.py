#!/usr/bin/env python3
"""
Scrape VUT thesis database for bachelor theses with A/B grades and analyze them.
Usage: python3 analyze_theses.py [--resume] [--year 2024]
"""

import requests
import re
import json
import os
import sys
import time
import argparse
from collections import Counter, defaultdict
from urllib.parse import urljoin

BASE_URL = "https://www.vut.cz"
SEARCH_URL = f"{BASE_URL}/studenti/zav-prace"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "thesis_analysis")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

session = requests.Session()
session.headers.update(HEADERS)

def fetch(url, params=None, timeout=30):
    try:
        resp = session.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"  Error: {e}")
        return None

def get_search_params(fid=5, rok=2024, typ=1, jazyk="cs", str_page=1):
    return {
        "action_name": "zform", "formID": "search_zav_praci",
        "action_type": "object", "page_id": "5614",
        "block_id": "141", "object_id": "13533",
        "fid": str(fid), "rok": str(rok), "typ": str(typ),
        "jazyk": jazyk, "submitBtn": "1", "str": str(str_page),
    }

def extract_total_info(html):
    """Extract total count and page count from search results."""
    total = 0
    m = re.search(r'z (\d+) nalezených', html)
    if m:
        total = int(m.group(1))
    max_page = 1
    pages = set(int(x) for x in re.findall(r'\bstr=(\d+)\b', html))
    if pages:
        max_page = max(pages)
    return total, max_page

def extract_thesis_ids(html):
    """Extract thesis IDs from search results."""
    if not html:
        return []
    return sorted(set(int(x) for x in re.findall(r'/studenti/zav-prace/detail/(\d+)', html)))

def extract_thesis_info(html, thesis_id):
    """Extract grade, PDF links, title from detail page."""
    if not html:
        return None
    info = {"id": thesis_id}
    
    # Title
    m = re.search(r'<h1 class="b-detail__title">(.*?)</h1>', html, re.DOTALL)
    if m: info["title"] = m.group(1).strip()
    
    # Author
    m = re.search(r'Autor práce:\s*<strong>(.*?)</strong>', html)
    if m: info["author"] = m.group(1).strip()
    
    # Academic year
    m = re.search(r'Ak\. rok:\s*<strong>(.*?)</strong>', html)
    if m: info["ak_rok"] = m.group(1).strip()
    
    # Supervisor
    m = re.search(r'Vedoucí:\s*<a[^>]*>(.*?)</a>', html)
    if m: info["vedouci"] = m.group(1).strip()
    
    # Final grade
    # Find "Klasifikace" row
    grade_patterns = [
        r'Klasifikace</p></div>\s*<div class="grid__cell[^"]*">\s*<div class="b-detail__content">\s*<p>\s*([A-E])\s*</p>',
        r'Klasifikace</p></div>.*?<div class="b-detail__content">\s*<p>\s*([A-E])\s*</p>',
    ]
    grade = None
    for pat in grade_patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            grade = m.group(1)
            break
    info["grade"] = grade if grade else "N/A"
    
    # Proposed grades
    m = re.search(r'Známka navržená vedoucím:\s*<b>(.*?)</b>', html)
    if m: info["grade_proposed_supervisor"] = m.group(1).strip()
    
    m = re.search(r'Známka navržená oponentem:\s*<b>(.*?)</b>', html)
    if m: info["grade_proposed_opponent"] = m.group(1).strip()
    
    # PDF files
    pdfs = []
    for m in re.finditer(r'<a href="(/www_base/zav_prace_soubor_verejne\.php\?file_id=\d+)" class="link-file">.*?<strong class="link-file__name">(.*?)</strong>\s*<span class="link-file__size">(.*?)</span>', html, re.DOTALL):
        pdfs.append({
            "url": BASE_URL + m.group(1),
            "name": m.group(2).strip(),
            "size": m.group(3).strip(),
        })
    info["pdfs"] = pdfs
    
    # Result of defense
    m = re.search(r'Výsledek obhajoby.*?<p>(.*?)</p>', html, re.DOTALL)
    if m: info["obhajoba"] = m.group(1).strip()
    
    return info

def download_pdf(pdf_url, filepath):
    if os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
        return True
    try:
        resp = session.get(pdf_url, timeout=120)
        resp.raise_for_status()
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'wb') as f:
            f.write(resp.content)
        return True
    except Exception as e:
        print(f"  Download error: {e}")
        return False

def extract_text_from_pdf(filepath):
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(filepath)
        return text
    except Exception as e:
        print(f"  Text extraction error: {e}")
        return ""

def count_normostrany(text):
    return len(text) / 1800

def find_sections(text):
    """
    Find introduction, conclusion, and appendix boundaries.
    
    Strategy:
    1. Find TOC end - look for first numbered chapter (e.g. "1 " or "1.") 
       after the "Úvod" entry in TOC
    2. Actual Úvod starts after TOC - look for standalone "Úvod" line after TOC
    3. Závěr - look for standalone "Závěr" line near the end of document
    4. Literature/Appendix mark the end of main text
    """
    lines = text.split('\n')
    
    # First, find where TOC ends (look for first chapter number heading)
    toc_end = -1
    chapter_1_start = -1
    for i, line in enumerate(lines):
        ls = line.strip()
        if re.match(r'^1\s+[A-ZÁČĎÉĚÍŇÓŘŠŤÚÝŽ]', ls):
            chapter_1_start = i
            toc_end = i - 1  # TOC ends before chapter 1
            break
    
    # Actual content starts after TOC
    content_start = max(toc_end, 0) if toc_end != -1 else 0
    
    # Find Úvod (skip TOC)
    intro_start = -1
    intro_end = -1
    for i in range(max(content_start, 0), len(lines)):
        ls = lines[i].strip()
        # Match standalone "Úvod" (not in TOC)
        if re.match(r'^[ÚUu]vod\s*$', ls) and intro_start == -1:
            # Make sure it's not in TOC (next line should have actual text, not page number)
            if i > content_start:
                intro_start = i
                break
            # Also check if this is followed by text, not numbers
            if i + 1 < len(lines) and not re.match(r'^\d+$', lines[i+1].strip()):
                intro_start = i
                break
    
    # If intro not found after TOC, try before TOC too
    if intro_start == -1:
        for i, line in enumerate(lines):
            ls = line.strip()
            if re.match(r'^[ÚUu]vod\s*$', ls):
                # Check it's not a TOC entry (TOC entries have page numbers after)
                if i + 1 >= len(lines) or not re.match(r'^\d+$', lines[i+1].strip()):
                    intro_start = i
                    break
    
    # Find end of introduction (next numbered chapter, or conclusion)
    if intro_start != -1:
        for i in range(intro_start + 2, len(lines)):
            ls = lines[i].strip()
            if re.match(r'^[23]\s+[A-ZÁČĎÉĚÍŇÓŘŠŤÚÝŽ]', ls):
                intro_end = i
                break
            # If next chapter with any number
            if re.match(r'^\d+\.\d+\s', ls):
                continue
            if re.match(r'^\d+\s+[A-ZÁČĎÉĚÍŇÓŘŠŤÚÝŽ]', ls) and i != chapter_1_start:
                chapter_num = re.match(r'^(\d+)', ls).group(1)
                if int(chapter_num) > 2:  # Skip chapter 2, look for 3+
                    intro_end = i
                    break
    
    # Find Závěr (skip TOC, look near end of document)
    conclusion_start = -1
    conclusion_end = -1
    
    # Search from the end for "Závěr"
    for i in range(len(lines) - 1, content_start, -1):
        ls = lines[i].strip()
        if re.match(r'^[ZzZŽ]ávěr\s*$', ls):
            # Skip if it looks like TOC (surrounded by page numbers)
            if i + 1 < len(lines) and re.match(r'^\d+$', lines[i+1].strip()):
                continue
            if i > 0 and re.match(r'^\d+$', lines[i-1].strip()):
                continue
            conclusion_start = i
            break
    
    # If conclusion not found from end, try forward search (skip TOC first)
    if conclusion_start == -1:
        for i in range(content_start, len(lines)):
            ls = lines[i].strip()
            if re.match(r'^[ZzZŽ]ávěr\s*$', ls):
                if i + 1 >= len(lines) or not re.match(r'^\d+$', lines[i+1].strip()):
                    conclusion_start = i
                    break
    
    # Find literature and appendix boundaries
    literature_start = -1
    appendix_start = -1
    
    for i in range(max(content_start, conclusion_start + 1 if conclusion_start != -1 else 0), len(lines)):
        ls = lines[i].strip()
        if re.match(r'^(Literatura|Reference|Bibliografie|Seznam\s+(literatury|použit|zkratek|symbol|obrázk|tabulek)|Použit\w+\s+zdroje)\s*$', ls, re.IGNORECASE):
            literature_start = i
            break
    
    for i in range(max(content_start, conclusion_start + 1 if conclusion_start != -1 else 0), len(lines)):
        ls = lines[i].strip()
        if re.match(r'^[PpŘř]říloha', ls) and (literature_start == -1 or abs(i - literature_start) > 5):
            appendix_start = i if literature_start == -1 else max(i, literature_start)
    
    # Set conclusion end
    if conclusion_start != -1:
        if appendix_start != -1 and appendix_start > conclusion_start:
            conclusion_end = appendix_start
        elif literature_start != -1 and literature_start > conclusion_start:
            conclusion_end = literature_start
        else:
            conclusion_end = min(conclusion_start + 40, len(lines))
    
    # Adjust intro end if not found
    if intro_start != -1 and intro_end == -1:
        if conclusion_start != -1 and conclusion_start > intro_start:
            intro_end = conclusion_start
        elif appendix_start != -1 and appendix_start > intro_start:
            intro_end = appendix_start
        elif literature_start != -1 and literature_start > intro_start:
            intro_end = literature_start
        else:
            intro_end = min(intro_start + 40, len(lines))
    
    # Main text goes from content_start to before literature/appendix
    main_end = len(lines)
    for boundary in [appendix_start, literature_start]:
        if boundary != -1 and boundary > content_start:
            main_end = min(main_end, boundary)
    
    # If conclusion found, main text extends to conclusion end
    if conclusion_start != -1:
        main_end = min(main_end, conclusion_start + 1)
    
    intro_text = "\n".join(lines[intro_start+1:intro_end]) if intro_start != -1 and intro_end != -1 else ""
    conclusion_text = "\n".join(lines[conclusion_start+1:conclusion_end]) if conclusion_start != -1 and conclusion_end != -1 else ""
    main_text = "\n".join(lines[content_start:main_end])
    
    return {
        "intro_text": intro_text.strip(),
        "conclusion_text": conclusion_text.strip(),
        "main_text": main_text.strip(),
        "intro_start": intro_start,
        "intro_end": intro_end,
        "conclusion_start": conclusion_start,
        "conclusion_end": conclusion_end,
        "main_end": main_end,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--year', type=int, choices=[2023, 2024], default=None)
    parser.add_argument('--skip-scrape', action='store_true', help='Skip scraping, use existing metadata')
    parser.add_argument('--skip-pdf', action='store_true', help='Skip PDF download')
    parser.add_argument('--limit', type=int, default=0, help='Limit thesis detail fetches (for testing)')
    args = parser.parse_args()
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    years = [args.year] if args.year else [2024, 2023]
    all_theses = {}
    
    # --- SCRAPE METADATA ---
    if not args.skip_scrape:
        for year in years:
            print(f"\n{'='*60}")
            print(f"YEAR {year}/{(year+1)}")
            print(f"{'='*60}")
            
            # Get first page
            html = fetch(SEARCH_URL, get_search_params(rok=year, str_page=1))
            if not html:
                print(f"  ERROR fetching first page for {year}")
                continue
            
            total_count, total_pages = extract_total_info(html)
            print(f"  Total theses: {total_count}, Pages: {total_pages}")
            
            # Collect all thesis IDs from all pages
            all_ids = set()
            for page in range(1, total_pages + 1):
                print(f"  Page {page}/{total_pages}...")
                page_html = html if page == 1 else fetch(SEARCH_URL, get_search_params(rok=year, str_page=page))
                if page_html:
                    ids = extract_thesis_ids(page_html)
                    all_ids.update(ids)
                time.sleep(0.3)
            
            print(f"  Total unique IDs: {len(all_ids)}")
            
            # Save IDs
            ids_path = os.path.join(OUTPUT_DIR, f"ids_{year}.json")
            with open(ids_path, 'w') as f:
                json.dump(sorted(list(all_ids)), f)
            
            # Get details for each thesis
            year_theses = {}
            saved = 0
            for i, tid in enumerate(sorted(all_ids)):
                if args.limit and i >= args.limit:
                    break
                
                detail_html = fetch(f"{BASE_URL}/studenti/zav-prace/detail/{tid}")
                info = extract_thesis_info(detail_html, tid)
                if info:
                    year_theses[tid] = info
                    if info.get('grade') in ('A', 'B', 'N/A'):
                        saved += 1
                        grade_str = info.get('grade', '?')
                        title_short = info.get('title', '?')[:50]
                        print(f"  [{i+1}/{len(all_ids)}] ID={tid} Grade={grade_str} | {title_short}")
                time.sleep(0.3)
            
            all_theses[year] = year_theses
            
            # Save metadata
            meta_path = os.path.join(OUTPUT_DIR, f"metadata_{year}.json")
            serializable = {}
            for tid, info in year_theses.items():
                sinfo = {k: v for k, v in info.items() if k != 'pdfs'}
                if 'pdfs' in info:
                    sinfo['pdfs'] = info['pdfs']
                serializable[str(tid)] = sinfo
            with open(meta_path, 'w', encoding='utf-8') as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)
            print(f"  Saved metadata for {len(year_theses)} theses to {meta_path}")
    else:
        # Load from saved metadata
        for year in years:
            meta_path = os.path.join(OUTPUT_DIR, f"metadata_{year}.json")
            if os.path.exists(meta_path):
                with open(meta_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                all_theses[year] = {int(k): v for k, v in data.items()}
                print(f"  Loaded {len(all_theses[year])} theses from {meta_path}")
    
    # --- FILTER A/B ---
    print(f"\n{'='*60}")
    print(f"A/B GRADE FILTER")
    print(f"{'='*60}")
    
    ab_theses = {}
    for year, theses in all_theses.items():
        ab = {tid: info for tid, info in theses.items() if info.get('grade') in ('A', 'B')}
        ab_theses[year] = ab
        print(f"  Year {year}: {len(ab)} A/B out of {len(theses)} total")
        for tid, info in sorted(ab.items()):
            print(f"    {info.get('grade')} | {info.get('title', '?')[:70]} | {info.get('author', '?')}")
    
    # Save A/B list
    ab_list = {}
    for year, theses in ab_theses.items():
        ab_list[str(year)] = {str(tid): info for tid, info in theses.items()}
    ab_path = os.path.join(OUTPUT_DIR, "ab_theses.json")
    with open(ab_path, 'w', encoding='utf-8') as f:
        json.dump(ab_list, f, ensure_ascii=False, indent=2)
    print(f"  Saved A/B list to {ab_path}")
    
    # --- DOWNLOAD PDFs ---
    if not args.skip_pdf:
        print(f"\n{'='*60}")
        print(f"DOWNLOAD PDFs")
        print(f"{'='*60}")
        
        downloaded = 0
        for year, theses in ab_theses.items():
            for tid, info in sorted(theses.items()):
                pdfs = info.get('pdfs', [])
                for pdf in pdfs:
                    name_lower = pdf.get('name', '').lower()
                    if 'text' in name_lower or 'práce' in name_lower or 'pra práce' in name_lower:
                        filename = f"{year}_{tid}_{pdf['name'].replace(' ','_')}.pdf"
                        filepath = os.path.join(OUTPUT_DIR, "pdfs", filename)
                        if download_pdf(pdf['url'], filepath):
                            downloaded += 1
                            info['pdf_path'] = filepath
                            print(f"  [{downloaded}] Downloaded: {filename} ({pdf['size']})")
                        time.sleep(0.5)
        
        print(f"\n  Total PDFs downloaded: {downloaded}")
    
    # --- ANALYZE TEXT ---
    print(f"\n{'='*60}")
    print(f"TEXT ANALYSIS")
    print(f"{'='*60}")
    
    analysis_results = []
    
    for year, theses in ab_theses.items():
        for tid, info in sorted(theses.items()):
            pdf_path = info.get('pdf_path')
            if not pdf_path or not os.path.exists(pdf_path):
                continue
            
            print(f"\n  Analyzing ID={tid}: {info.get('title','?')[:60]}...")
            text = extract_text_from_pdf(pdf_path)
            if not text or len(text) < 100:
                print(f"    Insufficient text ({len(text)} chars)")
                continue
            
            sections = find_sections(text)
            
            total_chars = len(text)
            total_ns = total_chars / 1800
            main_chars = len(sections['main_text'])
            main_ns = main_chars / 1800
            main_pages = main_chars / 2200
            
            intro_chars = len(sections['intro_text'])
            intro_ns = intro_chars / 1800
            conclusion_chars = len(sections['conclusion_text'])
            conclusion_ns = conclusion_chars / 1800
            
            result = {
                'year': f"{year}/{(year+1)}",
                'id': tid,
                'title': info.get('title', '?'),
                'author': info.get('author', '?'),
                'grade': info.get('grade', '?'),
                'total_chars': total_chars,
                'total_normostrany': round(total_ns, 1),
                'main_chars': main_chars,
                'main_normostrany': round(main_ns, 1),
                'main_pages_est': round(main_pages, 1),
                'intro_chars': intro_chars,
                'intro_normostrany': round(intro_ns, 1),
                'conclusion_chars': conclusion_chars,
                'conclusion_normostrany': round(conclusion_ns, 1),
                'intro_text': sections['intro_text'][:2000],
                'conclusion_text': sections['conclusion_text'][:2000],
            }
            analysis_results.append(result)
            
            print(f"    Total: {total_chars} chars, {total_ns:.1f} NS")
            print(f"    Main:  {main_chars} chars, {main_ns:.1f} NS, ~{main_pages:.0f} stran")
            if intro_ns > 0:
                print(f"    Úvod:  {intro_chars} chars, {intro_ns:.1f} NS")
            if conclusion_ns > 0:
                print(f"    Závěr: {conclusion_chars} chars, {conclusion_ns:.1f} NS")
    
    # Save results
    res_path = os.path.join(OUTPUT_DIR, "analysis_results.json")
    with open(res_path, 'w', encoding='utf-8') as f:
        json.dump(analysis_results, f, ensure_ascii=False, indent=2)
    print(f"\nSaved analysis to {res_path}")
    
    # --- GENERATE REPORT ---
    generate_report(analysis_results, ab_theses, all_theses, OUTPUT_DIR)

def generate_report(analysis_results, ab_theses, all_theses, output_dir):
    lines = []
    lines.append("=" * 70)
    lines.append("ANALÝZA BAKALÁŘSKÝCH PRACÍ FEKT VUT (hodnocení A a B)")
    lines.append("=" * 70)
    lines.append("")
    
    # Overview
    for year in sorted(ab_theses.keys(), reverse=True):
        theses = ab_theses[year]
        total_all = len(all_theses.get(year, {}))
        lines.append(f"\nRok {year}/{(year+1)}:")
        lines.append(f"  BP celkem: {total_all}")
        lines.append(f"  BP s A/B: {len(theses)}")
        grade_counts = Counter(info.get('grade') for info in theses.values())
        lines.append(f"    z toho A: {grade_counts.get('A', 0)}, B: {grade_counts.get('B', 0)}")
        
        # List theses
        for tid, info in sorted(theses.items()):
            lines.append(f"  {info.get('grade')} | {info.get('title', '?')}")
            lines.append(f"       Autor: {info.get('author', '?')}, Vedoucí: {info.get('vedouci', '?')}")
    
    # Length analysis
    if analysis_results:
        lines.append("\n" + "=" * 70)
        lines.append("DÉLKA TEXTU")
        lines.append("=" * 70)
        lines.append(f"\nCelkem analyzováno PDF: {len(analysis_results)}")
        lines.append("")
        
        for year in sorted(set(r['year'] for r in analysis_results)):
            yr = [r for r in analysis_results if r['year'] == year]
            if not yr:
                continue
            ns = [r['main_normostrany'] for r in yr]
            pg = [r['main_pages_est'] for r in yr]
            tot_ns = [r['total_normostrany'] for r in yr]
            intro_ns = [r['intro_normostrany'] for r in yr if r['intro_normostrany'] > 0]
            conc_ns = [r['conclusion_normostrany'] for r in yr if r['conclusion_normostrany'] > 0]
            
            lines.append(f"\n  {year} ({len(yr)} prací):")
            lines.append(f"    Hlavní text: NS Ø {sum(ns)/len(ns):.1f}  (min {min(ns):.1f}, max {max(ns):.1f})")
            lines.append(f"    Hlavní text: stran Ø {sum(pg)/len(pg):.1f}  (min {min(pg):.1f}, max {max(pg):.1f})")
            lines.append(f"    Celý PDF:   NS Ø {sum(tot_ns)/len(tot_ns):.1f}  (min {min(tot_ns):.1f}, max {max(tot_ns):.1f})")
            if intro_ns:
                lines.append(f"    Úvod:       NS Ø {sum(intro_ns)/len(intro_ns):.1f}  (min {min(intro_ns):.1f}, max {max(intro_ns):.1f})")
            if conc_ns:
                lines.append(f"    Závěr:      NS Ø {sum(conc_ns)/len(conc_ns):.1f}  (min {min(conc_ns):.1f}, max {max(conc_ns):.1f})")
        
        # Overall
        all_ns = [r['main_normostrany'] for r in analysis_results]
        all_pg = [r['main_pages_est'] for r in analysis_results]
        all_tot = [r['total_normostrany'] for r in analysis_results]
        all_intro = [r['intro_normostrany'] for r in analysis_results if r['intro_normostrany'] > 0]
        all_conc = [r['conclusion_normostrany'] for r in analysis_results if r['conclusion_normostrany'] > 0]
        
        lines.append(f"\n  CELKEM ({len(analysis_results)} prací):")
        lines.append(f"    Hlavní text: NS Ø {sum(all_ns)/len(all_ns):.1f}  (min {min(all_ns):.1f}, max {max(all_ns):.1f})")
        lines.append(f"    Hlavní text: stran Ø {sum(all_pg)/len(all_pg):.1f}  (min {min(all_pg):.1f}, max {max(all_pg):.1f})")
        lines.append(f"    Celý PDF:   NS Ø {sum(all_tot)/len(all_tot):.1f}  (min {min(all_tot):.1f}, max {max(all_tot):.1f})")
        if all_intro:
            lines.append(f"    Úvod:       NS Ø {sum(all_intro)/len(all_intro):.1f}  (min {min(all_intro):.1f}, max {max(all_intro):.1f})")
        if all_conc:
            lines.append(f"    Závěr:      NS Ø {sum(all_conc)/len(all_conc):.1f}  (min {min(all_conc):.1f}, max {max(all_conc):.1f})")
        
        # Ukázky úvodů
        lines.append("\n" + "=" * 70)
        lines.append("UKÁZKY ÚVODŮ (prvních 5 prací)")
        lines.append("=" * 70)
        for i, r in enumerate(analysis_results[:5]):
            lines.append(f"\n--- {i+1}. {r['title'][:60]} ({r['author']}) ---")
            if r['intro_text']:
                lines.append(r['intro_text'][:1500])
            else:
                lines.append("(úvod nebyl v textu nalezen)")
        
        # Ukázky závěrů
        lines.append("\n" + "=" * 70)
        lines.append("UKÁZKY ZÁVĚRŮ (prvních 5 prací)")
        lines.append("=" * 70)
        for i, r in enumerate(analysis_results[:5]):
            lines.append(f"\n--- {i+1}. {r['title'][:60]} ({r['author']}) ---")
            if r['conclusion_text']:
                lines.append(r['conclusion_text'][:1500])
            else:
                lines.append("(závěr nebyl v textu nalezen)")
    
    report = '\n'.join(lines)
    report_path = os.path.join(output_dir, "report.txt")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\nReport saved to {report_path}")
    print(report[:3000])

if __name__ == "__main__":
    main()
