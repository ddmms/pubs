# Research Publications Portal

[![Deploy Publications to GitHub Pages](https://github.com/ddmms/pubs/actions/workflows/deploy.yml/badge.svg)](https://github.com/ddmms/pubs/actions/workflows/deploy.yml)
[![License: BSD-3-Clause](https://img.shields.io/badge/License-BSD--3--Clause-blue.svg)](LICENSE)

*Data Driven Materials and Molecular Science*

Automatically aggregates and presents research publications for group members using the [ORCID Public API](https://pub.orcid.org). The portal is deployed directly to GitHub Pages as an interactive single-page application.

*Last updated: 2026-10-08*

---

## Features

- **Automated aggregation**: Fetches and deduplicates member publications from ORCID records.
- **Interactive UI**:
  - Filter publications dynamically by author and by year.
  - Live full-text search across titles, journals, DOIs, and author names.
  - Sort chronologically (newest first or oldest first).
  - Direct links to DOIs, publication URLs, and one-click citation copying.
  - Responsive design with toggleable light and dark themes.
- **Multiple formats**: Exports to interactive HTML (`index.html`), Markdown (`PUBLICATIONS.md`), and JSON feed (`publications.json`).
- **Scheduled updates**: Weekly synchronization with ORCID via GitHub Actions.

---

## Project Structure

```text
.
├── .github/workflows/deploy.yml  # Automated sync and GitHub Pages deployment
├── data/
│   ├── authors.csv               # Configured ORCID IDs and author names
│   └── cache/                    # Cached ORCID API responses
├── src/
│   ├── __init__.py
│   └── pubs.py                   # Core aggregation and site generation logic
├── tests/
│   ├── __init__.py
│   └── test_pubs.py              # Test suite
├── index.html                    # Generated single-page interactive portal
├── PUBLICATIONS.md               # Generated Markdown bibliography
├── publications.json             # Generated JSON publications feed
├── pubs.py                       # CLI entrypoint
├── LICENSE                       # BSD 3-Clause license text
└── README.md                     # Project documentation
```

---

## Usage

### Managing Authors

Author mappings are configured in [`data/authors.csv`](data/authors.csv):

```csv
orcid,name
0000-0002-7013-6670,Alin Marin Elena
0009-0005-2015-9478,Elliott Kasoar
0000-0001-7374-9352,Junwen Yin
```

### Running Locally

To fetch publications and regenerate the portal files:

```bash
python pubs.py
```

Command-line options:
- `--csv path/to/authors.csv`: Path to CSV file with ORCID/name mapping (default: `data/authors.csv`)
- `-o PUBLICATIONS.md`: Output path for Markdown file (default: `PUBLICATIONS.md`)
- `--html index.html`: Output path for HTML webpage (default: `index.html`)
- `--json publications.json`: Output path for JSON export (default: `publications.json`)
- `--cache-dir data/cache`: Directory to cache ORCID API responses (default: `data/cache`)

### Running Tests

Execute the unit test suite:

```bash
python -m unittest discover tests
```

---

## Copyright & License

Copyright (c) 2026, Alin M. Elena and contributors.

This project is licensed under the terms of the BSD 3-Clause License. See the [LICENSE](LICENSE) file for the full license text.

