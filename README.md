# ega-public-metadata

Python code for interacting with the [EGA (European Genome-phenome Archive) public metadata API](https://ega-archive.org/discovery/metadata/public-metadata-api/). Code and examples are provided in a Jupyter notebook.

This README describes how to set up Python and run the Jupyter notebook in this repository.

The repository also contains export tooling for preparing metadata for Researchdata.se:

* `metadata_enrichment_app/cli.py` for the command-line workflow
* `metadata_enrichment_app/app.py` for the Streamlit-based workflow

Both workflows use the shared export logic in `metadata_enrichment_core/core.py` and are covered by the same regression tests.

For setup and usage instructions for the Streamlit app and CLI workflow, see the separate README in `metadata_enrichment_app/README.md`.

## Prerequisites

You need to have the following installed on your system:

* Python 3
* a terminal, such as Terminal on macOS or a shell on Linux


## Set up a virtual environment for the notebook

It is recommended to work in a Python virtual environment so that the notebook dependencies are kept separate from other Python projects on your machine.

From the repository root, run:

```text
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install jupyter pandas requests
```

When the environment is active, your shell prompt usually shows `(.venv)` at the beginning.

If you later want to leave the virtual environment, run:

```text
deactivate
```


## Run the Jupyter notebook

Start Jupyter from the repository root while the virtual environment is active:

```text
jupyter notebook
```

This opens Jupyter in your browser. Then open `ega-public-metadata.ipynb` and run the cells from top to bottom.

If you prefer JupyterLab, you can use:

```text
jupyter lab
```

If you are not familiar with Jupyter notebooks, you may want to first have a look at [Jupyter's documentation](https://docs.jupyter.org/en/latest/).


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
