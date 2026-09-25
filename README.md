# Automated Publication Tracking of Rejected Manuscripts

This repository contains `manuscript_tracker.py`, the automated tracking component of a hybrid automated–manual pipeline developed to determine where manuscripts rejected by *Anesthesia & Analgesia* were subsequently published. The script accompanies two manuscripts currently under review at *Anesthesia & Analgesia*: a full-length analysis of manuscript trajectories following editorial decisions and a companion Research Letter describing the tracking methodology.

**Author:** Valluvan Rangasamy, MD, MPH
**Position:** Editorial Fellow, *Anesthesia & Analgesia*
**Department:** Department of Anesthesia, Critical Care and Pain Medicine
**Institution:** Beth Israel Deaconess Medical Center / Harvard Medical School
**Contact:** vrangasa@bidmc.harvard.edu

---

## What the script does

Given a spreadsheet of rejected manuscripts (title, corresponding author, decision date, decision type), the script:

1. Filters the dataset to rejected manuscripts only (desk rejections and rejections after peer review).
2. Queries two bibliographic databases — **PubMed** and **CrossRef** — for each manuscript, restricted to publications dated at least 30 days after the rejection decision.
3. Scores every candidate record with a composite confidence score combining title similarity and corresponding-author surname concordance.
4. Sorts matches into high-, medium-, and low-confidence tiers and writes them to a multi-sheet Excel workbook.

All automated matches were subsequently verified manually. The automated step exists to make manual verification tractable, not to replace it.

## What an "API" is

An API (application programming interface) is a structured way for one program to request information from another over the internet. When you search PubMed in a web browser, you type a query and read the results on screen. An API lets a script do the same thing programmatically: it sends the query as a formatted web request and receives the results as machine-readable text (XML or JSON) instead of a web page. This script uses two free, public APIs:

- **NCBI E-utilities** (`esearch` to find PubMed IDs matching a query, `efetch` to retrieve the full records as XML)
- **CrossRef REST API** (a single endpoint that returns matching publication records as JSON, drawn from DOI registration metadata across publishers)

Both services impose rate limits — a cap on how many requests a client may send per second — so the script pauses briefly between requests and identifies itself with a contact email in accordance with NCBI's usage policy.

## How titles are compared: the Ratcliff–Obershelp algorithm

Manuscript titles often change slightly between rejection and eventual publication ("elderly" becomes "older," a subtitle is added, punctuation changes). Exact string matching would miss these, so the script measures *similarity* instead, using the Ratcliff–Obershelp algorithm as implemented in Python's standard-library `difflib.SequenceMatcher`.

The algorithm works by recursively finding the **longest common substring** of the two strings, then repeating the search in the unmatched text to the left and right of that block, until no common text remains. The similarity ratio is then:

similarity = 2 × M / T

where **M** is the total number of matching characters across all common blocks and **T** is the combined length of both strings. The ratio ranges from 0 (nothing in common) to 1 (identical).

Before comparison, both titles are normalized: converted to lowercase, stripped of punctuation, and collapsed to single spaces. This prevents trivial formatting differences (capitalization, hyphens, colons) from lowering the score.

### Worked example

Submitted title: Postoperative Delirium in Elderly Patients
Published title: Postoperative delirium in older patients

After cleaning, the strings are:
postoperative delirium in elderly patients   (42 characters)
postoperative delirium in older patients     (40 characters)

The algorithm finds three matching blocks totaling **M = 39** characters: the shared prefix `postoperative delirium in ` (26 characters), the fragment `lder` shared by "e**lder**ly" and "o**lder**" (4 characters), and the shared suffix ` patients` (9 characters). With T = 42 + 40 = 82:

similarity = 2 × 39 / 82 = 0.9512

### From similarity to confidence

Title similarity alone is not sufficient — short generic titles can score highly against unrelated papers. The composite confidence score therefore also checks whether the corresponding author's surname appears in the candidate record's author list:

confidence = 0.7 × title_similarity + 0.3 × author_match

where `author_match` is 1 if the surname is found and 0 otherwise. Continuing the example, if the corresponding author's surname appears among the candidate's authors:

confidence = 0.7 × 0.9512 + 0.3 = 0.9659   →   high-confidence tier

If the surname did not match:

confidence = 0.7 × 0.9512 + 0 = 0.6659     →   medium-confidence tier

The tiers used for triage are:

| Tier | Confidence score | Handling |
|---|---|---|
| High | ≥ 0.80 | Manually confirmed |
| Medium | 0.50 – 0.79 | Manually reviewed |
| Low | < 0.50 | Manually screened; supplemented by manual searching |

## Usage

Requirements: Python 3.8+, `pandas`, `requests`, `openpyxl`.

```bash
pip install pandas requests openpyxl
python manuscript_tracker.py
```

Place your input CSV in the same directory as the script. The script auto-detects common column names for the title, corresponding author, decision date, and final decision (see `COLUMN_OPTIONS` at the top of the script). Progress is logged to `search_log.txt`, intermediate results are saved every 50 manuscripts, and final results are written to `manuscript_search_results.xlsx` with one sheet per confidence tier plus a summary sheet.

Runtime scales with the number of manuscripts because of API rate limiting; a run of several hundred manuscripts takes hours.

## Scope of this repository

This repository contains only the automated publication-tracking component of the study pipeline. The remaining analysis code (journal classification and statistical analysis) is not publicly posted; access to that source code may be granted upon request to the author.

## Citation

If you use this code, please cite the accompanying Research Letter:

Rangasamy V, Mount D, Vetter T. Tracking the Publication Fate of Rejected Manuscripts:
A Hybrid Automated–Manual Approach. *Anesth Analg*. 2026.
doi:10.1213/ANE.0000000000008347

Software archive: [![DOI](https://zenodo.org/badge/DOI/ZENODO_DOI_HERE.svg)](https://doi.org/ZENODO_DOI_HERE)

A machine-readable citation is provided in `CITATION.cff` — GitHub's "Cite this repository" button generates APA and BibTeX formats automatically.

## Reference

Ratcliff JW, Metzener DE. Pattern matching: the Gestalt approach. *Dr. Dobb's Journal.* 1988;13(7):46.

## License

MIT
