"""Pubs - Aggregate research publications from ORCID API.

Copyright (c) 2026, Alin M. Elena and contributors
Distributed under the terms of the BSD 3-Clause License.
"""
import argparse
import csv
from datetime import datetime, timezone
import html
import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Union
import requests

DEFAULT_CSV_PATH = "data/authors.csv"

# Fallback dictionary if CSV is not found
DEFAULT_ORCID_IDS: Dict[str, str] = {
    "0000-0002-7013-6670": "Alin Marin Elena",
}


def load_orcids_from_csv(csv_path: str = DEFAULT_CSV_PATH) -> Dict[str, str]:
    """Load ORCID to author name mapping from a CSV file.

    Format expected: 'orcid,name' with optional header row.
    Lines beginning with '#' and empty rows are ignored.
    ORCID values are stripped of any leading URL prefixes (e.g. 'https://orcid.org/').
    """
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Authors CSV file not found at '{csv_path}'")

    mapping: Dict[str, str] = {}
    orcid_pattern = re.compile(r"^(\d{4}-\d{4}-\d{4}-\d{3}[\dX])$", re.IGNORECASE)

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for line_num, row in enumerate(reader, start=1):
            if not row or not any(field.strip() for field in row):
                continue

            first_cell = row[0].strip()
            if first_cell.startswith("#"):
                continue

            # Header row detection
            if first_cell.lower() in ("orcid", "orcid_id", "orcid id", "id"):
                continue

            if len(row) < 2:
                print(f"Warning: Skipping invalid row {line_num} in {csv_path}: expected 'orcid,name'")
                continue

            raw_orcid = row[0].strip()
            name = row[1].strip()

            clean_orcid = re.sub(r"^https?://(www\.)?orcid\.org/", "", raw_orcid, flags=re.IGNORECASE).strip()

            if not orcid_pattern.match(clean_orcid):
                print(f"Warning: Skipping row {line_num} in {csv_path}: invalid ORCID '{raw_orcid}'")
                continue

            if not name:
                print(f"Warning: Skipping row {line_num} in {csv_path}: missing author name for '{clean_orcid}'")
                continue

            mapping[clean_orcid] = name

    return mapping


def get_configured_orcid_ids(csv_path: str = DEFAULT_CSV_PATH) -> Dict[str, str]:
    """Retrieve ORCID mapping from CSV if available, otherwise return default mapping."""
    if os.path.isfile(csv_path):
        try:
            loaded = load_orcids_from_csv(csv_path)
            if loaded:
                return loaded
        except Exception as e:
            print(f"Warning: Could not read {csv_path}: {e}")
    return dict(DEFAULT_ORCID_IDS)


# Initialized from CSV if present
ORCID_IDS: Dict[str, str] = get_configured_orcid_ids()
ORCIDS_IDS = ORCID_IDS

ORCID_API_BASE = "https://pub.orcid.org/v3.0"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "pubs-fetcher/1.0 (https://orcid.org; mailto:admin@example.org)",
}


def extract_doi(external_ids: Dict[str, Any] | None) -> str | None:
    """Find and normalize DOI from external-ids field."""
    if not external_ids or not isinstance(external_ids, dict):
        return None
    for ext_id in external_ids.get("external-id") or []:
        if not ext_id or not isinstance(ext_id, dict):
            continue
        id_type = ext_id.get("external-id-type")
        if id_type and str(id_type).lower() == "doi":
            doi_val = ext_id.get("external-id-value")
            if doi_val and isinstance(doi_val, str):
                doi_clean = doi_val.strip()
                doi_clean = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi_clean, flags=re.IGNORECASE)
                doi_clean = re.sub(r"^doi:\s*", "", doi_clean, flags=re.IGNORECASE)
                return doi_clean.strip().lower()
    return None


def extract_fallback_url(summary: Dict[str, Any], group: Dict[str, Any] | None = None) -> str | None:
    """Extract alternative URL from summary url field or external-ids."""
    url_obj = summary.get("url")
    if isinstance(url_obj, dict):
        val = url_obj.get("value")
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    # Check external-id URLs in summary and group
    containers = [summary.get("external-ids")]
    if group and isinstance(group, dict):
        containers.append(group.get("external-ids"))

    for container in containers:
        if not container or not isinstance(container, dict):
            continue
        for ext_id in container.get("external-id") or []:
            if not ext_id or not isinstance(ext_id, dict):
                continue
            ext_url_obj = ext_id.get("external-id-url")
            if isinstance(ext_url_obj, dict):
                ext_url = ext_url_obj.get("value")
                if ext_url and isinstance(ext_url, str) and ext_url.strip():
                    return ext_url.strip()
            # If the external-id itself is a URL
            id_type = ext_id.get("external-id-type")
            if id_type and str(id_type).lower() in ("uri", "url"):
                id_val = ext_id.get("external-id-value")
                if id_val and isinstance(id_val, str) and id_val.strip().startswith("http"):
                    return id_val.strip()
    return None


