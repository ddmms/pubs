"""Pubs package.

Copyright (c) 2026, Alin M. Elena and contributors
Distributed under the terms of the BSD 3-Clause License.
"""
from .pubs import (
    ORCID_IDS,
    ORCIDS_IDS,
    aggregate_publications,
    assemble_doi_url,
    build_html_page,
    export_json,
    extract_doi,
    extract_fallback_url,
    fetch_member_works,
    generate_html,
    generate_markdown,
    get_publication_url,
    load_orcids_from_csv,
    normalize_title,
    select_best_summary,
)

__all__ = [
    "ORCID_IDS",
    "ORCIDS_IDS",
    "aggregate_publications",
    "assemble_doi_url",
    "build_html_page",
    "export_json",
    "extract_doi",
    "extract_fallback_url",
    "fetch_member_works",
    "generate_html",
    "generate_markdown",
    "get_publication_url",
    "load_orcids_from_csv",
    "normalize_title",
    "select_best_summary",
]

