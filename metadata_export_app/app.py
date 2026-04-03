"""Streamlit UI for FEGA Sweden metadata export."""

from __future__ import annotations

import re
import sys

from pathlib import Path
from typing import cast

import requests
import streamlit as st

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from metadata_export_core.core import (
    DEFAULT_SITE_BASE_URL,
    DEFAULT_SITEMAP_FILENAME,
    EGAClient,
    ExportConfig,
    MetadataValidationError,
    ORGANISATIONS,
    PUBLISHER_ORGANISATIONS,
    StudyContext,
    build_export_artifacts,
    build_export_zip_bytes,
    fetch_study_context,
    transform_ega_dataset,
)


st.set_page_config(
    page_title='FEGA Sweden Metadata Export',
    page_icon=':material/data_object:',
    layout='wide',
)


def parse_keywords(raw_value: str) -> list[str]:
    return [
        keyword
        for keyword in (
            part.strip()
            for part in re.split(r'[\n,]', raw_value)
        )
        if keyword
    ]


@st.cache_data(show_spinner=False)
def load_study_context(study_id: str) -> StudyContext:
    with EGAClient() as client:
        return fetch_study_context(client, study_id.strip())


def initialize_dataset_state(study_context: StudyContext) -> None:
    for dataset in study_context.datasets:
        accession_id = dataset['accession_id']
        include_key = f'include_{accession_id}'
        keywords_key = f'keywords_{accession_id}'
        if include_key not in st.session_state:
            st.session_state[include_key] = True
        if keywords_key not in st.session_state:
            st.session_state[keywords_key] = ''


def build_preview_dataset(
    study_context: StudyContext,
    accession_id: str,
    creator_orgs: list[str],
    publisher_org: str,
    site_base_url: str,
) -> dict[str, object]:
    dataset = next(dataset for dataset in study_context.datasets if dataset['accession_id'] == accession_id)
    keywords = parse_keywords(cast(str, st.session_state.get(f'keywords_{accession_id}', '')))
    return cast(
        dict[str, object],
        transform_ega_dataset(
            ega_dataset=dataset,
            num_datasets=len(study_context.datasets),
            study_title=study_context.title,
            study_url=study_context.url,
            creator_orgs=creator_orgs,
            publisher_org=publisher_org,
            keywords=keywords,
            site_base_url=site_base_url.rstrip('/'),
        ),
    )


st.title('FEGA Sweden Metadata Export')
st.caption(
    'Interactive export workflow for study-level metadata plus dataset-specific enrichment, '
    'built to replace the current CLI once verified.'
)

st.markdown(
    """
This app keeps the export logic separate from the UI and lets you enrich each dataset individually,
especially keywords, before generating `.qmd` files and a sitemap bundle.
"""
)

with st.form('study_lookup'):
    study_id = st.text_input('EGA Study ID', placeholder='EGAS50000000906')
    fetch_clicked = st.form_submit_button('Fetch Study Metadata', use_container_width=True)

if fetch_clicked:
    if not study_id.strip():
        st.error('Enter an EGA Study ID first.')
    else:
        try:
            st.session_state['study_context'] = load_study_context(study_id.strip())
            st.session_state['loaded_study_id'] = study_id.strip()
        except requests.RequestException as exc:
            st.error(f'Failed to fetch metadata from the EGA API: {exc}')
        except MetadataValidationError as exc:
            st.error(f'Metadata validation failed: {exc}')
        except (KeyError, TypeError, ValueError) as exc:
            st.error(f'Failed to transform metadata: {exc}')

study_context = cast(StudyContext | None, st.session_state.get('study_context'))

if study_context is None:
    st.info('Fetch an EGA study to start editing metadata.')
    st.stop()

initialize_dataset_state(study_context)

summary_col, settings_col = st.columns([1, 2], gap='large')

with summary_col:
    st.subheader('Study Summary')
    st.metric('Study ID', st.session_state.get('loaded_study_id', ''))
    st.metric('Datasets', len(study_context.datasets))
    st.markdown(f'**Study title**  \n{study_context.title}')
    st.markdown(f'**Study identifier**  \n`{study_context.url}`')