def assemble_doi_url(doi: Optional[str]) -> Optional[str]:
    """Assemble a standard HTTPS DOI URL from a DOI string."""
    if not doi or not isinstance(doi, str):
        return None
    clean = doi.strip()
    clean = re.sub(r"^https?://(dx\.)?doi\.org/", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^doi:\s*", "", clean, flags=re.IGNORECASE).strip()
    return f"https://doi.org/{clean}" if clean else None


def get_publication_url(pub: Dict[str, Any]) -> Optional[str]:
    """Assemble publication URL using DOI if present, falling back to url field."""
    doi = pub.get("doi")
    if doi:
        assembled = assemble_doi_url(doi)
        if assembled:
            return assembled
    return pub.get("url")


def select_best_summary(summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Select the preferred work summary in a group (display-index 0), or the first."""
    if not summaries:
        return {}
    for s in summaries:
        if str(s.get("display-index", "")).strip() == "0":
            return s
    return summaries[0]


def fetch_member_works(orcid_id: str, cache_dir: str = "data/cache") -> List[Dict[str, Any]]:
    """Retrieve all work summaries for an individual ORCID record, with offline cache support."""
    url = f"{ORCID_API_BASE}/{orcid_id}/works"
    data = None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if cache_dir:
                try:
                    os.makedirs(cache_dir, exist_ok=True)
                    cache_file = os.path.join(cache_dir, f"{orcid_id}.json")
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)
                except OSError:
                    pass
        else:
            print(f"Warning: Failed to fetch {orcid_id} (HTTP {resp.status_code})")
    except requests.RequestException as e:
        print(f"Warning: Network error fetching {orcid_id}: {e}")

    # Fallback to local cache if network response not available
    if not data and cache_dir:
        cache_file = os.path.join(cache_dir, f"{orcid_id}.json")
        if os.path.isfile(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                print(f"Loaded cached records for {orcid_id}")
            except Exception as e:
                print(f"Warning: Could not read cache for {orcid_id}: {e}")

    if not data:
        return []

    group_items = data.get("group") or []
    records = []

    for group in group_items:
        # Each group contains one or more work summaries (usually versions of the same work)
        summaries = group.get("work-summary") or []
        if not summaries:
            continue

        summary = select_best_summary(summaries)

        # Extract title (fallback across summaries if preferred lacks one)
        title = None
        for s in [summary] + summaries:
            title_obj = s.get("title")
            if isinstance(title_obj, dict):
                inner_title = title_obj.get("title")
                if isinstance(inner_title, dict):
                    val = inner_title.get("value")
                    if val and isinstance(val, str) and val.strip():
                        title = val.strip()
                        break
        if not title:
            continue

        # Extract publication year (fallback across summaries)
        year = None
        for s in [summary] + summaries:
            pub_date = s.get("publication-date")
            if isinstance(pub_date, dict):
                year_obj = pub_date.get("year")
                if isinstance(year_obj, dict):
                    y_val = year_obj.get("value")
                    if y_val and str(y_val).strip():
                        year = str(y_val).strip()
                        break
        if not year:
            year = "Unknown"

        # Extract journal / publication venue (fallback across summaries)
        venue = None
        for s in [summary] + summaries:
            journal_obj = s.get("journal-title")
            if isinstance(journal_obj, dict):
                j_val = journal_obj.get("value")
                if j_val and isinstance(j_val, str) and j_val.strip():
                    venue = j_val.strip()
                    break

        # Extract DOI (check summary, all summaries, and group)
        doi = extract_doi(summary.get("external-ids"))
        if not doi:
            for s in summaries:
                doi = extract_doi(s.get("external-ids"))
                if doi:
                    break
        if not doi:
            doi = extract_doi(group.get("external-ids"))

        # URL: assemble using DOI if available, or fall back to alternative URL
        work_url = assemble_doi_url(doi) or extract_fallback_url(summary, group)

        records.append({
            "title": title,
            "year": year,
            "journal": venue,
            "doi": doi,
            "url": work_url,
            "type": summary.get("type") or "other",
        })

    return records


def normalize_title(title: str) -> str:
    """Normalize title for fuzzy comparison during deduplication."""
    return re.sub(r"\W+", "", title.lower())


def aggregate_publications(
    orcids: Sequence[str] | Dict[str, str] | None = None,
    cache_dir: str = "data/cache",
) -> List[Dict[str, Any]]:
    """Fetch and deduplicate publications across all researchers with author tracking."""
    if orcids is None:
        mapping = ORCID_IDS
    elif isinstance(orcids, dict):
        mapping = orcids
    else:
        mapping = {o: ORCID_IDS.get(o, o) for o in orcids}

    deduped: List[Dict[str, Any]] = []
    doi_map: Dict[str, Dict[str, Any]] = {}
    title_map: Dict[str, Dict[str, Any]] = {}

    for orcid, author_name in mapping.items():
        works = fetch_member_works(orcid, cache_dir=cache_dir)
        for work in works:
            doi = work.get("doi")
            norm_title = normalize_title(work["title"])

            existing = None
            if doi and doi in doi_map:
                existing = doi_map[doi]
            elif norm_title in title_map:
                existing = title_map[norm_title]

            if existing:
                # Merge author tracking across group members
                if author_name and author_name not in existing.setdefault("authors", []):
                    existing["authors"].append(author_name)
                if orcid and orcid not in existing.setdefault("orcids", []):
                    existing["orcids"].append(orcid)

                # Merge richer metadata if available
                if not existing.get("doi") and doi:
                    existing["doi"] = doi
                    doi_map[doi] = existing
                if existing.get("doi"):
                    existing["url"] = assemble_doi_url(existing["doi"])
                elif not existing.get("url") and work.get("url"):
                    existing["url"] = work["url"]
                if not existing.get("journal") and work.get("journal"):
                    existing["journal"] = work["journal"]
                if existing.get("year") == "Unknown" and work.get("year") != "Unknown":
                    existing["year"] = work["year"]
            else:
                work_copy = dict(work)
                if work_copy.get("doi"):
                    work_copy["url"] = assemble_doi_url(work_copy["doi"])
                work_copy["authors"] = [author_name] if author_name else []
                work_copy["orcids"] = [orcid] if orcid else []
                deduped.append(work_copy)
                if doi:
                    doi_map[doi] = work_copy
                if norm_title:
                    title_map[norm_title] = work_copy

    # Sort descending by year (Unknowns at the end), then alphabetically by title
    def sort_key(item):
        yr = item["year"]
        year_num = int(yr) if str(yr).isdigit() else 0
        return (-year_num, item.get("title", "").lower())

    return sorted(deduped, key=sort_key)


def generate_markdown(
    publications: List[Dict[str, Any]],
    output_path: str = "PUBLICATIONS.md",
    last_updated: Optional[str] = None,
):
    """Write grouped publications by year to Markdown."""
    if last_updated is None:
        last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = [
        "# Group Publications",
        "",
        "*Auto-generated via ORCID Public API*",
        f"*Last updated: {last_updated}*",
    ]

    current_year = None
    for pub in publications:
        year = pub["year"]
        if year != current_year:
            current_year = year
            lines.extend(["", f"## {current_year}", ""])

        target_url = get_publication_url(pub)
        title_str = f"**[{pub['title']}]({target_url})**" if target_url else f"**{pub['title']}**"
        venue_str = f" *{pub['journal']}*." if pub.get("journal") else ""
        doi_str = f" [DOI: {pub['doi']}](https://doi.org/{pub['doi']})" if pub.get("doi") else ""

        lines.append(f"- {title_str}{venue_str}{doi_str}")

    lines.extend([
        "",
        "---",
        "",
        "Copyright (c) 2026, Alin M. Elena and contributors. Released under the [BSD 3-Clause License](LICENSE).",
    ])

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")
    print(f"Generated {output_path} with {len(publications)} publications.")


def export_json(publications: List[Dict[str, Any]], output_path: str = "publications.json"):
    """Export publications list to JSON file."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(publications, f, indent=2, ensure_ascii=False)
    print(f"Exported {len(publications)} publications to {output_path}")


def build_html_page(
    publications: List[Dict[str, Any]],
    orcid_dict: Dict[str, str],
    last_updated: Optional[str] = None,
) -> str:
    """Build the complete self-contained HTML page for GitHub Pages."""
    template = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Data Driven Materials and Molecular Science | Publications</title>
  <meta name="description" content="Data Driven Materials and Molecular Science publications aggregated from ORCID with filtering by author and year.">
  <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>📚</text></svg>">
  <style>
    :root {
      --bg: #f8fafc;
      --card-bg: #ffffff;
      --text: #0f172a;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --primary-light: #eff6ff;
      --badge-bg: #f1f5f9;
      --badge-text: #475569;
      --orcid-color: #a6ce39;
      --tag-author-bg: #ecfdf5;
      --tag-author-text: #065f46;
      --tag-author-border: #a7f3d0;
      --shadow: 0 1px 3px rgba(0, 0, 0, 0.05), 0 1px 2px rgba(0, 0, 0, 0.03);
      --shadow-hover: 0 4px 6px -1px rgba(0, 0, 0, 0.08), 0 2px 4px -2px rgba(0, 0, 0, 0.05);
      --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }

    [data-theme="dark"] {
      --bg: #0b1120;
      --card-bg: #1e293b;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --border: #334155;
      --primary: #38bdf8;
      --primary-hover: #7dd3fc;
      --primary-light: #082f49;
      --badge-bg: #334155;
      --badge-text: #cbd5e1;
      --orcid-color: #a6ce39;
      --tag-author-bg: #064e3b;
      --tag-author-text: #6ee7b7;
      --tag-author-border: #047857;
      --shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
      --shadow-hover: 0 4px 6px -1px rgba(0, 0, 0, 0.4);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--font);
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 0 1rem 3rem;
      transition: background-color 0.2s, color 0.2s;
    }

    .container {
      max-width: 1100px;
      margin: 0 auto;
    }

    /* Header */
    header {
      padding: 2.5rem 0 1.5rem;
      border-bottom: 1px solid var(--border);
      margin-bottom: 2rem;
    }
    .header-top {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 1rem;
      flex-wrap: wrap;
    }
    .header-title-group h1 {
      font-size: 2.2rem;
      font-weight: 800;
      letter-spacing: -0.025em;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 0.6rem;
    }
    .header-title-group p {
      color: var(--text-muted);
      margin-top: 0.25rem;
      font-size: 1.05rem;
    }
    .header-actions {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      flex-wrap: wrap;
    }
    .btn {
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      padding: 0.5rem 0.9rem;
      font-size: 0.875rem;
      font-weight: 500;
      border-radius: 6px;
      cursor: pointer;
      text-decoration: none;
      transition: all 0.15s ease;
      border: 1px solid var(--border);
      background: var(--card-bg);
      color: var(--text);
    }
    .btn:hover {
      background: var(--badge-bg);
      border-color: var(--primary);
    }
    .btn-primary {
      background: var(--primary);
      color: #ffffff !important;
      border-color: var(--primary);
    }
    .btn-primary:hover {
      background: var(--primary-hover);
    }

    /* Stats Grid */
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 1rem;
      margin-bottom: 2rem;
    }
    .stat-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1.1rem;
      box-shadow: var(--shadow);
    }
    .stat-label {
      font-size: 0.8rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
    }
    .stat-value {
      font-size: 1.75rem;
      font-weight: 800;
      color: var(--primary);
      margin-top: 0.2rem;
    }

    /* Controls */
    .controls-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 1.25rem;
      margin-bottom: 2rem;
      box-shadow: var(--shadow);
    }
    .controls-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      align-items: flex-end;
    }
    .form-group {
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
    }
    .form-group label {
      font-size: 0.825rem;
      font-weight: 600;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 0.3rem;
    }
    .form-control {
      width: 100%;
      padding: 0.55rem 0.75rem;
      font-size: 0.9rem;
      font-family: inherit;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--card-bg);
      color: var(--text);
      outline: none;
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    .form-control:focus {
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.15);
    }
    .controls-extra {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-top: 1rem;
      padding-top: 1rem;
      border-top: 1px solid var(--border);
    }
    .filter-options {
      display: flex;
      align-items: center;
      gap: 1.2rem;
    }
    .checkbox-label {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      font-size: 0.85rem;
      cursor: pointer;
      user-select: none;
    }

    /* Active Filters & Counter */
    .results-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.5rem;
      flex-wrap: wrap;
      gap: 0.75rem;
    }
    .results-counter {
      font-size: 0.95rem;
      color: var(--text-muted);
    }
    .results-counter strong {
      color: var(--text);
    }
    .active-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      align-items: center;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.25rem 0.6rem;
      background: var(--primary-light);
      color: var(--primary);
      border-radius: 9999px;
      font-size: 0.775rem;
      font-weight: 500;
      border: 1px solid rgba(37, 99, 235, 0.2);
    }
    .pill-close {
      cursor: pointer;
      font-weight: bold;
      border: none;
      background: none;
      color: inherit;
      line-height: 1;
    }

    /* Year Section */
    .year-section {
      margin-bottom: 2.5rem;
    }
    .year-header {
      font-size: 1.45rem;
      font-weight: 700;
      color: var(--text);
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 1rem;
      padding-bottom: 0.4rem;
      border-bottom: 2px solid var(--primary);
    }
    .year-count-badge {
      font-size: 0.8rem;
      font-weight: 600;
      color: var(--text-muted);
      background: var(--badge-bg);
      padding: 0.15rem 0.6rem;
      border-radius: 9999px;
    }

    /* Publication Cards */
    .pubs-list {
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }
    .pub-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1.25rem;
      box-shadow: var(--shadow);
      transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
      display: flex;
      flex-direction: column;
      gap: 0.65rem;
    }
    .pub-card:hover {
      box-shadow: var(--shadow-hover);
      border-color: var(--primary);
      transform: translateY(-1px);
    }
    .pub-title {
      font-size: 1.05rem;
      font-weight: 600;
      line-height: 1.4;
      color: var(--text);
    }
    .pub-title a {
      color: inherit;
      text-decoration: none;
      transition: color 0.15s;
    }
    .pub-title a:hover {
      color: var(--primary);
    }
    .pub-meta {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.6rem;
      font-size: 0.85rem;
    }
    .pub-authors {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      align-items: center;
    }
    .author-chip {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      background: var(--tag-author-bg);
      color: var(--tag-author-text);
      border: 1px solid var(--tag-author-border);
      padding: 0.15rem 0.55rem;
      border-radius: 9999px;
      font-size: 0.775rem;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.15s;
      text-decoration: none;
    }
    .author-chip:hover {
      opacity: 0.85;
    }
    .orcid-icon {
      width: 13px;
      height: 13px;
      fill: var(--orcid-color);
      flex-shrink: 0;
    }
    .pub-venue {
      color: var(--text-muted);
      font-style: italic;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 0.15rem 0.5rem;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 600;
      background: var(--badge-bg);
      color: var(--badge-text);
    }
    .badge-year {
      background: var(--primary-light);
      color: var(--primary);
      font-weight: 700;
    }
    .badge-doi {
      background: #f3e8ff;
      color: #6b21a8;
      border: 1px solid #e9d5ff;
      text-decoration: none;
      transition: background 0.15s;
    }
    [data-theme="dark"] .badge-doi {
      background: #3b0764;
      color: #d8b4fe;
      border-color: #581c87;
    }
    .badge-doi:hover {
      opacity: 0.85;
    }

    .pub-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      margin-top: 0.25rem;
      align-items: center;
    }
    .btn-action {
      padding: 0.25rem 0.6rem;
      font-size: 0.75rem;
      font-weight: 500;
      border-radius: 4px;
      background: var(--badge-bg);
      color: var(--text-muted);
      border: 1px solid var(--border);
      cursor: pointer;
      transition: all 0.15s;
      display: inline-flex;
      align-items: center;
      gap: 0.3rem;
    }
    .btn-action:hover {
      background: var(--primary-light);
      color: var(--primary);
      border-color: var(--primary);
    }

    /* Empty state */
    .empty-state {
      text-align: center;
      padding: 4rem 1rem;
      background: var(--card-bg);
      border: 1px dashed var(--border);
      border-radius: 12px;
      color: var(--text-muted);
    }
    .empty-state h3 {
      font-size: 1.2rem;
      font-weight: 700;
      color: var(--text);
      margin-bottom: 0.5rem;
    }

    /* Toast */
    .toast {
      position: fixed;
      bottom: 2rem;
      right: 2rem;
      background: #0f172a;
      color: #ffffff;
      padding: 0.75rem 1.25rem;
      border-radius: 8px;
      font-size: 0.875rem;
      font-weight: 500;
      box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.2);
      z-index: 1000;
      opacity: 0;
      transform: translateY(10px);
      transition: opacity 0.2s, transform 0.2s;
      pointer-events: none;
    }
    [data-theme="dark"] .toast {
      background: #f8fafc;
      color: #0f172a;
    }
    .toast.show {
      opacity: 1;
      transform: translateY(0);
    }

    /* Footer */
    footer {
      margin-top: 3rem;
      padding-top: 1.5rem;
      border-top: 1px solid var(--border);
      color: var(--text-muted);
      font-size: 0.85rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
    }
    footer p {
      margin: 0.2rem 0;
    }
    footer a { color: var(--primary); text-decoration: none; }
    footer a:hover { text-decoration: underline; }
    .footer-right {
      text-align: right;
    }

    @media (max-width: 640px) {
      .header-title-group h1 { font-size: 1.7rem; }
      .controls-grid { grid-template-columns: 1fr; }
      .results-bar { flex-direction: column; align-items: flex-start; }
      .footer-right { text-align: left; }
    }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="header-top">
        <div class="header-title-group">
          <h1><span>📚</span> Research Publications</h1>
          <p>Data Driven Materials and Molecular Science</p>
        </div>
        <div class="header-actions">
          <button id="theme-btn" class="btn" title="Toggle theme">
            <span id="theme-icon">🌓</span> <span id="theme-text">Theme</span>
          </button>
          <a href="PUBLICATIONS.md" class="btn" title="View Markdown">
            <span>📝</span> Markdown
          </a>
          <a href="publications.json" class="btn" title="JSON Feed">
            <span>📦</span> JSON
          </a>
          <a href="https://github.com/ddmms/pubs" target="_blank" rel="noopener noreferrer" class="btn btn-primary" title="GitHub Repository">
            <span>🐙</span> GitHub
          </a>
        </div>
      </div>
    </header>

    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-label">Total Publications</div>
        <div class="stat-value" id="stat-total-pubs">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Group Authors</div>
        <div class="stat-value" id="stat-authors">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Years Span</div>
        <div class="stat-value" id="stat-years">0</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Venues / Journals</div>
        <div class="stat-value" id="stat-venues">0</div>
      </div>
    </div>

    <section class="controls-card">
      <div class="controls-grid">
        <div class="form-group">
          <label for="author-filter">👤 Filter by Author</label>
          <select id="author-filter" class="form-control">
            <option value="all">All Group Authors</option>
          </select>
        </div>
        <div class="form-group">
          <label for="year-filter">📅 Filter by Year</label>
          <select id="year-filter" class="form-control">
            <option value="all">All Years</option>
          </select>
        </div>
        <div class="form-group">
          <label for="search-input">🔍 Search</label>
          <input type="search" id="search-input" class="form-control" placeholder="Search title, venue, DOI...">
        </div>
        <div class="form-group">
          <label for="sort-select">⚡ Sort By</label>
          <select id="sort-select" class="form-control">
            <option value="year-desc">Year (Newest first)</option>
            <option value="year-asc">Year (Oldest first)</option>
            <option value="title-asc">Title (A to Z)</option>
          </select>
        </div>
      </div>

      <div class="controls-extra">
        <div class="filter-options">
          <label class="checkbox-label">
            <input type="checkbox" id="group-year-toggle" checked>
            <span>Group by Year</span>
          </label>
        </div>
        <button id="reset-filters-btn" class="btn">
          <span>🔄</span> Reset Filters
        </button>
      </div>
    </section>

    <div class="results-bar">
      <div class="results-counter">
        Showing <strong id="visible-count">0</strong> of <strong id="total-count">0</strong> publications
      </div>
      <div id="active-pills" class="active-pills"></div>
    </div>

    <main id="pubs-container">
      <!-- Publications will be rendered dynamically -->
    </main>

    <footer>
      <div class="footer-left">
        <p>&copy; 2026 Alin M. Elena and contributors. Released under the <a href="LICENSE">BSD 3-Clause License</a>.</p>
        <p>Aggregated via <a href="https://pub.orcid.org" target="_blank" rel="noopener noreferrer">ORCID Public API</a>.</p>
      </div>
      <div class="footer-right">
        <p>Last updated: <time datetime="__LAST_UPDATED_ISO__">__LAST_UPDATED_TEXT__</time></p>
        <p>Hosted on <a href="https://pages.github.com" target="_blank" rel="noopener noreferrer">GitHub Pages</a>.</p>
      </div>
    </footer>
  </div>

  <div id="toast" class="toast">Copied to clipboard!</div>

  <script id="publications-data" type="application/json">
