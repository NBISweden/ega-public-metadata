# metadata-enrichment-app

This folder contains FEGA Sweden Metadata Enrichment, a Streamlit app for interactively enriching FEGA Sweden metadata and exporting it to Quarto `.qmd` files and a sitemap file.

The app is intended to replace the existing CLI export workflow over time, but the CLI script in `metadata_export/researchdata_se.py` is still kept as a reference path while the app is being validated.

This folder also contains `metadata_enrichment_app/cli.py`, the command-line interface for FEGA Sweden Metadata Enrichment, which uses the same project model and export core as the Streamlit app.

## Quick start

For a new user, the normal app workflow is:

1. Enter an `EGA Study ID`, fetch study metadata from EGA, and confirm that the loaded study is correct.
2. Fill in study-level metadata:
   creators, publisher, and optionally global keywords.
3. Select the datasets to include and add any dataset-specific keywords that are needed.
4. Click `Generate Export Files`.
5. Review the preview and download the generated files you need.

If you already have a saved project JSON, you can also load it directly in the app and continue from the saved state.

## Features

-   Fetch a study and its datasets from the EGA public metadata API
-   Set one or more creators at study level
-   Set a required publisher at study level
-   Use FEGA Sweden site metadata defaults, with optional override under advanced settings
-   Add global keywords for all datasets plus additional keywords per dataset
-   Include or exclude datasets directly alongside each dataset expander
-   Save a reproducible project snapshot as JSON
-   Load a saved project snapshot and continue editing
-   Preview generated `qmd` and `schema.org` JSON-LD
-   Download an individual generated dataset file or the sitemap directly from the preview
-   Download a zip archive containing dataset files, sitemap, and the saved project JSON, with study ID in the filename

The app generates preview and download artifacts when you explicitly click `Generate export`. If you change metadata afterwards, the app keeps showing the last generated snapshot until you generate again.

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
streamlit run metadata_enrichment_app/app.py
```

## CLI

The FEGA Sweden Metadata Enrichment CLI supports two modes:

-   `fetch`: fetch a study from EGA, enrich metadata from CLI options, and export files
-   `project`: regenerate export files from a saved project snapshot JSON

Example:

``` text
python3 metadata_enrichment_app/cli.py fetch \
  EGAS50000000906 \
  tmp/app-cli-export \
  --creator UU \
  --creator LU \
  --publisher BTB \
  --global-keyword genomics \
  --dataset-keyword EGAD50000001324="reference cohort" \
  --site-name "FEGA Sweden" \
  --site-base-url https://fega.nbis.se \
  --project-file tmp/app-cli-export/project.json \
  --zip-file tmp/app-cli-export/export.zip
```

To regenerate from a saved project snapshot:

``` text
python3 metadata_enrichment_app/cli.py project \
  tmp/app-cli-export/project.json \
  tmp/app-cli-regenerated
```

## Validation status

The app uses the same core export logic as `metadata_export/researchdata_se.py`.

The CLI script is still kept in the repository as a reference path until the Streamlit workflow is considered fully verified. This lets us compare outputs from both paths and reduce the risk of the app drifting semantically from the existing export behavior.

## Metadata mapping

The app generates a `schema.org` `Dataset` JSON-LD payload for each exported dataset and embeds it in the generated `.qmd` file.

| schema.org field | Metadata layer | Source in app / EGA | Transformation / note |
| --- | --- | --- | --- |
| `@context` | FEGA Sweden-managed | hard-coded in shared export core | always `https://schema.org` |
| `@type` | FEGA Sweden-managed | hard-coded in shared export core | always `Dataset` |
| `identifier` | derived | `dataset.accession_id` from EGA | converted to `http://identifiers.org/ega.dataset:{accession_id}` |
| `name` | EGA source | `dataset.title` from EGA | trimmed and emitted as dataset title |
| `publisher` | app enrichment | publisher selected in the app | required for Researchdata.se export; FEGA Sweden is not an allowed value |
| `includedInDataCatalog` | app-managed site metadata | FEGA Sweden defaults, optionally overridden in advanced settings | emitted as a `DataCatalog` for the configured site |
| `sdPublisher` | app-managed site metadata | FEGA Sweden defaults, optionally overridden in advanced settings | emitted as the configured site as publisher of the structured metadata |
| `datePublished` | derived from EGA source | `dataset.released_date` from EGA | parsed from ISO timestamp and normalized to `YYYY-MM-DD` |
| `description` | derived from EGA source | `dataset.description` from EGA | dataset description plus an appended summary saying which study the dataset belongs to |
| `inLanguage` | FEGA Sweden-managed | hard-coded in shared export core | always English: `en` / `English` |
| `isPartOf.@id` | derived | `study.accession_id` from EGA | converted to `http://identifiers.org/ega.study:{accession_id}` |
| `isPartOf.name` | EGA source | `study.title` from EGA | copied from the EGA study title |
| `creator` | app enrichment | one or more creators selected in the app | required for Researchdata.se export; emitted as one or more creators |
| `keywords` | app enrichment | global keywords plus dataset-specific additional keywords | required for Researchdata.se export; effective keywords are built as `global + dataset-specific`, deduplicated in order |

Notes:

-   `publisher`, `creator`, and `keywords` are enrichment fields added in the app, not native EGA metadata fields.
-   The app stores both global keywords and dataset-specific additional keywords in the saved project snapshot.
-   Preview and downloads are generated from the latest saved export snapshot, not directly from the current unsaved widget state.
-   `includedInDataCatalog` and `sdPublisher` are derived from the app's site name and site base URL settings, which default to FEGA Sweden and are only normally changed under advanced settings.

## Tests and golden files

Run the export tests from the repository root:

``` text
python3 -m unittest discover -s tests -p 'test_*.py'
```

The test suite currently includes:

-   CLI/core parity tests for the same export input
-   golden-output fixtures under `tests/fixtures/golden_export/`
-   project snapshot roundtrip tests
-   app state and validation tests for publisher selection, dataset selection, and required keywords

If you intentionally change the export format, update the affected fixture files in `tests/fixtures/golden_export/` so that they match the new expected output, then rerun the test suite and review the diff before committing.
