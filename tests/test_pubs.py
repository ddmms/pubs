import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure src/ is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pubs import (
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


class TestPubs(unittest.TestCase):
    def test_extract_doi_valid(self):
        ext_ids = {
            "external-id": [
                {"external-id-type": "wosuid", "external-id-value": "WOS:123"},
                {"external-id-type": "doi", "external-id-value": "10.1063/5.0297006"},
            ]
        }
        self.assertEqual(extract_doi(ext_ids), "10.1063/5.0297006")

    def test_extract_doi_with_prefix_and_case(self):
        ext_ids = {
            "external-id": [
                {"external-id-type": "DOI", "external-id-value": "https://doi.org/10.1038/S41524-025-01611-8"}
            ]
        }
        self.assertEqual(extract_doi(ext_ids), "10.1038/s41524-025-01611-8")

        ext_ids_dx = {
            "external-id": [
                {"external-id-type": "doi", "external-id-value": "http://dx.doi.org/10.1039/D5CP01882J"}
            ]
        }
        self.assertEqual(extract_doi(ext_ids_dx), "10.1039/d5cp01882j")

        ext_ids_prefix = {
            "external-id": [
                {"external-id-type": "doi", "external-id-value": "doi: 10.1103/PhysRevB.109.094205"}
            ]
        }
        self.assertEqual(extract_doi(ext_ids_prefix), "10.1103/physrevb.109.094205")

    def test_extract_doi_malformed_and_none(self):
        self.assertIsNone(extract_doi(None))
        self.assertIsNone(extract_doi({}))
        self.assertIsNone(extract_doi({"external-id": None}))
        self.assertIsNone(extract_doi({"external-id": [None, {}]}))
        self.assertIsNone(extract_doi({"external-id": [{"external-id-type": None, "external-id-value": None}]}))
        self.assertIsNone(extract_doi({"external-id": [{"external-id-type": "isbn", "external-id-value": "12345"}]}))

    def test_extract_fallback_url(self):
        summary = {"url": {"value": "https://example.com/paper"}}
        self.assertEqual(extract_fallback_url(summary), "https://example.com/paper")

        summary_ext_url = {
            "url": None,
            "external-ids": {
                "external-id": [
                    {"external-id-type": "wosuid", "external-id-url": {"value": "https://wos.com/123"}}
                ]
            },
        }
        self.assertEqual(extract_fallback_url(summary_ext_url), "https://wos.com/123")

        summary_uri = {
            "url": None,
            "external-ids": {
                "external-id": [
                    {"external-id-type": "uri", "external-id-value": "https://arxiv.org/abs/2603.05442"}
                ]
            },
        }
        self.assertEqual(extract_fallback_url(summary_uri), "https://arxiv.org/abs/2603.05442")

        self.assertIsNone(extract_fallback_url({"url": None, "external-ids": None}))

    def test_assemble_doi_url(self):
        self.assertEqual(assemble_doi_url("10.1063/5.0297006"), "https://doi.org/10.1063/5.0297006")
        self.assertEqual(assemble_doi_url("https://doi.org/10.1063/5.0297006"), "https://doi.org/10.1063/5.0297006")
        self.assertEqual(assemble_doi_url("http://dx.doi.org/10.1063/5.0297006"), "https://doi.org/10.1063/5.0297006")
        self.assertEqual(assemble_doi_url("doi: 10.1063/5.0297006"), "https://doi.org/10.1063/5.0297006")
        self.assertIsNone(assemble_doi_url(None))
        self.assertIsNone(assemble_doi_url(""))
        self.assertIsNone(assemble_doi_url("   "))

    def test_get_publication_url(self):
        # Prefers assembled DOI URL
        pub_doi = {"doi": "10.1039/d5dd00565e", "url": "https://other.com/paper"}
        self.assertEqual(get_publication_url(pub_doi), "https://doi.org/10.1039/d5dd00565e")

        # Uses DOI when url is None
        pub_doi_only = {"doi": "10.1039/d5dd00565e", "url": None}
        self.assertEqual(get_publication_url(pub_doi_only), "https://doi.org/10.1039/d5dd00565e")

        # Fallback to url if no DOI
        pub_fallback = {"doi": None, "url": "https://arxiv.org/abs/2602.19411"}
        self.assertEqual(get_publication_url(pub_fallback), "https://arxiv.org/abs/2602.19411")

        # Neither DOI nor url
        pub_none = {"doi": None, "url": None}
        self.assertIsNone(get_publication_url(pub_none))

    def test_select_best_summary(self):
        self.assertEqual(select_best_summary([]), {})
        s1 = {"display-index": "1", "title": "Version 1"}
        s2 = {"display-index": "0", "title": "Preferred Version"}
        s3 = {"display-index": "1", "title": "Version 2"}
        self.assertEqual(select_best_summary([s1, s2, s3]), s2)
        self.assertEqual(select_best_summary([s1, s3]), s1)

    def test_normalize_title(self):
        self.assertEqual(normalize_title("DL_POLY 5: Calculation!"), "dl_poly5calculation")

    def test_aggregate_publications_deduplication(self):
        mock_works_1 = [
            {
                "title": "Quantum Simulations",
                "year": "2025",
                "journal": "JCP",
                "doi": "10.1063/123",
                "url": "https://doi.org/10.1063/123",
                "type": "journal-article",
            },
            {
                "title": "Old Preprint",
                "year": "2024",
                "journal": None,
                "doi": None,
                "url": "https://arxiv.org/111",
                "type": "preprint",
            },
        ]
        mock_works_2 = [
            {
                "title": "Quantum Simulations",
                "year": "2025",
                "journal": None,
                "doi": "10.1063/123",
                "url": None,
                "type": "journal-article",
            },
            {
                "title": "Old Preprint",
                "year": "2024",
                "journal": "Nature",
                "doi": "10.1038/456",
                "url": "https://doi.org/10.1038/456",
                "type": "journal-article",
            },
        ]

        with patch("pubs.fetch_member_works", side_effect=[mock_works_1, mock_works_2]):
            result = aggregate_publications(["orcid-1", "orcid-2"])
            self.assertEqual(len(result), 2)
            preprint_res = next(r for r in result if r["title"] == "Old Preprint")
            self.assertEqual(preprint_res["doi"], "10.1038/456")
            self.assertEqual(preprint_res["journal"], "Nature")

    def test_fetch_member_works_handles_nulls(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "group": [
                {
                    "work-summary": [
                        {
                            "title": {"title": {"value": "Valid Title"}},
                            "publication-date": None,
                            "journal-title": None,
                            "url": None,
                            "external-ids": None,
                            "type": None,
                        }
                    ]
                }
            ]
        }
        with patch("requests.get", return_value=mock_resp):
            works = fetch_member_works("0000-0000-0000-0000", cache_dir=None)
            self.assertEqual(len(works), 1)
            self.assertEqual(works[0]["title"], "Valid Title")
            self.assertEqual(works[0]["year"], "Unknown")
            self.assertIsNone(works[0]["doi"])
            self.assertIsNone(works[0]["url"])

    def test_generate_markdown(self):
        publications = [
            {
                "title": "New Paper",
                "year": "2026",
                "journal": "Science",
                "doi": "10.1126/science.1",
                "url": "https://doi.org/10.1126/science.1",
            },
            {
                "title": "DOI Assembled Paper",
                "year": "2026",
                "journal": "Nature",
                "doi": "10.1038/s41586-026-0001",
                "url": None,
            },
            {
                "title": "Preprint Paper",
                "year": "2026",
                "journal": None,
                "doi": None,
                "url": "https://arxiv.org/123",
            },
        ]
        with tempfile.NamedTemporaryFile("r+", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            generate_markdown(publications, output_path=tmp_path)
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn("# Group Publications", content)
            self.assertIn("## 2026", content)
            self.assertIn("- **[New Paper](https://doi.org/10.1126/science.1)** *Science*. [DOI: 10.1126/science.1](https://doi.org/10.1126/science.1)", content)
            self.assertIn("- **[DOI Assembled Paper](https://doi.org/10.1038/s41586-026-0001)** *Nature*. [DOI: 10.1038/s41586-026-0001](https://doi.org/10.1038/s41586-026-0001)", content)
            self.assertIn("- **[Preprint Paper](https://arxiv.org/123)**", content)
            self.assertIn("Last updated:", content)
            self.assertIn("Copyright (c) 2026, Alin M. Elena and contributors", content)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_orcid_ids_dict_structure(self):
        import re

        self.assertIsInstance(ORCID_IDS, dict)
        self.assertIsInstance(ORCIDS_IDS, dict)
        self.assertEqual(ORCID_IDS, ORCIDS_IDS)
        self.assertGreater(len(ORCID_IDS), 0)

        orcid_pattern = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
        for orcid, name in ORCID_IDS.items():
            self.assertRegex(orcid, orcid_pattern)
            self.assertIsInstance(name, str)
            self.assertTrue(len(name.strip()) > 0)

        self.assertIn("0000-0002-7013-6670", ORCID_IDS)
        self.assertEqual(ORCID_IDS["0000-0002-7013-6670"], "Alin Marin Elena")

    def test_load_orcids_from_csv(self):
        csv_content = """# Group members
orcid,name
0000-0002-7013-6670,Alin Marin Elena
https://orcid.org/0000-0001-6068-6786,Gilberto Teobaldi
"""
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as tmp:
            tmp.write(csv_content)
            tmp_path = tmp.name

        try:
            loaded = load_orcids_from_csv(tmp_path)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded["0000-0002-7013-6670"], "Alin Marin Elena")
            self.assertEqual(loaded["0000-0001-6068-6786"], "Gilberto Teobaldi")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_load_orcids_from_csv_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            load_orcids_from_csv("non_existent_file.csv")

    def test_aggregate_publications_accepts_dict(self):
        mock_works = [
            {
                "title": "Quantum Simulations",
                "year": "2025",
                "journal": "JCP",
                "doi": "10.1063/123",
                "url": "https://doi.org/10.1063/123",
                "type": "journal-article",
            }
        ]
        with patch("pubs.fetch_member_works", return_value=mock_works) as mock_fetch:
            res = aggregate_publications({"0000-0002-7013-6670": "Alin Marin Elena"}, cache_dir="data/cache")
            mock_fetch.assert_called_once_with("0000-0002-7013-6670", cache_dir="data/cache")
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["title"], "Quantum Simulations")
            self.assertEqual(res[0]["authors"], ["Alin Marin Elena"])
            self.assertEqual(res[0]["orcids"], ["0000-0002-7013-6670"])

    def test_aggregate_publications_coauthor_merging(self):
        work_a = [
            {
                "title": "Shared Paper",
                "year": "2026",
                "journal": "Nature",
                "doi": "10.1038/s1",
                "url": "https://doi.org/10.1038/s1",
                "type": "journal-article",
            }
        ]
        work_b = [
            {
                "title": "Shared Paper",
                "year": "2026",
                "journal": "Nature",
                "doi": "10.1038/s1",
                "url": "https://doi.org/10.1038/s1",
                "type": "journal-article",
            }
        ]
        with patch("pubs.fetch_member_works", side_effect=[work_a, work_b]):
            res = aggregate_publications({
                "0000-0002-7013-6670": "Alin Marin Elena",
                "0000-0001-6068-6786": "Gilberto Teobaldi",
            })
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["authors"], ["Alin Marin Elena", "Gilberto Teobaldi"])
            self.assertEqual(res[0]["orcids"], ["0000-0002-7013-6670", "0000-0001-6068-6786"])

    def test_build_and_generate_html(self):
        pubs = [
            {
                "title": "Interactive Simulations Webpage",
                "year": "2026",
                "journal": "J. Comput. Phys.",
                "doi": "10.1016/j.jcp.2026.01",
                "url": "https://doi.org/10.1016/j.jcp.2026.01",
                "type": "journal-article",
                "authors": ["Alin Marin Elena"],
                "orcids": ["0000-0002-7013-6670"],
            }
        ]
        author_dict = {
            "0000-0002-7013-6670": "Alin Marin Elena",
            "0000-0001-6068-6786": "Gilberto Teobaldi",
        }
        html_str = build_html_page(pubs, author_dict)
        self.assertIn("<!DOCTYPE html>", html_str)
        self.assertIn("Filter by Author", html_str)
        self.assertIn("Filter by Year", html_str)
        self.assertIn("Alin Marin Elena", html_str)
        self.assertIn("Gilberto Teobaldi", html_str)
        self.assertIn("Interactive Simulations Webpage", html_str)
        self.assertIn("updateYearDropdown", html_str)
        self.assertIn("populateAuthorDropdown", html_str)
        self.assertIn("Alin M. Elena and contributors", html_str)
        self.assertIn("Last updated:", html_str)

        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".html") as tmp:
            tmp_path = tmp.name

        try:
            generate_html(pubs, author_dict, output_path=tmp_path)
            self.assertTrue(os.path.exists(tmp_path))
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Interactive Simulations Webpage", content)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_export_json(self):
        pubs = [
            {
                "title": "Data Export Test",
                "year": "2026",
                "journal": None,
                "doi": None,
                "url": None,
                "type": "other",
                "authors": ["Gilberto Teobaldi"],
                "orcids": ["0000-0001-6068-6786"],
            }
        ]
        with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".json") as tmp:
            tmp_path = tmp.name

        try:
            export_json(pubs, output_path=tmp_path)
            self.assertTrue(os.path.exists(tmp_path))
            with open(tmp_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["title"], "Data Export Test")
            self.assertEqual(loaded[0]["authors"], ["Gilberto Teobaldi"])
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()

