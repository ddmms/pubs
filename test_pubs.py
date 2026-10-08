import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from pubs import (
    ORCID_IDS,
    ORCIDS_IDS,
    aggregate_publications,
    extract_doi,
    extract_fallback_url,
    fetch_member_works,
    generate_markdown,
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
        # Explicit url in summary
        summary = {"url": {"value": "https://example.com/paper"}}
        self.assertEqual(extract_fallback_url(summary), "https://example.com/paper")

        # external-id-url
        summary_ext_url = {
            "url": None,
            "external-ids": {
                "external-id": [
                    {"external-id-type": "wosuid", "external-id-url": {"value": "https://wos.com/123"}}
                ]
            },
        }
        self.assertEqual(extract_fallback_url(summary_ext_url), "https://wos.com/123")

        # uri external-id type
        summary_uri = {
            "url": None,
            "external-ids": {
                "external-id": [
                    {"external-id-type": "uri", "external-id-value": "https://arxiv.org/abs/2603.05442"}
                ]
            },
        }
        self.assertEqual(extract_fallback_url(summary_uri), "https://arxiv.org/abs/2603.05442")

        # None / missing
        self.assertIsNone(extract_fallback_url({"url": None, "external-ids": None}))

    def test_select_best_summary(self):
        self.assertEqual(select_best_summary([]), {})
        s1 = {"display-index": "1", "title": "Version 1"}
        s2 = {"display-index": "0", "title": "Preferred Version"}
        s3 = {"display-index": "1", "title": "Version 2"}
        self.assertEqual(select_best_summary([s1, s2, s3]), s2)
        # Fallback to first if none has 0
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
        # Co-author has same paper but with missing journal and different year
        mock_works_2 = [
            {
                "title": "Quantum Simulations",
                "year": "2025",
                "journal": None,
                "doi": "10.1063/123",
                "url": None,
                "type": "journal-article",
            },
            # Second author has published version of preprint with journal and DOI
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
            # Verify Old Preprint was enriched with DOI and Journal
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
            works = fetch_member_works("0000-0000-0000-0000")
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
            self.assertIn("- **[Preprint Paper](https://arxiv.org/123)**", content)
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
            res = aggregate_publications({"0000-0002-7013-6670": "Alin Marin Elena"})
            mock_fetch.assert_called_once_with("0000-0002-7013-6670")
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["title"], "Quantum Simulations")


if __name__ == "__main__":
    unittest.main()

