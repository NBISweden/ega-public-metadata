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
usage: researchdata [-h] [-V] [--creator {unspecified,FEGA-SE,LiU,LU,UU,BTB}]
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
  --creator {unspecified,FEGA-SE,LiU,LU,UU,BTB}
                        main organisation that collected the data
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

### EGA to schema.org mapping

The generated `.qmd` files embed a `schema.org` JSON-LD `Dataset` payload in the YAML front matter. Documenting the mapping here is a good default, because it keeps the contract close to the export script and makes changes easy to review.

| schema.org field | Source in EGA / script input | Transformation / note |
| --- | --- | --- |
| `@context` | hard-coded in script | always `https://schema.org` |
| `@type` | hard-coded in script | always `Dataset` |
| `identifier` | `dataset.accession_id` | converted to `http://identifiers.org/ega.dataset:{accession_id}` |
| `name` | `dataset.title` | trimmed and emitted as dataset title |
| `publisher` | hard-coded in script | always `FEGA Sweden` |
| `datePublished` | `dataset.released_date` | parsed from ISO timestamp and normalized to `YYYY-MM-DD` |
| `description` | `dataset.description` | dataset description plus an appended summary saying which study the dataset belongs to |
| `inLanguage` | hard-coded in script | always English: `en` / `English` |
| `isPartOf.@id` | `study.accession_id` | converted to `http://identifiers.org/ega.study:{accession_id}` |
| `isPartOf.name` | `study.title` | copied from the EGA study title |
| `creator` | `--creator` CLI option | included only when a specific organisation is supplied; omitted for `unspecified` |
| `keywords` | repeated `--keyword` CLI options | included only when one or more keywords are supplied |

Notes:

-   The mapping is implemented in `transform_ega_dataset()`.
-   `identifier` and `isPartOf.@id` are derived identifiers, not values fetched directly from the API payload.
-   `creator` and `keywords` are enrichment fields added during export, not native EGA metadata fields.
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
