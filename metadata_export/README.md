# metadata-export

This folder contains scripts that can be used for for exporting metadata for datasets deposited in FEGA Sweden.

## researchdata_se.py

### Prerequisites

-   Python 3
-   The Python library `requests:` <https://requests.readthedocs.io/en/latest/>

### Python virtual environment

Create and activate a virtual environment from the repository root:

``` text
python3 -m venv .venv
source .venv/bin/activate
```

Install `requests` inside the virtual environment:

``` text
python3 -m pip install --upgrade pip
python3 -m pip install requests
```

### Usage

``` text
./researchdata_se.py -h
usage: researchdata [-h] [-V] --creator {FEGA-SE,LiU,LU,UU,BTB}
                    --publisher {LiU,LU,UU,BTB}
                    [--keyword KEYWORD] [--site-base-url SITE_BASE_URL]
                    [--sitemap-filename SITEMAP_FILENAME]
                    study_id output_dir

A command-line utility for preparing FEGA Sweden metadata for researchdata.se

positional arguments:
  study_id              EGA Study accession number
  output_dir            Path to the output directory

options:
  -h, --help            show this help message and exit
  -V, --version         show program's version number and exit
  --creator {FEGA-SE,LiU,LU,UU,BTB}
                        main organisation that collected the data; repeat the option for multiple creators
  --publisher {LiU,LU,UU,BTB}
                        organisation responsible for publishing the dataset metadata record
  --keyword KEYWORD     keyword describing the dataset; repeat the option for multiple keywords
  --site-base-url SITE_BASE_URL
                        base URL for generated dataset landing pages
  --sitemap-filename SITEMAP_FILENAME
                        filename for the generated sitemap XML
```

### Example

``` text
./researchdata_se.py \
  --creator UU \
  --creator BTB \
  --publisher LU \
  --keyword "Swedish population" \
  --keyword "genetic variation" \
  --keyword genomics \
  EGAS50000000906 \
  tmp
```

### Output

The script writes:

-   one `.qmd` file per dataset accession to `output_dir`
-   a complete sitemap file, by default `sitemap.xml`, in the same directory

Example output:

``` text
Wrote tmp/EGAD50000001323.qmd
Wrote tmp/EGAD50000001324.qmd
Wrote tmp/sitemap.xml
```

### Metadata layers

The export currently works in three layers:

-   **EGA source metadata**: the validated study and dataset fields fetched from the EGA API
-   **Normalized internal metadata**: a script-internal model that separates source values, derived values, and FEGA Sweden-managed enrichment before rendering
-   **schema.org JSON-LD**: the final `Dataset` payload embedded in the generated `.qmd` files

This split makes it easier to evolve the mapping stepwise without having schema.org decisions leak into every part of the export flow.

### FEGA roles in schema.org

The export now models FEGA Sweden in two explicit supporting roles in addition to the dataset `publisher` field:

| Role | schema.org field | Current value |
| --- | --- | --- |
| Dataset publisher | `publisher` | selected organisation from `--publisher` |
| Catalog exposing the dataset | `includedInDataCatalog` | `FEGA Sweden` |
| Publisher of the structured metadata | `sdPublisher` | `FEGA Sweden` |

This means FEGA Sweden no longer needs to carry the full meaning of `publisher` when a responsible organisation is known. FEGA Sweden remains explicit as the catalog exposing the dataset and as publisher of the structured metadata, but is not used as dataset `publisher`.

### EGA to schema.org mapping

The generated `.qmd` files embed a `schema.org` JSON-LD `Dataset` payload in the YAML front matter.

| schema.org field | Metadata layer | Source in EGA / script input | Transformation / note |
| --- | --- | --- | --- |
| `@context` | FEGA Sweden-managed | hard-coded in script | always `https://schema.org` |
| `@type` | FEGA Sweden-managed | hard-coded in script | always `Dataset` |
| `identifier` | derived | `dataset.accession_id` | converted to `http://identifiers.org/ega.dataset:{accession_id}` |
| `name` | EGA source | `dataset.title` | trimmed and emitted as dataset title |
| `publisher` | FEGA Sweden enrichment | `--publisher` CLI option | required for Researchdata.se export; FEGA Sweden is not an allowed value |
| `includedInDataCatalog` | FEGA Sweden-managed | export site base URL | emitted as a `DataCatalog` for `FEGA Sweden` |
| `sdPublisher` | FEGA Sweden-managed | export site base URL | emitted as `FEGA Sweden` as publisher of the structured metadata |
| `datePublished` | derived from EGA source | `dataset.released_date` | parsed from ISO timestamp and normalized to `YYYY-MM-DD` |
| `description` | derived from EGA source | `dataset.description` | dataset description plus an appended summary saying which study the dataset belongs to |
| `inLanguage` | FEGA Sweden-managed | hard-coded in script | always English: `en` / `English` |
| `isPartOf.@id` | derived | `study.accession_id` | converted to `http://identifiers.org/ega.study:{accession_id}` |
| `isPartOf.name` | EGA source | `study.title` | copied from the EGA study title |
| `creator` | FEGA Sweden enrichment | repeated `--creator` CLI options | required for Researchdata.se export; emitted as one or more creators |
| `keywords` | FEGA Sweden enrichment | repeated `--keyword` CLI options | included only when one or more keywords are supplied |

Notes:

-   The normalization layer is implemented in `normalize_ega_dataset_metadata()`.
-   The final schema.org rendering is implemented in `transform_ega_dataset()`.
-   `identifier` and `isPartOf.@id` are derived identifiers, not values fetched directly from the API payload.
-   `publisher`, `creator`, and `keywords` are enrichment fields added during export, not native EGA metadata fields.
-   `includedInDataCatalog` and `sdPublisher` are derived from the export configuration, primarily `--site-base-url`.
-   Organisation objects omit keys whose values would otherwise be `null`, such as missing `@id` values.
-   `creator` and `publisher` can now be set independently on the command line.
-   Repeat `--creator` to include multiple creator organisations in the JSON-LD output.
-   `--creator` is required because Researchdata.se treats `creator` as mandatory.
-   `--publisher` is required because Researchdata.se treats `publisher` as mandatory.
-   `FEGA-SE` can be used as `creator`, but not as `publisher`.
-   If this mapping grows beyond a dozen rows or starts needing rationale per field, move it to a dedicated section or a separate mapping document and keep a short summary here.

### Notes

-   Use repeated `--keyword` options for multiple keywords.
-   `--site-base-url` lets you change the base URL used for generated landing-page links in the sitemap.
-   `--sitemap-filename` lets you override the default `sitemap.xml` filename.
-   The script validates required study and dataset fields and reports validation errors with accession-specific messages when metadata is missing or malformed.
-   Related datasets are fetched with pagination support, so studies with many datasets can be exported without manual paging.

### Tests

Run the regression and flow tests from the repository root:

``` text
python3 -m unittest discover -s tests -p 'test_*.py'
```

The current test coverage includes:

-   validation and transformation tests for the shared export core
-   parity tests that compare CLI output with core-generated artifacts for the same input
-   golden-output fixtures under `tests/fixtures/golden_export/` that lock representative `.qmd` and sitemap content

If you intentionally change the generated output format, update the corresponding fixture files in `tests/fixtures/golden_export/` and rerun the test suite before committing.
