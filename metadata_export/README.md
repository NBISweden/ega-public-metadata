# metadata-export

This folder contains scripts that can be used for for exporting metadata for datasets deposited in FEGA Sweden.

## researchdata_se.py

### Prerequisites

-   Python 3
-   The Python library `requests:` <https://requests.readthedocs.io/en/latest/>

### Usage

``` text
./researchdata_se.py -h
usage: researchdata [-h] [-V] [--creator {unspecified,FEGA-SE,LiU,LU,UU,BTB}] [--keywords [KEYWORDS ...]] study_id output_dir

A command-line utility for preparing FEGA Sweden metadata for researchdata.se

positional arguments:
  study_id              EGA Study accession number
  output_dir            Path to the output directory

options:
  -h, --help            show this help message and exit
  -V, --version         show program's version number and exit
  --creator {unspecified,FEGA-SE,LiU,LU,UU,BTB}
                        main organisation that collected the data
  --keywords [KEYWORDS ...]
                        keywords describing the dataset
```

The script writes one `.qmd` file per dataset accession to `output_dir` and also generates a complete `sitemap.xml` in the same directory for the exported dataset pages.
