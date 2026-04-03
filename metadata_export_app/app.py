"""Streamlit UI for FEGA Sweden Metadata Enrichment."""

from __future__ import annotations

from datetime import date
import json
import sys

from pathlib import Path
from typing import cast

import requests
import streamlit as st

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from metadata_export_core.core import (
    DEFAULT_SITE_NAME,
    DEFAULT_SITE_BASE_URL,
    DEFAULT_SITEMAP_FILENAME,
    EGAClient,
    ExportConfig,
    ExportProject,
    GeneratedFile,
    MetadataValidationError,
    ORGANISATIONS,
    PUBLISHER_ORGANISATIONS,
    StudyContext,
    build_export_artifacts_from_project,
    build_export_project,
    build_export_zip_bytes,
    deserialize_export_project,
    fetch_study_context,
    merge_keywords,
    serialize_export_project,
)
from metadata_export_app.state import (
    build_export_archive_filename,
    build_export_request_signature,
    build_preview_dataset_from_project,
    build_project_filename,
    clear_generated_export_state,
    collect_effective_dataset_keywords_by_accession,
    collect_dataset_keywords_by_accession,
    collect_global_keywords,
    collect_sitemap_lastmod,
    collect_selected_accessions,
    find_selected_accessions_missing_keywords,
    get_export_validation_message,
    get_organisation_display_name,
    get_publisher_select_index,
    initialize_dataset_state,
    initialize_form_state_defaults,
    parse_keywords,
    restore_project_to_session_state,
)


st.set_page_config(
    page_title='FEGA Sweden Metadata Enrichment',
    page_icon=':material/data_object:',
    layout='wide',
)
@st.cache_data(show_spinner=False)
def load_study_context(study_id: str) -> StudyContext:
    with EGAClient() as client:
        return fetch_study_context(client, study_id.strip())


st.title('FEGA Sweden Metadata Enrichment')
st.caption(
    'Enrich public EGA study metadata and generate Researchdata.se export files.'
)

st.markdown(
    """
Use this app to look up an EGA study, review the datasets that belong to it, add the metadata
needed for publication, and generate Quarto `.qmd` files together with a sitemap and a saved
project snapshot.
"""
)

uploaded_project = st.file_uploader(
    'Load saved export project',
    type=['json'],
    help='Load a previously saved project snapshot to regenerate or update export files.',
)
if uploaded_project is not None:
    try:
        project = deserialize_export_project(uploaded_project.getvalue().decode('utf-8'))
        restore_project_to_session_state(project, st.session_state)
        st.success(f'Loaded project for study {project["study_id"]}.')
    except (UnicodeDecodeError, MetadataValidationError, json.JSONDecodeError) as exc:
        st.error(f'Failed to load project file: {exc}')

with st.form('study_lookup'):
    study_id = st.text_input('EGA Study ID', placeholder='EGAS50000000906')
    fetch_clicked = st.form_submit_button('Fetch Study Metadata', use_container_width=True)

if fetch_clicked:
    if not study_id.strip():
        st.error('Enter an EGA Study ID first.')
    else:
        try:
            clear_generated_export_state(st.session_state)
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

initialize_dataset_state(study_context, st.session_state)
initialize_form_state_defaults(st.session_state)

summary_col, settings_col = st.columns([1, 2], gap='large')

with summary_col:
    st.subheader('Study Summary')
    st.metric('Study ID', st.session_state.get('loaded_study_id', ''))
    st.metric('Datasets', len(study_context.datasets))
    st.markdown(f'**Study title**  \n{study_context.title}')
    st.markdown(f'**Study identifier**  \n`{study_context.url}`')

with settings_col:
    st.subheader('Study-Level Metadata')
    st.multiselect(
        'Creators',
        options=list(ORGANISATIONS.keys()),
        key='creator_orgs',
        format_func=get_organisation_display_name,
        help='Select one or more organisations that created or collected the data.',
    )
    st.selectbox(
        'Publisher',
        options=list(PUBLISHER_ORGANISATIONS),
        index=get_publisher_select_index(cast(str | None, st.session_state.get('publisher_org'))),
        placeholder='Choose a publisher',
        key='publisher_org',
        format_func=get_organisation_display_name,
        help='Dataset publisher. FEGA Sweden is intentionally excluded here.',
    )
    st.text_input(
        'Site name',
        key='site_name',
        help='Used as the name for includedInDataCatalog and sdPublisher.',
    )
    st.text_input(
        'Site base URL',
        key='site_base_url',
    )
    st.text_input(
        'Sitemap filename',
        key='sitemap_filename',
    )
    sitemap_lastmod_toggle_col, sitemap_lastmod_value_col = st.columns([1.2, 1.8], gap='small')
    with sitemap_lastmod_toggle_col:
        st.checkbox(
            'Set sitemap lastmod',
            key='use_sitemap_lastmod',
            help='Use the selected date for sitemap <lastmod>. If left unchecked, <lastmod> is omitted.',
        )
    with sitemap_lastmod_value_col:
        st.date_input(
            'Sitemap last modified',
            key='sitemap_lastmod_date',
            disabled=not bool(st.session_state.get('use_sitemap_lastmod', False)),
            format='YYYY-MM-DD',
        )
    st.text_area(
        'Global keywords',
        key='global_keywords_raw',
        help='Keywords applied to every included dataset. Use commas or new lines.',
    )

