# ega-public-metadata

Python code for interacting with the [EGA (European Genome-phenome Archive) public metadata API](https://ega-archive.org/discovery/metadata/public-metadata-api/). Code and examples are provided in a Jupyter notebook.

The repository also contains export tooling for preparing metadata for Researchdata.se:

* `metadata_export/researchdata_se.py` for the current CLI workflow
* `metadata_enrichment_app/app.py` for the new Streamlit-based workflow under development

During the transition, the CLI script is kept as a reference implementation while the Streamlit app is being validated against the same shared enrichment core and regression tests.

## Prerequisites

You need to have the following installed on your system:

* Python (for example the free [Anaconda distribution](https://anaconda.org))
* [Jupyter](https://jupyter.org) (JupyterLab or Jupyter Notebook)
* [pandas](https://pandas.pydata.org)
* [requests](https://requests.readthedocs.io/en/latest/)


## Getting started

Once you have installed the required software, just open the Jupyter notebook `ega-public-metadata.ipynb` and execute the code in the cells, from top to bottom. If you are not familiar with Jupyter notebooks, you may want to first have a look at [Jupyter's documentation](https://docs.jupyter.org/en/latest/).


## Export validation

The Researchdata.se export flow is currently validated in three ways:

* shared core logic used by both the CLI and the Streamlit app
* parity tests that compare CLI output with the shared core output for the same input
* golden-output fixtures under `tests/fixtures/golden_export/` that lock representative `.qmd` output

Run the export tests from the repository root:

``` text
python3 -m unittest discover -s tests -p 'test_*.py'
```


## License

MIT


## Maintainer

Markus Englund, markus.englund@nbis.se
