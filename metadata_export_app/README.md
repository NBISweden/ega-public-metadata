# metadata-export-app

This folder contains a Streamlit app for interactively exporting FEGA Sweden metadata to Quarto `.qmd` files and a sitemap file.

The app is intended to replace the existing CLI export workflow over time, but the CLI script in `metadata_export/researchdata_se.py` is still kept as a reference path while the app is being validated.

## Features

-   Fetch a study and its datasets from the EGA public metadata API
-   Set one or more creators at study level
-   Set a required publisher at study level
-   Add required keywords individually per dataset
-   Save a reproducible project snapshot as JSON
-   Load a saved project snapshot and continue editing
-   Preview generated `qmd` and `schema.org` JSON-LD
-   Download generated dataset files and sitemap as a zip archive
-   Write the currently selected export directly to a local output directory

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

## Validation status

The app uses the same core export logic as `metadata_export/researchdata_se.py`.

The CLI script is still kept in the repository as a reference path until the Streamlit workflow is considered fully verified. This lets us compare outputs from both paths and reduce the risk of the app drifting semantically from the existing export behavior.

## Tests and golden files

Run the export tests from the repository root:

``` text
python3 -m unittest discover -s tests -p 'test_*.py'
```

The test suite currently includes:

-   CLI/core parity tests for the same export input
-   golden-output fixtures under `tests/fixtures/golden_export/`
-   project snapshot roundtrip tests

If you intentionally change the export format, update the affected fixture files in `tests/fixtures/golden_export/` so that they match the new expected output, then rerun the test suite and review the diff before committing.
