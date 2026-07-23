#!/usr/bin/env python3
"""
manuscript_tracker.py

Tracks where rejected manuscripts were eventually published.

For each rejected manuscript, the script searches PubMed (NCBI E-utilities)
and CrossRef, then scores every candidate record with a composite
confidence score:

    confidence = 0.7 * title_similarity + 0.3 * author_match

Title similarity is the Ratcliff-Obershelp ratio (difflib.SequenceMatcher)
between cleaned titles; author_match is 1 if the corresponding author's
surname appears in the candidate's author list, else 0. Matches are
grouped into tiers for manual verification:

    high   : confidence >= 0.80
    medium : 0.50 <= confidence < 0.80
    low    : confidence < 0.50

Input : CSV with a manuscript title, corresponding author, final decision
        date, and final decision label (column names are auto-detected).
Output: Excel workbook with one sheet per confidence tier plus a summary.

Author      : Valluvan Rangasamy, MD, MPH
Department  : Department of Anesthesia, Critical Care and Pain Medicine
Institution : Beth Israel Deaconess Medical Center / Harvard Medical School
Contact     : vrangasa@bidmc.harvard.edu
License     : MIT
"""

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from difflib import SequenceMatcher

import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("search_log.txt"), logging.StreamHandler()],
)

# Candidate column names, checked in order.
COLUMN_OPTIONS = {
    "title": ["title", "manuscript_title", "paper_title"],
    "author": ["corresponding_author", "corresp_name_lastfirst", "corresp_fullname", "author"],
    "date": ["date_final_decision", "rejection_date", "decision_date"],
    "decision": ["decision_final", "final_decision", "decision", "outcome"],
}


