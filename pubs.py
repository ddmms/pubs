import argparse
import json
import re
from typing import Any, Dict, List
import requests

# Map group members' ORCID IDs to their names (format: "0000-0000-0000-000X": "Full Name")
ORCID_IDS: Dict[str, str] = {
    "0000-0002-7013-6670": "Alin Marin Elena",
    "0000-0001-6068-6786": "Gilberto Teobaldi",
}
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


def select_best_summary(summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Select the preferred work summary in a group (display-index 0), or the first."""
    if not summaries:
        return {}
    for s in summaries:
        if str(s.get("display-index", "")).strip() == "0":
            return s
    return summaries[0]


def fetch_member_works(orcid_id: str) -> List[Dict[str, Any]]:
    """Retrieve all work summaries for an individual ORCID record."""
    url = f"{ORCID_API_BASE}/{orcid_id}/works"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
    except requests.RequestException as e:
        print(f"Warning: Network error fetching {orcid_id}: {e}")
        return []

    if resp.status_code != 200:
        print(f"Warning: Failed to fetch {orcid_id} (HTTP {resp.status_code})")
        return []

    data = resp.json()
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

        # URL fallback: DOI URL or explicit work URL or external-id URL
        work_url = f"https://doi.org/{doi}" if doi else extract_fallback_url(summary, group)

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


def aggregate_publications(orcids: List[str] | Dict[str, str]) -> List[Dict[str, Any]]:
    """Fetch and deduplicate publications across all researchers."""
    deduped: List[Dict[str, Any]] = []
    doi_map: Dict[str, Dict[str, Any]] = {}
    title_map: Dict[str, Dict[str, Any]] = {}

    for orcid in orcids:
        works = fetch_member_works(orcid)
        for work in works:
            doi = work.get("doi")
            norm_title = normalize_title(work["title"])

            existing = None
            if doi and doi in doi_map:
                existing = doi_map[doi]
            elif norm_title in title_map:
                existing = title_map[norm_title]

            if existing:
                # Merge richer metadata if available
                if not existing.get("doi") and doi:
                    existing["doi"] = doi
                    doi_map[doi] = existing
                if not existing.get("journal") and work.get("journal"):
                    existing["journal"] = work["journal"]
                if not existing.get("url") and work.get("url"):
                    existing["url"] = work["url"]
                if existing.get("year") == "Unknown" and work.get("year") != "Unknown":
                    existing["year"] = work["year"]
            else:
                deduped.append(work)
                if doi:
                    doi_map[doi] = work
                if norm_title:
                    title_map[norm_title] = work

    # Sort descending by year (Unknowns at the end), then alphabetically by title
    def sort_key(item):
        yr = item["year"]
        year_num = int(yr) if str(yr).isdigit() else 0
        return (-year_num, item.get("title", "").lower())

    return sorted(deduped, key=sort_key)


def generate_markdown(publications: List[Dict[str, Any]], output_path: str = "PUBLICATIONS.md"):
    """Write grouped publications by year to Markdown."""
    lines = ["# Group Publications", "", "*Auto-generated via ORCID Public API*"]

    current_year = None
    for pub in publications:
        year = pub["year"]
        if year != current_year:
            current_year = year
            lines.extend(["", f"## {current_year}", ""])

        title_str = f"**[{pub['title']}]({pub['url']})**" if pub["url"] else f"**{pub['title']}**"
        venue_str = f" *{pub['journal']}*." if pub["journal"] else ""
        doi_str = f" [DOI: {pub['doi']}](https://doi.org/{pub['doi']})" if pub["doi"] else ""

        lines.append(f"- {title_str}{venue_str}{doi_str}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")
    print(f"Generated {output_path} with {len(publications)} publications.")


def main():
    parser = argparse.ArgumentParser(description="Fetch and aggregate publications from ORCID.")
    parser.add_argument(
        "orcids",
        nargs="*",
        default=list(ORCID_IDS.keys()),
        help="ORCID IDs to fetch (format: 0000-0000-0000-000X). Defaults to configured ORCID_IDS.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="PUBLICATIONS.md",
        help="Output markdown file path (default: PUBLICATIONS.md)",
    )
    args = parser.parse_args()

    pubs = aggregate_publications(args.orcids)
    generate_markdown(pubs, output_path=args.output)


if __name__ == "__main__":
    main()
