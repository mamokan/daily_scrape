#!/usr/bin/env python
"""
Scraper for the Moroccan public procurement portal:
https://www.marchespublics.gov.ma (Advanced consultation search)

It performs a search by a start/end date range (date de mise en ligne)
and saves every result into a CSV file.

Usage:
    python scrape_marches.py --start 01/01/2024 --end 31/12/2024
    python scrape_marches.py --start 2024-01-01 --end 2024-12-31 --output out.csv
    python scrape_marches.py --last-days 7          # rolling range, auto-named file

Dates are accepted in dd/mm/yyyy or yyyy-mm-dd format and are sent to the
site as dd/mm/yyyy (the format the form expects).
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.marchespublics.gov.ma/index.php"
SEARCH_PATH = "?page=entreprise.EntrepriseAdvancedSearch&searchAnnCons"
FORM_ID = "ctl0_ctl4"

DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")
PAGER_NEXT_ID = "ctl0_CONTENU_PAGE_resultSearch_PagerTop_ctl2"


def log(msg):
    print(msg, flush=True)


def parse_date(value):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise SystemExit("Format de date invalide pour '%s' (utilisez JJ/MM/AAAA)" % value)


def collect_fields(soup):
    """Extract every form field (name -> value) from the PRADO form."""
    form = soup.find("form", id=FORM_ID)
    if form is None:
        return {}
    fields = {}
    for tag in form.find_all(["input", "select", "textarea"]):
        name = tag.get("name")
        if not name:
            continue
        if tag.name == "select":
            opt = tag.find("option", selected=True) or tag.find("option")
            val = opt.get("value") if opt else ""
        elif tag.get("type") in ("checkbox", "radio"):
            val = tag.get("value", "") if tag.get("checked") else ""
        else:
            val = tag.get("value", "")
        fields[name] = val
    return fields


def clean(text):
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def strip_prefix(text, *prefixes):
    for p in prefixes:
        if text.startswith(p):
            text = text[len(p):].strip()
    return text


def visible_text(el):
    """Return the text of an element, ignoring hidden info-bulle/info-suite tooltips."""
    if el is None:
        return ""
    out = []
    for t in el.find_all(string=True):
        p = t.parent
        skip = False
        while p is not None:
            cls = p.get("class") or []
            if any("info-bulle" in c for c in cls) or any("info-suite" in c for c in cls):
                skip = True
                break
            p = p.parent
        if not skip:
            out.append(t)
    return clean(" ".join(out))


def extract_rows(soup):
    """Return a list of dicts, one per result row found in the page."""
    rows = []
    # Each result row owns a hidden input named *$tableauResultSearch$ctlN$refCons
    for inp in soup.find_all("input", attrs={"name": lambda x: x and "$tableauResultSearch$ctl" in x and x.endswith("refCons")}):
        tr = inp.find_parent("tr")
        if tr is None:
            continue

        ref = clean(inp.get("value"))
        org = tr.find("input", attrs={"name": lambda x: x and x.endswith("orgCons")})
        org_val = clean(org.get("value")) if org else ""

        reference = tr.find(id=re.compile(r"_reference$"))
        categorie = tr.find(id=re.compile(r"_panelBlocCategorie$"))
        objet_el = tr.find(id=re.compile(r"_panelBlocObjet$"))
        objet_full = tr.find(id=re.compile(r"infosBullesObjet"))
        acheteur_el = tr.find(id=re.compile(r"_panelBlocDenomination$"))
        lieux_el = tr.find(id=re.compile(r"_panelBlocLieuxExec$"))
        type_el = tr.find(id=re.compile(r"identification_cons_\d+_type_procedure"))
        cloture = tr.find("div", class_="cloture-line")

        # Detail link: the "Accéder à la consultation" action in panelAction
        lien_detail = ""
        action = tr.find(id=re.compile(r"panelAction$"))
        if action:
            a = action.find("a", attrs={"href": re.compile(r"EntrepriseDetailConsultation")})
            if a and a.get("href"):
                href = a["href"]
                lien_detail = BASE_URL + href if href.startswith("?") else href

        # Date de publication: first dd/mm/yyyy found in the first col-90 cell
        date_pub = ""
        first_col = tr.find("td", class_="col-90")
        if first_col:
            m = DATE_RE.search(first_col.get_text(" ", strip=True))
            if m:
                date_pub = m.group(0)

        if objet_full and visible_text(objet_full):
            objet = visible_text(objet_full)
        else:
            objet = strip_prefix(visible_text(objet_el), "Objet :", "Objet:")
        acheteur = strip_prefix(visible_text(acheteur_el), "Acheteur public :", "Acheteur public:")

        rows.append({
            "reference": clean(reference.get_text()) if reference else "",
            #"categorie": visible_text(categorie),
            #"type_procedure": visible_text(type_el),
            "date_publication": date_pub,
            "acheteur": acheteur,
            "objet": objet,
            #"lieux_execution": visible_text(lieux_el),
            "date_limite": clean(cloture.get_text(" ", strip=True)) if cloture else "",
            #"ref_cons": ref,
            #"org_cons": org_val,
            "lien_detail": lien_detail,
        })
    return rows


def has_next_page(soup):
    """True when the 'next page' control is still an active link."""
    return soup.find("a", id=PAGER_NEXT_ID) is not None


def do_search(session, start, end):
    """Run the initial search and return (first_page_soup, fields)."""
    url = BASE_URL + SEARCH_PATH
    r = session.get(url, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    fields = collect_fields(soup)
    if not fields:
        raise SystemExit("Impossible de lire le formulaire de recherche (PRADO).")

    # "Date de mise en ligne" (publication date) is the *Calcule field on this site.
    fields["ctl0$CONTENU_PAGE$AdvancedSearch$dateMiseEnLigneCalculeStart"] = start
    fields["ctl0$CONTENU_PAGE$AdvancedSearch$dateMiseEnLigneCalculeEnd"] = end
    # Keep the other date field wide open so it doesn't add a conflicting filter.
    fields["ctl0$CONTENU_PAGE$AdvancedSearch$dateMiseEnLigneStart"] = "01/01/1990"
    fields["ctl0$CONTENU_PAGE$AdvancedSearch$dateMiseEnLigneEnd"] = "31/12/2099"
    fields["ctl0$CONTENU_PAGE$AdvancedSearch$lancerRecherche"] = "Lancer la recherche"
    fields["PRADO_POSTBACK_TARGET"] = "ctl0$CONTENU_PAGE$AdvancedSearch$lancerRecherche"
    fields["PRADO_POSTBACK_PARAMETER"] = ""

    r = session.post(url, data=fields, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser"), fields


def set_page_size(session, current_soup, size):
    """Trigger the site's page-size postback so more rows are returned per page."""
    url = BASE_URL + SEARCH_PATH
    fields = collect_fields(current_soup)
    key = "ctl0$CONTENU_PAGE$resultSearch$listePageSizeTop"
    if key not in fields:
        return current_soup
    fields[key] = str(size)
    fields["PRADO_POSTBACK_TARGET"] = key
    fields["PRADO_POSTBACK_PARAMETER"] = ""
    r = session.post(url, data=fields, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def go_next_page(session, current_soup):
    url = BASE_URL + SEARCH_PATH
    fields = collect_fields(current_soup)
    fields["PRADO_POSTBACK_TARGET"] = "ctl0$CONTENU_PAGE$resultSearch$PagerTop$ctl2"
    fields["PRADO_POSTBACK_PARAMETER"] = ""
    r = session.post(url, data=fields, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def main():
    parser = argparse.ArgumentParser(description="Scrape consultations from marchespublics.gov.ma by date range")
    parser.add_argument("--start", help="Start date (JJ/MM/AAAA or AAAA-MM-JJ)")
    parser.add_argument("--end", help="End date (JJ/MM/AAAA or AAAA-MM-JJ)")
    parser.add_argument("--last-days", type=int, help="Rolling range: scrape the last N days (end = today)")
    parser.add_argument("--output", help="Output CSV file path (auto-named from dates if omitted)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between page requests (seconds)")
    parser.add_argument("--max-pages", type=int, default=0, help="Max pages to scrape (0 = unlimited)")
    parser.add_argument("--page-size", type=int, default=500, help="Results per page (10/20/50/100/500)")
    args = parser.parse_args()

    if args.last_days:
        end_dt = datetime.today()
        start_dt = end_dt - timedelta(days=args.last_days)
    else:
        if not args.start or not args.end:
            parser.error("Specify --start and --end, or use --last-days N.")
        start_dt = parse_date(args.start)
        end_dt = parse_date(args.end)

    start = start_dt.strftime("%d/%m/%Y")
    end = end_dt.strftime("%d/%m/%Y")

    if not args.output:
        args.output = "resultats_%s_au_%s.csv" % (start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    log("Recherche du %s au %s ..." % (start, end))
    soup, _ = do_search(session, start, end)

    if args.page_size and args.page_size != 10:
        log("Changement du nombre de resultats par page -> %d ..." % args.page_size)
        soup = set_page_size(session, soup, args.page_size)

    fieldnames = [
        "reference", "date_publication",
        "acheteur", "objet","date_limite",
         "lien_detail"
    ]

    total = 0
    page = 1
    with open(args.output, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

        while True:
            rows = extract_rows(soup)
            if not rows and page == 1:
                log("Aucun resultat pour cette plage de dates.")
                break

            for row in rows:
                writer.writerow(row)
            total += len(rows)
            log("Page %d : %d resultat(s) (total %d)" % (page, len(rows), total))

            if not has_next_page(soup):
                break
            if args.max_pages and page >= args.max_pages:
                log("Limite de pages atteinte (%d)." % args.max_pages)
                break

            page += 1
            time.sleep(args.delay)
            soup = go_next_page(session, soup)

    log("Terminé. %d resultat(s) enregistres dans '%s'." % (total, args.output))


if __name__ == "__main__":
    main()