class ManuscriptTracker:
    def __init__(self, csv_file_path):
        self.csv_file = csv_file_path
        self.session = requests.Session()
        # NCBI asks that automated clients identify themselves with a
        # contact email (https://www.ncbi.nlm.nih.gov/books/NBK25497/).
        self.session.headers.update(
            {"User-Agent": "rejection_tracker/1.0 (mailto:vrangasa@bidmc.harvard.edu)"}
        )

        self.df = pd.read_csv(csv_file_path)
        logging.info(f"Loaded {len(self.df)} manuscripts from {csv_file_path}")

        # Detect the four required columns.
        found = {}
        for role, options in COLUMN_OPTIONS.items():
            found[role] = next((c for c in options if c in self.df.columns), None)

        missing = [f"{role} column (tried: {', '.join(COLUMN_OPTIONS[role])})"
                   for role, col in found.items() if col is None]
        if missing:
            print("Error: Could not find required columns:")
            for m in missing:
                print(f"  - {m}")
            print(f"Available columns: {list(self.df.columns)}")
            raise ValueError("Required columns not found")

        self.title_col = found["title"]
        self.author_col = found["author"]
        self.date_col = found["date"]
        self.decision_col = found["decision"]
        print(f"Using columns: title='{self.title_col}', author='{self.author_col}', "
              f"date='{self.date_col}', decision='{self.decision_col}'")

        # Show the decision categories, then keep only rejections.
        print("\nDecision categories found:")
        for decision, count in self.df[self.decision_col].value_counts().items():
            print(f"  {decision}: {count}")

        original_count = len(self.df)
        decisions = self.df[self.decision_col].str.lower()
        rejected = self.df[
            decisions.str.contains("desk rejection", na=False)
            | decisions.str.contains("rejection after peer-review", na=False)
            | decisions.str.contains(r"peer.*review.*reject", na=False)
        ]
        if len(rejected) == 0:
            # Fall back to a broader filter if the labels differ.
            print("Using broader rejection filter...")
            rejected = self.df[decisions.str.contains("reject|decline", na=False)]
        if len(rejected) == 0:
            print("\nNo rejected manuscripts found with automatic filtering.")
            print("Found decision values:", list(self.df[self.decision_col].unique()))
            raise ValueError("No rejected manuscripts found")

        self.df = rejected
        print(f"\nFiltered manuscripts:")
        print(f"  Original total: {original_count}")
        print(f"  Rejected manuscripts: {len(self.df)}")
        print(f"  Excluded (accepted): {original_count - len(self.df)}")

    # ------------------------------------------------------------------
    # Matching helpers
    # ------------------------------------------------------------------

    def clean_title(self, title):
        """Lowercase a title and strip punctuation and extra whitespace."""
        if pd.isna(title):
            return ""
        title = str(title).lower().strip()
        title = re.sub(r"[^\w\s]", " ", title)
        title = re.sub(r"\s+", " ", title)
        return title.strip()

    def extract_author_lastname(self, author_name):
        """Get the surname from 'Last, First' or 'First Last'."""
        if pd.isna(author_name):
            return ""
        name = str(author_name).strip()
        if "," in name:
            return name.split(",")[0].strip().lower()
        parts = name.split()
        return parts[-1].lower() if parts else ""

    def similarity_score(self, title1, title2):
        """Ratcliff-Obershelp similarity between two cleaned titles (0-1)."""
        return SequenceMatcher(None, self.clean_title(title1), self.clean_title(title2)).ratio()

    def score_candidate(self, original_title, found_title, author_lastname, candidate_authors):
        """Return (title_similarity, author_match, confidence) for a candidate."""
        similarity = self.similarity_score(original_title, found_title)
        author_match = bool(author_lastname) and any(
            author_lastname in a.lower() for a in candidate_authors
        )
        confidence = similarity * 0.7 + (0.3 if author_match else 0)
        return similarity, author_match, confidence

    # ------------------------------------------------------------------
    # Database searches
    # ------------------------------------------------------------------

    def search_pubmed(self, title, author_lastname, min_date=None):
        """Search PubMed via E-utilities (esearch + efetch)."""
        try:
            query_parts = [f'"{self.clean_title(title)}"[Title]']
            if author_lastname:
                query_parts.append(f"{author_lastname}[Author]")
            if min_date:
                date_str = min_date.strftime("%Y/%m/%d")
                query_parts.append(
                    f'("{date_str}"[Date - Publication] : "3000"[Date - Publication])'
                )

            response = self.session.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params={"db": "pubmed", "term": " AND ".join(query_parts),
                        "retmax": 20, "retmode": "xml"},
            )
            time.sleep(0.5)  # rate limiting
            if response.status_code != 200:
                return []

            pmids = [e.text for e in ET.fromstring(response.content).findall(".//Id")]
            if not pmids:
                return []

            response = self.session.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"},
            )
            time.sleep(0.5)
            if response.status_code != 200:
                return []

            return self.parse_pubmed_results(response.content, title, author_lastname)

        except Exception as e:
            logging.error(f"Error searching PubMed: {e}")
            return []

    def parse_pubmed_results(self, xml_content, original_title, original_author_lastname):
        """Parse PubMed efetch XML into scored candidate records."""
        results = []
        try:
            root = ET.fromstring(xml_content)
            for article in root.findall(".//PubmedArticle"):
                try:
                    title_elem = article.find(".//ArticleTitle")
                    found_title = title_elem.text if title_elem is not None else "No title"

                    authors = []
                    for author in article.findall(".//Author"):
                        lastname = author.find("LastName")
                        forename = author.find("ForeName")
                        if lastname is not None:
                            name = lastname.text
                            if forename is not None:
                                name += f", {forename.text}"
                            authors.append(name)

                    year_elem = article.find(".//PubDate/Year")
                    pub_date = year_elem.text if year_elem is not None else "Unknown"

                    journal_elem = article.find(".//Journal/Title")
                    journal = journal_elem.text if journal_elem is not None else "Unknown"

                    pmid_elem = article.find(".//PMID")
                    pmid = pmid_elem.text if pmid_elem is not None else "Unknown"

                    similarity, author_match, confidence = self.score_candidate(
                        original_title, found_title, original_author_lastname, authors
                    )

                    results.append({
                        "source": "PubMed",
                        "title": found_title,
                        "authors": "; ".join(authors[:5]),
                        "journal": journal,
                        "publication_date": pub_date,
                        "pmid": pmid,
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        "title_similarity": similarity,
                        "author_match": author_match,
                        "confidence_score": confidence,
                    })
                except Exception as e:
                    logging.error(f"Error parsing individual PubMed result: {e}")
        except Exception as e:
            logging.error(f"Error parsing PubMed XML: {e}")
        return results

    def search_crossref(self, title, author_lastname, min_date=None):
        """Search the CrossRef REST API."""
        try:
            params = {"query.title": title, "rows": 10}
            if author_lastname:
                params["query.author"] = author_lastname
            if min_date:
                params["filter"] = f"from-pub-date:{min_date.strftime('%Y-%m-%d')}"

            response = self.session.get("https://api.crossref.org/works", params=params)
            time.sleep(1)  # CrossRef rate limiting
            if response.status_code != 200:
                return []

            results = []
            for item in response.json().get("message", {}).get("items", []):
                try:
                    found_title = " ".join(item.get("title", ["No title"]))

                    authors = []
                    for author in item.get("author", []):
                        if "family" in author:
                            name = author["family"]
                            if "given" in author:
                                name += f", {author['given']}"
                            authors.append(name)

                    pub_date = "Unknown"
                    for key in ("published-print", "published-online"):
                        if key in item:
                            date_parts = item[key].get("date-parts", [[]])[0]
                            if date_parts:
                                pub_date = str(date_parts[0])
                                break

                    journal = item.get("container-title", ["Unknown"])[0]
                    doi = item.get("DOI", "Unknown")

                    similarity, author_match, confidence = self.score_candidate(
                        title, found_title, author_lastname, authors
                    )

                    results.append({
                        "source": "CrossRef",
                        "title": found_title,
                        "authors": "; ".join(authors[:5]),
                        "journal": journal,
                        "publication_date": pub_date,
                        "doi": doi,
                        "url": f"https://doi.org/{doi}" if doi != "Unknown" else "Unknown",
                        "title_similarity": similarity,
                        "author_match": author_match,
                        "confidence_score": confidence,
                    })
                except Exception as e:
                    logging.error(f"Error parsing CrossRef result: {e}")
            return results
        except Exception as e:
            logging.error(f"Error searching CrossRef: {e}")
            return []

    # ------------------------------------------------------------------
    # Main workflow
    # ------------------------------------------------------------------

    def process_manuscript(self, row):
        """Search both databases for one manuscript and rank the candidates."""
        title = row[self.title_col]
        author = row[self.author_col]
        decision_date_str = row[self.date_col]

        logging.info(f"Processing: {str(title)[:50]}...")

        # Restrict searches to publications dated at least 30 days after
        # the rejection decision.
        min_date = None
        if pd.notna(decision_date_str):
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
                try:
                    min_date = datetime.strptime(str(decision_date_str), fmt) + timedelta(days=30)
                    break
                except ValueError:
                    continue
            if min_date is None:
                logging.warning(f"Could not parse date {decision_date_str}")

        author_lastname = self.extract_author_lastname(author)

        all_results = self.search_pubmed(title, author_lastname, min_date)
        all_results += self.search_crossref(title, author_lastname, min_date)
        all_results.sort(key=lambda x: x["confidence_score"], reverse=True)

        return {
            "original_title": title,
            "original_author": author,
            "rejection_date": decision_date_str,
            "decision_type": row[self.decision_col],
            "search_results": all_results,
            "best_match": all_results[0] if all_results else None,
            "total_matches": len(all_results),
        }

    def run_search(self):
        """Process every rejected manuscript and save the results."""
        logging.info("Starting manuscript search process...")
        all_results = []
        total = len(self.df)

        for idx, row in self.df.iterrows():
            try:
                logging.info(f"Processing manuscript {idx + 1}/{total}")
                all_results.append(self.process_manuscript(row))

                # Save intermediate results every 50 manuscripts.
                if (idx + 1) % 50 == 0:
                    self.save_results(all_results, suffix=f"_partial_{idx + 1}")
                    logging.info(f"Saved intermediate results at {idx + 1} manuscripts")
            except Exception as e:
                logging.error(f"Error processing manuscript {idx + 1}: {e}")

        self.save_results(all_results)
        logging.info("Search process completed!")
        return all_results

    def save_results(self, results, suffix=""):
        """Write matches to an Excel workbook, one sheet per confidence tier."""

        def match_row(base, match):
            row = base.copy()
            row.update({
                "Found_Title": match["title"],
                "Found_Authors": match["authors"],
                "Journal": match["journal"],
                "Publication_Date": match["publication_date"],
                "Source": match["source"],
                "URL": match["url"],
                "Title_Similarity": match["title_similarity"],
                "Author_Match": match["author_match"],
                "Confidence_Score": match["confidence_score"],
            })
            return row

        try:
            high, medium, low, none = [], [], [], []

            for result in results:
                base = {
                    "Original_Title": result["original_title"],
                    "Original_Author": result["original_author"],
                    "Rejection_Date": result["rejection_date"],
                    "Decision_Type": result["decision_type"],
                    "Total_Matches_Found": result["total_matches"],
                }
                best = result["best_match"]
                if best and best["confidence_score"] >= 0.8:
                    high.append(match_row(base, best))
                elif best and best["confidence_score"] >= 0.5:
                    medium.append(match_row(base, best))
                elif result["search_results"]:
                    low.extend(match_row(base, m) for m in result["search_results"])
                else:
                    none.append(base)

            filename = f"manuscript_search_results{suffix}.xlsx"
            with pd.ExcelWriter(filename, engine="openpyxl") as writer:
                for sheet, rows in [
                    ("High_Confidence_Matches", high),
                    ("Medium_Confidence_Matches", medium),
                    ("Low_Confidence_Matches", low),
                    ("No_Matches_Found", none),
                ]:
                    if rows:
                        pd.DataFrame(rows).to_excel(writer, sheet_name=sheet, index=False)

                summary = pd.DataFrame({
                    "Category": ["High Confidence (>=80%)", "Medium Confidence (50-79%)",
                                 "Low Confidence (<50%)", "No Matches Found",
                                 "Total Manuscripts"],
                    "Count": [len(high), len(medium), len(low), len(none), len(results)],
                })
                summary.to_excel(writer, sheet_name="Summary", index=False)

            logging.info(f"Results saved to {filename}")
            print(f"\nResults saved to: {filename}")
            print(f"High confidence matches: {len(high)}")
            print(f"Medium confidence matches: {len(medium)}")
            print(f"Low confidence matches: {len(low)}")
            print(f"No matches found: {len(none)}")

        except Exception as e:
            logging.error(f"Error saving results: {e}")


