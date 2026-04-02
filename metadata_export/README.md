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