with settings_col:
    st.subheader('Study-Level Metadata')
    creator_orgs = st.multiselect(
        'Creators',
        options=list(ORGANISATIONS.keys()),
        default=cast(list[str], st.session_state.get('creator_orgs', [])),
        help='Select one or more organisations that created or collected the data.',
    )
    st.session_state['creator_orgs'] = creator_orgs

    publisher_org = st.selectbox(
        'Publisher',
        options=list(PUBLISHER_ORGANISATIONS),
        index=0,
        help='Dataset publisher. FEGA Sweden is intentionally excluded here.',
    )
    site_base_url = st.text_input(
        'Site base URL',
        value=cast(str, st.session_state.get('site_base_url', DEFAULT_SITE_BASE_URL)),
    )
    st.session_state['site_base_url'] = site_base_url
    sitemap_filename = st.text_input(
        'Sitemap filename',
        value=cast(str, st.session_state.get('sitemap_filename', DEFAULT_SITEMAP_FILENAME)),
    )
    st.session_state['sitemap_filename'] = sitemap_filename

st.subheader('Dataset-Level Metadata')
st.caption('Keywords can be specified independently for each dataset before export.')

for dataset in study_context.datasets:
    accession_id = dataset['accession_id']
    with st.expander(f"{accession_id}: {dataset['title']}", expanded=False):
        left_col, right_col = st.columns([1, 2], gap='large')
        with left_col:
            st.checkbox(
                'Include in export',
                key=f'include_{accession_id}',
            )
            st.text_input(
                'Keywords',
                key=f'keywords_{accession_id}',
                help='Enter comma-separated or line-separated keywords for this dataset.',
            )
        with right_col:
            st.markdown(f"**Release date**  \n`{dataset['released_date']}`")
            st.markdown(f"**Description**  \n{dataset['description']}")

selected_accessions = {
    dataset['accession_id']
    for dataset in study_context.datasets
    if cast(bool, st.session_state.get(f"include_{dataset['accession_id']}", False))
}
dataset_keywords_by_accession = {
    dataset['accession_id']: parse_keywords(
        cast(str, st.session_state.get(f"keywords_{dataset['accession_id']}", ''))
    )
    for dataset in study_context.datasets
}

preview_col, action_col = st.columns([2, 1], gap='large')

with action_col:
    st.subheader('Generate Export')
    if not creator_orgs:
        st.warning('Select at least one creator before generating export files.')
    elif not selected_accessions:
        st.warning('Select at least one dataset to include in the export.')
    else:
        try:
            export_config = ExportConfig(
                site_base_url=site_base_url.rstrip('/'),
                sitemap_filename=sitemap_filename.strip() or DEFAULT_SITEMAP_FILENAME,
            )
            artifacts = build_export_artifacts(
                study_context=study_context,
                creator_orgs=creator_orgs,
                publisher_org=publisher_org,
                export_config=export_config,
                default_keywords=[],
                dataset_keywords_by_accession=dataset_keywords_by_accession,
                selected_accessions=selected_accessions,
            )
            zip_bytes = build_export_zip_bytes(artifacts)
            st.success(
                f'Prepared {len(artifacts.dataset_files)} dataset files and {artifacts.sitemap_file.filename}.'
            )
            st.download_button(
                'Download Export ZIP',
                data=zip_bytes,
                file_name='fega-sweden-metadata-export.zip',
                mime='application/zip',
                use_container_width=True,
            )
            st.session_state['artifacts'] = artifacts
        except MetadataValidationError as exc:
            st.error(f'Metadata validation failed: {exc}')

with preview_col:
    st.subheader('Preview')
    artifacts = st.session_state.get('artifacts')
    if not artifacts:
        st.info('Generate an export to preview the resulting files.')
    else:
        preview_options = [dataset_file.filename for dataset_file in artifacts.dataset_files]
        preview_filename = st.selectbox('Preview dataset file', options=preview_options)
        preview_file = next(
            dataset_file for dataset_file in artifacts.dataset_files if dataset_file.filename == preview_filename
        )
        preview_accession = preview_filename.removesuffix('.qmd')
        preview_dataset = build_preview_dataset(
            study_context=study_context,
            accession_id=preview_accession,
            creator_orgs=creator_orgs,
            publisher_org=publisher_org,
            site_base_url=site_base_url,
        )

        preview_tabs = st.tabs(['QMD', 'JSON-LD', 'Sitemap'])
        with preview_tabs[0]:
            st.code(preview_file.content, language='markdown')
        with preview_tabs[1]:
            st.json(preview_dataset, expanded=False)
        with preview_tabs[2]:
            st.code(artifacts.sitemap_file.content, language='xml')