def main():
    print("Manuscript Publication Tracker")
    print("=" * 40)

    csv_files = [f for f in os.listdir(".") if f.endswith(".csv")]
    if not csv_files:
        print("Error: No CSV file found in the current directory.")
        print("Place your CSV file in the same folder as this script.")
        input("Press Enter to exit...")
        return

    if len(csv_files) == 1:
        csv_file = csv_files[0]
        print(f"Found CSV file: {csv_file}")
    else:
        print("Multiple CSV files found:")
        for i, f in enumerate(csv_files, 1):
            print(f"{i}. {f}")
        try:
            csv_file = csv_files[int(input("Enter the number of the file to use: ")) - 1]
        except (ValueError, IndexError):
            print("Invalid choice. Using the first file.")
            csv_file = csv_files[0]

    try:
        tracker = ManuscriptTracker(csv_file)
        print(f"\nProcessing {len(tracker.df)} manuscripts...")
        print("This may take several hours. Progress is logged to 'search_log.txt'.")
        print("Intermediate results are saved every 50 manuscripts.")

        if input("\nPress Enter to start, or 'q' to quit: ").lower() == "q":
            return

        tracker.run_search()
        print("\nSearch completed! Check the Excel file for results.")

    except Exception as e:
        logging.error(f"Error in main process: {e}")
        print(f"An error occurred: {e}")
        print("Check 'search_log.txt' for details.")

    input("Press Enter to exit...")


if __name__ == "__main__":
    main()