creator_orgs = cast(list[str], st.session_state.get('creator_orgs', []))
publisher_org = cast(str | None, st.session_state.get('publisher_org'))
site_name = cast(str, st.session_state.get('site_name', DEFAULT_SITE_NAME))
site_base_url = cast(str, st.session_state.get('site_base_url', DEFAULT_SITE_BASE_URL))
sitemap_filename = cast(str, st.session_state.get('sitemap_filename', DEFAULT_SITEMAP_FILENAME))
sitemap_lastmod = collect_sitemap_lastmod(st.session_state)
global_keywords = collect_global_keywords(st.session_state)

st.subheader('Dataset-Level Metadata')
st.caption(
    'Additional keywords can be specified per dataset. '
    'Each selected dataset must end up with at least one keyword from global keywords, '
    'dataset-specific keywords, or both.'
)

actions_col, spacer_col = st.columns([1.6, 3.4], gap='large')
with actions_col:
    st.markdown('**Selection**')
    selection_button_cols = st.columns(2, gap='small')
    with selection_button_cols[0]:
        if st.button('Select all', use_container_width=True):
            for dataset in study_context.datasets:
                st.session_state[f"include_{dataset['accession_id']}"] = True
    with selection_button_cols[1]:
        if st.button('Select none', use_container_width=True):
            for dataset in study_context.datasets:
                st.session_state[f"include_{dataset['accession_id']}"] = False

for dataset in study_context.datasets:
    accession_id = dataset['accession_id']
    local_keywords = parse_keywords(
        cast(str, st.session_state.get(f'keywords_{accession_id}', ''))
    )
    effective_keywords = merge_keywords(global_keywords, local_keywords)
    row_cols = st.columns([0.35, 9.65], gap='small')
    with row_cols[0]:
        st.checkbox(
            'Include in export',
            key=f'include_{accession_id}',
            label_visibility='collapsed',
        )
    with row_cols[1]:
        with st.expander(f"{accession_id}: {dataset['title']}", expanded=False):
            left_col, right_col = st.columns([1, 2], gap='large')
            with left_col:
                st.text_input(
                    'Additional keywords',
                    key=f'keywords_{accession_id}',
                    help='Enter comma-separated or line-separated keywords to add for this dataset.',
                )
            with right_col:
                st.markdown(f"**Release date**  \n`{dataset['released_date']}`")
                st.markdown(f"**Description**  \n{dataset['description']}")
                if global_keywords:
                    st.markdown(f"**Global keywords**  \n{', '.join(global_keywords)}")
                st.markdown(
                    '**Effective keywords**  \n'
                    + (', '.join(effective_keywords) if effective_keywords else '_missing_')
                )

selected_accessions = collect_selected_accessions(study_context, st.session_state)
dataset_keywords_by_accession = collect_dataset_keywords_by_accession(study_context, st.session_state)
effective_dataset_keywords_by_accession = collect_effective_dataset_keywords_by_accession(
    study_context,
    global_keywords,
    dataset_keywords_by_accession,
)
selected_accessions_missing_keywords = find_selected_accessions_missing_keywords(
    study_context,
    selected_accessions,
    effective_dataset_keywords_by_accession,
)

preview_col, action_col = st.columns([2, 1], gap='large')

