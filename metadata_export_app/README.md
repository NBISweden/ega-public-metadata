# metadata-export-app

This folder contains a Streamlit app for interactively exporting FEGA Sweden metadata to Quarto `.qmd` files and a sitemap file.

The app is intended to replace the existing CLI export workflow over time, but the CLI script in `metadata_export/researchdata_se.py` is still kept as a reference path while the app is being validated.

## Features

-   Fetch a study and its datasets from the EGA public metadata API
-   Set one or more creators at study level
-   Set a required publisher at study level
-   Add keywords individually per dataset
-   Save a reproducible project snapshot as JSON
-   Load a saved project snapshot and continue editing
-   Preview generated `qmd` and `schema.org` JSON-LD
-   Download generated dataset files and sitemap as a zip archive

## Prerequisites

-   Python 3
-   `requests`
-   `streamlit`

Example installation in a virtual environment from the repository root:

``` text
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install requests streamlit
```

## Run

From the repository root:

``` text
streamlit run metadata_export_app/app.py
```