__PUBLICATIONS_JSON__
  </script>
  <script id="authors-data" type="application/json">
__AUTHORS_JSON__
  </script>

  <script>
    (function() {
      // Load embedded data
      const pubsDataEl = document.getElementById('publications-data');
      const authorsDataEl = document.getElementById('authors-data');
      let publications = [];
      let authorsDict = {};

      try {
        publications = JSON.parse(pubsDataEl.textContent.trim());
      } catch (e) {
        console.error('Failed to parse publications data', e);
      }
      try {
        authorsDict = JSON.parse(authorsDataEl.textContent.trim());
      } catch (e) {
        console.error('Failed to parse authors data', e);
      }

      // Map author names to ORCID IDs
      const nameToOrcid = {};
      for (const [orcid, name] of Object.entries(authorsDict)) {
        nameToOrcid[name] = orcid;
      }

      // DOM Elements
      const authorFilter = document.getElementById('author-filter');
      const yearFilter = document.getElementById('year-filter');
      const searchInput = document.getElementById('search-input');
      const sortSelect = document.getElementById('sort-select');
      const groupYearToggle = document.getElementById('group-year-toggle');
      const resetBtn = document.getElementById('reset-filters-btn');
      const pubsContainer = document.getElementById('pubs-container');
      const visibleCountEl = document.getElementById('visible-count');
      const totalCountEl = document.getElementById('total-count');
      const activePillsEl = document.getElementById('active-pills');
      const toastEl = document.getElementById('toast');

      // Stats Elements
      const statTotalPubs = document.getElementById('stat-total-pubs');
      const statAuthors = document.getElementById('stat-authors');
      const statYears = document.getElementById('stat-years');
      const statVenues = document.getElementById('stat-venues');

      // Calculate initial metrics
      function initStats() {
        statTotalPubs.textContent = publications.length;
        statAuthors.textContent = Object.keys(authorsDict).length;

        const validYears = publications
          .map(p => parseInt(p.year, 10))
          .filter(y => !isNaN(y));
        if (validYears.length > 0) {
          const minYear = Math.min(...validYears);
          const maxYear = Math.max(...validYears);
          statYears.textContent = `${minYear} - ${maxYear}`;
        } else {
          statYears.textContent = 'N/A';
        }

        const venues = new Set(publications.map(p => p.journal).filter(Boolean));
        statVenues.textContent = venues.size;
        totalCountEl.textContent = publications.length;
      }

      // Populate filter dropdowns
      function populateAuthorDropdown() {
        const authorCounts = {};
        for (const name of Object.values(authorsDict)) {
          authorCounts[name] = 0;
        }
        for (const pub of publications) {
          for (const a of (pub.authors || [])) {
            if (authorCounts[a] !== undefined) {
              authorCounts[a]++;
            }
          }
        }

        for (const [name, count] of Object.entries(authorCounts)) {
          const opt = document.createElement('option');
          opt.value = name;
          opt.textContent = `${name} (${count})`;
          authorFilter.appendChild(opt);
        }
      }

      function updateYearDropdown(selectedAuthor = null) {
        if (!selectedAuthor) {
          selectedAuthor = authorFilter.value;
        }

        const relevantPubs = (selectedAuthor && selectedAuthor !== 'all')
          ? publications.filter(p => p.authors && p.authors.includes(selectedAuthor))
          : publications;

        const yearCounts = {};
        for (const pub of relevantPubs) {
          const yr = pub.year || 'Unknown';
          yearCounts[yr] = (yearCounts[yr] || 0) + 1;
        }

        const sortedYears = Object.keys(yearCounts).sort((a, b) => {
          if (a === 'Unknown') return 1;
          if (b === 'Unknown') return -1;
          return parseInt(b, 10) - parseInt(a, 10);
        });

        const previousYear = yearFilter.value;
        yearFilter.innerHTML = '';

        const allOpt = document.createElement('option');
        allOpt.value = 'all';
        allOpt.textContent = selectedAuthor !== 'all' ? `All Years (${relevantPubs.length})` : 'All Years';
        yearFilter.appendChild(allOpt);

        for (const yr of sortedYears) {
          const opt = document.createElement('option');
          opt.value = yr;
          opt.textContent = `${yr} (${yearCounts[yr]})`;
          yearFilter.appendChild(opt);
        }

        if (previousYear && previousYear !== 'all' && yearCounts[previousYear] !== undefined) {
          yearFilter.value = previousYear;
        } else {
          yearFilter.value = 'all';
        }
      }

      let pendingYear = null;
      function readUrlParams() {
        const params = new URLSearchParams(window.location.search);
        if (params.has('author')) authorFilter.value = params.get('author');
        if (params.has('year')) pendingYear = params.get('year');
        if (params.has('search')) searchInput.value = params.get('search');
        if (params.has('sort')) sortSelect.value = params.get('sort');
        if (params.has('group')) groupYearToggle.checked = params.get('group') !== 'false';
      }

      // Sync query params
      function syncUrlParams() {
        const params = new URLSearchParams();
        if (authorFilter.value !== 'all') params.set('author', authorFilter.value);
        if (yearFilter.value !== 'all') params.set('year', yearFilter.value);
        if (searchInput.value.trim() !== '') params.set('search', searchInput.value.trim());
        if (sortSelect.value !== 'year-desc') params.set('sort', sortSelect.value);
        if (!groupYearToggle.checked) params.set('group', 'false');

        const queryString = params.toString();
        const newUrl = window.location.pathname + (queryString ? '?' + queryString : '');
        window.history.replaceState({}, '', newUrl);
      }

      // Escape HTML helper
      function escapeHtml(str) {
        if (!str) return '';
        return String(str)
          .replace(/&/g, '&amp;')
          .replace(/</g, '&lt;')
          .replace(/>/g, '&gt;')
          .replace(/"/g, '&quot;')
          .replace(/'/g, '&#39;');
      }

      // Toast feedback
      let toastTimer = null;
      function showToast(msg) {
        toastEl.textContent = msg;
        toastEl.classList.add('show');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => {
          toastEl.classList.remove('show');
        }, 2200);
      }

      // Copy text to clipboard
      function copyToClipboard(text, successMsg) {
        navigator.clipboard.writeText(text).then(() => {
          showToast(successMsg);
        }).catch(() => {
          const ta = document.createElement('textarea');
          ta.value = text;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          showToast(successMsg);
        });
      }

      // Build BibTeX
      function generateBibtex(pub) {
        const firstAuthor = (pub.authors && pub.authors[0]) ? pub.authors[0].split(' ').pop().toLowerCase() : 'pub';
        const year = (pub.year && pub.year !== 'Unknown') ? pub.year : 'date';
        const keyDoi = pub.doi ? pub.doi.replace(/[^a-zA-Z0-9]/g, '') : '';
        const key = `${firstAuthor}${year}${keyDoi.slice(-4)}`;
        let bib = `@article{${key},\n`;
        bib += `  title = {${pub.title.replace(/{/g, '\\{').replace(/}/g, '\\}')}},\n`;
        if (pub.authors && pub.authors.length > 0) {
          bib += `  author = {${pub.authors.join(' and ')}},\n`;
        }
        if (pub.journal) {
          bib += `  journal = {${pub.journal}},\n`;
        }
        if (pub.year && pub.year !== 'Unknown') {
          bib += `  year = {${pub.year}},\n`;
        }
        if (pub.doi) {
          bib += `  doi = {${pub.doi}},\n`;
          bib += `  url = {https://doi.org/${pub.doi}},\n`;
        } else if (pub.url) {
          bib += `  url = {${pub.url}},\n`;
        }
        bib += `}`;
        return bib;
      }

      // Render publication card element
      function createPubCard(pub) {
        const card = document.createElement('article');
        card.className = 'pub-card';

        // Title
        const titleEl = document.createElement('h3');
        titleEl.className = 'pub-title';
        const cleanDoi = pub.doi ? pub.doi.replace(/^https?:\/\/(dx\.)?doi\.org\//i, '').replace(/^doi:\s*/i, '').trim() : '';
        const pubUrl = cleanDoi ? `https://doi.org/${cleanDoi}` : (pub.url || null);
        if (pubUrl) {
          const a = document.createElement('a');
          a.href = pubUrl;
          a.target = '_blank';
          a.rel = 'noopener noreferrer';
          a.textContent = pub.title;
          titleEl.appendChild(a);
        } else {
          titleEl.textContent = pub.title;
        }
        card.appendChild(titleEl);

        // Meta info (Authors, Venue, Badges)
        const metaEl = document.createElement('div');
        metaEl.className = 'pub-meta';

        // Authors
        if (pub.authors && pub.authors.length > 0) {
          const authorsWrapper = document.createElement('div');
          authorsWrapper.className = 'pub-authors';
          for (const author of pub.authors) {
            const orcid = nameToOrcid[author];
            const chip = document.createElement('span');
            chip.className = 'author-chip';
            chip.title = `Filter by ${author}`;
            chip.innerHTML = `
              <svg class="orcid-icon" viewBox="0 0 256 256">
                <path d="M256 128c0 70.7-57.3 128-128 128S0 198.7 0 128 57.3 0 128 0s128 57.3 128 128z"/>
                <path fill="#fff" d="M86.3 186.2H70.9V79.1h15.4v107.1zM78.6 62.2c-5.5 0-10-4.5-10-10s4.5-10 10-10 10 4.5 10 10-4.5 10-10 10zm108.8 77.8c0 27.8-19.8 46.2-49.9 46.2H108V79.1h31.6c28.2 0 47.8 19.5 47.8 46.5v14.4zm-16.1-.7c0-20.7-13.8-32.9-33.1-32.9h-14.7v72.8h14.7c20 0 33.1-13.1 33.1-34.1v-5.8z"/>
              </svg>
              <span>${escapeHtml(author)}</span>
            `;
            chip.addEventListener('click', (e) => {
              e.preventDefault();
              authorFilter.value = author;
              updateYearDropdown(author);
              render();
            });
            authorsWrapper.appendChild(chip);
          }
          metaEl.appendChild(authorsWrapper);
        }

        // Journal
        if (pub.journal) {
          const venueEl = document.createElement('span');
          venueEl.className = 'pub-venue';
          venueEl.textContent = pub.journal;
          metaEl.appendChild(venueEl);
        }

        // Year Badge
        const yearBadge = document.createElement('span');
        yearBadge.className = 'badge badge-year';
        yearBadge.textContent = pub.year;
        metaEl.appendChild(yearBadge);

        // Type Badge
        if (pub.type && pub.type !== 'other') {
          const typeBadge = document.createElement('span');
          typeBadge.className = 'badge';
          const formattedType = pub.type.replace(/-/g, ' ').replace(/\b\w/g, function(l) { return l.toUpperCase(); });
          typeBadge.textContent = formattedType;
          metaEl.appendChild(typeBadge);
        }

        // DOI Badge
        if (pub.doi) {
          const doiBadge = document.createElement('a');
          doiBadge.className = 'badge badge-doi';
          doiBadge.href = `https://doi.org/${pub.doi}`;
          doiBadge.target = '_blank';
          doiBadge.rel = 'noopener noreferrer';
          doiBadge.textContent = `DOI: ${pub.doi}`;
          metaEl.appendChild(doiBadge);
        }

        card.appendChild(metaEl);

        // Action Buttons (Copy Citation, Copy DOI, BibTeX)
        const actionsEl = document.createElement('div');
        actionsEl.className = 'pub-actions';

        // Copy Citation
        const btnCite = document.createElement('button');
        btnCite.className = 'btn-action';
        btnCite.innerHTML = '<span>📋</span> Copy Citation';
        btnCite.addEventListener('click', () => {
          const authorStr = (pub.authors && pub.authors.length > 0) ? pub.authors.join(', ') : 'Group Members';
          const venueStr = pub.journal ? ` *${pub.journal}*` : '';
          const doiStr = pub.doi ? ` https://doi.org/${pub.doi}` : (pub.url ? ` ${pub.url}` : '');
          const citation = `${authorStr} (${pub.year}). ${pub.title}.${venueStr}.${doiStr}`;
          copyToClipboard(citation, 'Citation copied to clipboard!');
        });
        actionsEl.appendChild(btnCite);

        // Copy DOI
        if (pub.doi) {
          const btnDoi = document.createElement('button');
          btnDoi.className = 'btn-action';
          btnDoi.innerHTML = '<span>🔗</span> Copy DOI';
          btnDoi.addEventListener('click', () => {
            copyToClipboard(`https://doi.org/${pub.doi}`, 'DOI link copied to clipboard!');
          });
          actionsEl.appendChild(btnDoi);
        }

        // BibTeX
        const btnBib = document.createElement('button');
        btnBib.className = 'btn-action';
        btnBib.innerHTML = '<span>📑</span> BibTeX';
        btnBib.addEventListener('click', () => {
          const bibtex = generateBibtex(pub);
          copyToClipboard(bibtex, 'BibTeX entry copied to clipboard!');
        });
        actionsEl.appendChild(btnBib);

        card.appendChild(actionsEl);
        return card;
      }

      // Filter and Sort publications
      function getFilteredPubs() {
        const selectedAuthor = authorFilter.value;
        const selectedYear = yearFilter.value;
        const searchQuery = searchInput.value.trim().toLowerCase();
        const sortMode = sortSelect.value;

        let filtered = publications.filter(pub => {
          // Author filter
          if (selectedAuthor !== 'all') {
            if (!pub.authors || !pub.authors.includes(selectedAuthor)) {
              return false;
            }
          }

          // Year filter
          if (selectedYear !== 'all') {
            if (pub.year !== selectedYear) {
              return false;
            }
          }

          // Search filter
          if (searchQuery !== '') {
            const titleMatch = (pub.title || '').toLowerCase().includes(searchQuery);
            const journalMatch = (pub.journal || '').toLowerCase().includes(searchQuery);
            const doiMatch = (pub.doi || '').toLowerCase().includes(searchQuery);
            const authorMatch = (pub.authors || []).some(a => a.toLowerCase().includes(searchQuery));
            if (!titleMatch && !journalMatch && !doiMatch && !authorMatch) {
              return false;
            }
          }

          return true;
        });

        // Sorting
        filtered.sort((a, b) => {
          if (sortMode === 'year-desc') {
            const yA = parseInt(a.year, 10) || 0;
            const yB = parseInt(b.year, 10) || 0;
            if (yB !== yA) return yB - yA;
            return (a.title || '').localeCompare(b.title || '');
          } else if (sortMode === 'year-asc') {
            const yA = parseInt(a.year, 10) || 0;
            const yB = parseInt(b.year, 10) || 0;
            if (yA !== yB) return yA - yB;
            return (a.title || '').localeCompare(b.title || '');
          } else if (sortMode === 'title-asc') {
            return (a.title || '').localeCompare(b.title || '');
          }
          return 0;
        });

        return filtered;
      }

      // Update active pills
      function updateActivePills() {
        activePillsEl.innerHTML = '';
        const pills = [];

        if (authorFilter.value !== 'all') {
          pills.push({
            label: `Author: ${authorFilter.value}`,
            clear: () => { authorFilter.value = 'all'; updateYearDropdown('all'); render(); }
          });
        }
        if (yearFilter.value !== 'all') {
          pills.push({
            label: `Year: ${yearFilter.value}`,
            clear: () => { yearFilter.value = 'all'; render(); }
          });
        }
        if (searchInput.value.trim() !== '') {
          pills.push({
            label: `Search: "${searchInput.value.trim()}"`,
            clear: () => { searchInput.value = ''; render(); }
          });
        }

        for (const p of pills) {
          const pill = document.createElement('span');
          pill.className = 'pill';
          pill.textContent = p.label + ' ';
          const btn = document.createElement('button');
          btn.className = 'pill-close';
          btn.innerHTML = '&times;';
          btn.title = 'Remove filter';
          btn.addEventListener('click', p.clear);
          pill.appendChild(btn);
          activePillsEl.appendChild(pill);
        }
      }

      // Render main publications list
      function render() {
        syncUrlParams();
        updateActivePills();

        const filtered = getFilteredPubs();
        visibleCountEl.textContent = filtered.length;
        pubsContainer.innerHTML = '';

        if (filtered.length === 0) {
          const empty = document.createElement('div');
          empty.className = 'empty-state';
          empty.innerHTML = `
            <h3>No publications found</h3>
            <p>Try adjusting your search criteria or resetting filters.</p>
            <button class="btn btn-primary" style="margin-top: 1rem;" onclick="document.getElementById('reset-filters-btn').click()">
              Reset All Filters
            </button>
          `;
          pubsContainer.appendChild(empty);
          return;
        }

        const groupByYear = groupYearToggle.checked;

        if (groupByYear) {
          // Group publications by year
          const groups = {};
          const groupOrder = [];

          for (const pub of filtered) {
            const yr = pub.year || 'Unknown';
            if (!groups[yr]) {
              groups[yr] = [];
              groupOrder.push(yr);
            }
            groups[yr].push(pub);
          }

          for (const yr of groupOrder) {
            const section = document.createElement('section');
            section.className = 'year-section';

            const header = document.createElement('h2');
            header.className = 'year-header';
            header.innerHTML = `
              <span>${yr}</span>
              <span class="year-count-badge">${groups[yr].length} publication${groups[yr].length === 1 ? '' : 's'}</span>
            `;
            section.appendChild(header);

            const list = document.createElement('div');
            list.className = 'pubs-list';
            for (const pub of groups[yr]) {
              list.appendChild(createPubCard(pub));
            }
            section.appendChild(list);
            pubsContainer.appendChild(section);
          }
        } else {
          // Flat list
          const list = document.createElement('div');
          list.className = 'pubs-list';
          for (const pub of filtered) {
            list.appendChild(createPubCard(pub));
          }
          pubsContainer.appendChild(list);
        }
      }

      // Theme toggle
      const themeBtn = document.getElementById('theme-btn');
      const themeIcon = document.getElementById('theme-icon');
      const themeText = document.getElementById('theme-text');

      function initTheme() {
        const saved = localStorage.getItem('pubs-theme');
        const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        const isDark = saved ? saved === 'dark' : prefersDark;
        applyTheme(isDark);
      }

      function applyTheme(isDark) {
        document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
        themeIcon.textContent = isDark ? '☀️' : '🌙';
        themeText.textContent = isDark ? 'Light' : 'Dark';
        localStorage.setItem('pubs-theme', isDark ? 'dark' : 'light');
      }

      themeBtn.addEventListener('click', () => {
        const currentTheme = document.documentElement.getAttribute('data-theme');
        applyTheme(currentTheme !== 'dark');
      });

      // Event listeners
      authorFilter.addEventListener('change', () => {
        updateYearDropdown(authorFilter.value);
        render();
      });
      yearFilter.addEventListener('change', render);
      searchInput.addEventListener('input', render);
      sortSelect.addEventListener('change', render);
      groupYearToggle.addEventListener('change', render);

      resetBtn.addEventListener('click', () => {
        authorFilter.value = 'all';
        updateYearDropdown('all');
        yearFilter.value = 'all';
        searchInput.value = '';
        sortSelect.value = 'year-desc';
        groupYearToggle.checked = true;
        render();
      });

      // Initial execution
      initStats();
      populateAuthorDropdown();
      readUrlParams();
      updateYearDropdown(authorFilter.value);
      if (pendingYear) {
        for (const opt of yearFilter.options) {
          if (opt.value === pendingYear) {
            yearFilter.value = pendingYear;
            break;
          }
        }
      }
      initTheme();
      render();
    })();
  </script>
</body>
</html>
"""
    if last_updated is None:
        now = datetime.now(timezone.utc)
        last_updated_iso = now.strftime("%Y-%m-%d")
        last_updated_text = f"{now.strftime('%B')} {now.day}, {now.year}"
    else:
        last_updated_iso = last_updated
        last_updated_text = last_updated

    pubs_json = json.dumps(publications, ensure_ascii=False)
    authors_json = json.dumps(orcid_dict, ensure_ascii=False)
    return (
        template.replace("__PUBLICATIONS_JSON__", pubs_json)
        .replace("__AUTHORS_JSON__", authors_json)
        .replace("__LAST_UPDATED_ISO__", last_updated_iso)
        .replace("__LAST_UPDATED_TEXT__", last_updated_text)
    )


def generate_html(
    publications: List[Dict[str, Any]],
    orcid_dict: Dict[str, str],
    output_path: str = "index.html",
    last_updated: Optional[str] = None,
):
    """Generate self-contained interactive publications webpage for GitHub Pages."""
    content = build_html_page(publications, orcid_dict, last_updated=last_updated)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Generated {output_path} with {len(publications)} publications.")


def main():
    parser = argparse.ArgumentParser(description="Fetch and aggregate publications from ORCID.")
    parser.add_argument(
        "orcids",
        nargs="*",
        default=None,
        help="ORCID IDs to fetch (format: 0000-0000-0000-000X). Overrides or filters CSV members.",
    )
    parser.add_argument(
        "--csv",
        "--authors-csv",
        dest="authors_csv",
        default=DEFAULT_CSV_PATH,
        help="Path to CSV file with orcid,name mapping (default: data/authors.csv)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="PUBLICATIONS.md",
        help="Output markdown file path (default: PUBLICATIONS.md)",
    )
    parser.add_argument(
        "--html",
        default="index.html",
        help="Output HTML webpage file path for GitHub Pages (default: index.html)",
    )
    parser.add_argument(
        "--json",
        default="publications.json",
        help="Output JSON file path (default: publications.json)",
    )
    parser.add_argument(
        "--cache-dir",
        default="data/cache",
        help="Directory to cache ORCID API responses (default: data/cache)",
    )
    parser.add_argument(
        "--no-html",
        action="store_true",
        help="Skip generating HTML webpage",
    )
    parser.add_argument(
        "--no-json",
        action="store_true",
        help="Skip exporting JSON",
    )
    args = parser.parse_args()

    # Determine orcid mapping
    if args.authors_csv and os.path.isfile(args.authors_csv):
        orcid_dict = load_orcids_from_csv(args.authors_csv)
    else:
        orcid_dict = ORCID_IDS

    if args.orcids:
        orcids_input = args.orcids
        orcid_dict = {o: orcid_dict.get(o, o) for o in args.orcids}
    else:
        orcids_input = orcid_dict

    pubs = aggregate_publications(orcids_input, cache_dir=args.cache_dir)

    if not pubs:
        print("Warning: No publications found or fetched.")

    # Write Markdown
    if args.output:
        generate_markdown(pubs, output_path=args.output)

    # Write JSON
    if not args.no_json and args.json:
        export_json(pubs, output_path=args.json)

    # Write HTML
    if not args.no_html and args.html:
        generate_html(pubs, orcid_dict=orcid_dict, output_path=args.html)


if __name__ == "__main__":
    main()