with action_col:
    st.subheader('Generate Export')
    validation_message = get_export_validation_message(
        creator_orgs=creator_orgs,
        publisher_org=publisher_org,
        selected_accessions=selected_accessions,
        selected_accessions_missing_keywords=selected_accessions_missing_keywords,
    )
    current_signature = build_export_request_signature(
        study_id=cast(str, st.session_state.get('loaded_study_id', '')),
        creator_orgs=creator_orgs,
        publisher_org=publisher_org,
        site_name=site_name.strip() or DEFAULT_SITE_NAME,
        site_base_url=site_base_url.rstrip('/'),
        sitemap_filename=sitemap_filename.strip() or DEFAULT_SITEMAP_FILENAME,
        sitemap_lastmod=sitemap_lastmod,
        global_keywords=global_keywords,
        dataset_keywords_by_accession=dataset_keywords_by_accession,
        selected_accessions=selected_accessions,
    )
    generate_clicked = st.button(
        'Generate export',
        type='primary',
        use_container_width=True,
        disabled=validation_message is not None,
    )
    if validation_message is not None:
        st.warning(validation_message)
    elif generate_clicked:
        try:
            export_config = ExportConfig(
                site_name=site_name.strip() or DEFAULT_SITE_NAME,
                site_base_url=site_base_url.rstrip('/'),
                sitemap_filename=sitemap_filename.strip() or DEFAULT_SITEMAP_FILENAME,
                sitemap_lastmod=sitemap_lastmod,
            )
            project = build_export_project(
                study_id=cast(str, st.session_state.get('loaded_study_id', '')),
                study_context=study_context,
                creator_orgs=creator_orgs,
                publisher_org=publisher_org,
                export_config=export_config,
                global_keywords=global_keywords,
                dataset_keywords_by_accession=dataset_keywords_by_accession,
                selected_accessions=selected_accessions,
            )
            artifacts = build_export_artifacts_from_project(project)
            project_json = serialize_export_project(project)
            project_filename = build_project_filename(project['study_id'])
            zip_bytes = build_export_zip_bytes(
                artifacts,
                extra_files=[
                    GeneratedFile(
                        filename=project_filename,
                        content=project_json,
                    )
                ],
            )
            st.session_state['artifacts'] = artifacts
            st.session_state['project_json'] = project_json
            st.session_state['project'] = project
            st.session_state['last_generated_signature'] = current_signature
            st.success(
                f'Prepared {len(artifacts.dataset_files)} dataset files and {artifacts.sitemap_file.filename}.'
            )
        except MetadataValidationError as exc:
            st.error(f'Metadata validation failed: {exc}')

    artifacts = st.session_state.get('artifacts')
    project = cast(ExportProject | None, st.session_state.get('project'))
    last_generated_signature = cast(str | None, st.session_state.get('last_generated_signature'))
    has_pending_changes = (
        artifacts is not None
        and last_generated_signature is not None
        and current_signature != last_generated_signature
    )
    if validation_message is None and artifacts is None:
        st.info('Click Generate export to prepare preview and downloads.')
    elif has_pending_changes:
        st.info('Preview and downloads reflect the last generated export. Generate again to update them.')

    if artifacts is not None and project is not None:
        project_json = cast(str, st.session_state.get('project_json', ''))
        project_filename = build_project_filename(project['study_id'])
        zip_bytes = build_export_zip_bytes(
            artifacts,
            extra_files=[
                GeneratedFile(
                    filename=project_filename,
                    content=project_json,
                )
            ],
        )
        st.download_button(
            'Download Export ZIP',
            data=zip_bytes,
            file_name=build_export_archive_filename(project['study_id']),
            mime='application/zip',
            use_container_width=True,
        )
        st.download_button(
            'Download Project JSON',
            data=project_json,
            file_name=project_filename,
            mime='application/json',
            use_container_width=True,
        )

with preview_col:
    st.subheader('Preview')
    if not artifacts or project is None:
        st.info('Generate an export to preview the resulting files.')
    else:
        preview_options = [dataset_file.filename for dataset_file in artifacts.dataset_files]
        preview_filename = st.selectbox('Preview dataset file', options=preview_options)
        preview_file = next(
            dataset_file for dataset_file in artifacts.dataset_files if dataset_file.filename == preview_filename
        )
        preview_accession = preview_filename.removesuffix('.qmd')
        preview_dataset = build_preview_dataset_from_project(project, preview_accession)
        download_col, sitemap_download_col = st.columns(2, gap='small')
        with download_col:
            st.download_button(
                'Download Selected QMD',
                data=preview_file.content,
                file_name=preview_file.filename,
                mime='text/markdown',
                use_container_width=True,
            )
        with sitemap_download_col:
            st.download_button(
                'Download Sitemap XML',
                data=artifacts.sitemap_file.content,
                file_name=artifacts.sitemap_file.filename,
                mime='application/xml',
                use_container_width=True,
            )

        preview_tabs = st.tabs(['QMD', 'JSON-LD', 'Sitemap'])
        with preview_tabs[0]:
            st.code(preview_file.content, language='markdown')
        with preview_tabs[1]:
            st.json(preview_dataset, expanded=1)
        with preview_tabs[2]:
            st.code(artifacts.sitemap_file.content, language='xml')
