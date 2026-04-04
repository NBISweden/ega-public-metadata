"""Streamlit UI for FEGA Sweden Metadata Enrichment."""

from __future__ import annotations
import hashlib
import json
import sys

from pathlib import Path
from typing import cast

import requests
import streamlit as st

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from metadata_enrichment_core.core import (
    DEFAULT_SITE_NAME,
    DEFAULT_SITE_BASE_URL,
    EGAClient,
    ExportConfig,
    ExportProject,
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
from metadata_enrichment_app.state import (
    build_export_archive_filename,
    build_export_request_signature,
    build_preview_dataset_from_project,
    build_project_filename,
    clear_study_workflow_state,
    collect_effective_dataset_keywords_by_accession,
    collect_dataset_keywords_by_accession,
    collect_global_keywords,
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


def render_progress_overview(step_statuses: list[tuple[str, bool]]) -> None:
    completed_steps = sum(1 for _, is_complete in step_statuses if is_complete)
    total_steps = len(step_statuses)
    st.progress(completed_steps / total_steps, text=f'{completed_steps} of {total_steps} steps complete')
    status_lines = []
    for step_number, (label, is_complete) in enumerate(step_statuses, start=1):
        checkbox = 'x' if is_complete else ' '
        status_lines.append(f'- [{checkbox}] Step {step_number}. {label}')
    st.markdown('\n'.join(status_lines))


def render_export_checklist(checklist_items: list[tuple[str, bool]]) -> None:
    checklist_lines = []
    for label, is_complete in checklist_items:
        checkbox = 'x' if is_complete else ' '
        checklist_lines.append(f'- [{checkbox}] {label}')
    st.markdown('\n'.join(checklist_lines))


def render_sidebar_panel(
    step_statuses: list[tuple[str, bool]],
    next_step_message: str,
    project: ExportProject | None = None,
    artifacts: object = None,
) -> None:
    with st.sidebar:
        st.subheader('Progress')
        render_progress_overview(step_statuses)
        st.info(f'Next step: {next_step_message}')

        if artifacts is not None and project is not None:
            project_json = cast(str, st.session_state.get('project_json', ''))
            project_filename = build_project_filename(project['study_id'])
            sidebar_zip_bytes = build_export_zip_bytes(artifacts)
            st.subheader('Downloads')
            st.download_button(
                'Download Export ZIP',
                data=sidebar_zip_bytes,
                file_name=build_export_archive_filename(project['study_id']),
                mime='application/zip',
                on_click=mark_generated_export_downloaded,
                use_container_width=True,
            )
            st.download_button(
                'Download Project JSON',
                data=project_json,
                file_name=project_filename,
                mime='application/json',
                on_click=mark_generated_export_downloaded,
                use_container_width=True,
            )


def rerun_app() -> None:
    rerun = getattr(st, 'rerun', None)
    if callable(rerun):
        rerun()
        return
    st.experimental_rerun()


def mark_generated_export_downloaded() -> None:
    last_generated_signature = cast(str | None, st.session_state.get('last_generated_signature'))
    if last_generated_signature is not None:
        st.session_state['last_downloaded_signature'] = last_generated_signature


def replace_loaded_study(study_id: str) -> None:
    fetched_study_context = load_study_context(study_id)
    clear_study_workflow_state(st.session_state)
    st.session_state['study_context'] = fetched_study_context
    st.session_state['loaded_study_id'] = study_id


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
needed for publication, and generate Quarto `.qmd` files together with a saved project snapshot.
"""
)

uploaded_project = st.file_uploader(
    'Optional: Load saved export project',
    type=['json'],
    help='Load a previously saved project snapshot to regenerate or update export files.',
)
if uploaded_project is not None:
    uploaded_project_bytes = uploaded_project.getvalue()
    uploaded_project_signature = hashlib.sha256(uploaded_project_bytes).hexdigest()
    try:
        if st.session_state.get('loaded_project_upload_signature') != uploaded_project_signature:
            project = deserialize_export_project(uploaded_project_bytes.decode('utf-8'))
            restore_project_to_session_state(project, st.session_state)
            st.session_state['loaded_project_upload_signature'] = uploaded_project_signature
            st.success(f'Loaded project for study {project["study_id"]}.')
    except (UnicodeDecodeError, MetadataValidationError, json.JSONDecodeError) as exc:
        st.session_state.pop('loaded_project_upload_signature', None)
        st.error(f'Failed to load project file: {exc}')
else:
    st.session_state.pop('loaded_project_upload_signature', None)

generate_success_message = cast(str | None, st.session_state.pop('generate_success_message', None))
if generate_success_message:
    st.success(generate_success_message)

st.subheader('Step 1. Fetch Study Metadata')
st.caption('Start with a study accession and fetch its current metadata from EGA.')
with st.form('study_lookup'):
    study_id = st.text_input('EGA Study ID', placeholder='EGAS50000000906')
    fetch_clicked = st.form_submit_button('Fetch Study From EGA', use_container_width=True)

if fetch_clicked:
    if not study_id.strip():
        st.error('Enter an EGA Study ID first.')
    else:
        requested_study_id = study_id.strip()
        loaded_study_id = cast(str | None, st.session_state.get('loaded_study_id'))
        if loaded_study_id is not None:
            st.session_state['pending_fetch_study_id'] = requested_study_id
            st.session_state['pending_fetch_from_study_id'] = loaded_study_id
        else:
            try:
                replace_loaded_study(requested_study_id)
                rerun_app()
            except requests.RequestException as exc:
                st.error(f'Failed to fetch metadata from the EGA API: {exc}')
            except MetadataValidationError as exc:
                st.error(f'Metadata validation failed: {exc}')
            except (KeyError, TypeError, ValueError) as exc:
                st.error(f'Failed to transform metadata: {exc}')

pending_fetch_study_id = cast(str | None, st.session_state.get('pending_fetch_study_id'))
pending_fetch_from_study_id = cast(str | None, st.session_state.get('pending_fetch_from_study_id'))
if pending_fetch_study_id is not None:
    warning_message = (
        'Fetching a new study will clear the current study workflow, including creators, '
        'publisher, keywords, dataset selection, preview, and generated files.'
    )
    if pending_fetch_from_study_id:
        warning_message += f' Current study: `{pending_fetch_from_study_id}`.'
    warning_message += f' New study: `{pending_fetch_study_id}`.'
    st.warning(warning_message)
    confirm_col, cancel_col = st.columns(2, gap='small')
    with confirm_col:
        confirm_fetch = st.button(
            'Continue And Replace Current Study',
            type='primary',
            use_container_width=True,
        )
    with cancel_col:
        cancel_fetch = st.button(
            'Cancel Fetch',
            use_container_width=True,
        )

    if confirm_fetch:
        try:
            replace_loaded_study(pending_fetch_study_id)
            rerun_app()
        except requests.RequestException as exc:
            st.error(f'Failed to fetch metadata from the EGA API: {exc}')
        except MetadataValidationError as exc:
            st.error(f'Metadata validation failed: {exc}')
        except (KeyError, TypeError, ValueError) as exc:
            st.error(f'Failed to transform metadata: {exc}')
    elif cancel_fetch:
        st.session_state.pop('pending_fetch_study_id', None)
        st.session_state.pop('pending_fetch_from_study_id', None)
        rerun_app()

study_context = cast(StudyContext | None, st.session_state.get('study_context'))

if study_context is None:
    render_sidebar_panel(
        step_statuses=[
        ('Fetch study metadata from EGA', False),
        ('Fill in study-level metadata', False),
        ('Select datasets and add keywords', False),
        ('Generate export files', False),
        ('Preview and download files', False),
        ],
        next_step_message='Enter an EGA Study ID and click Fetch Study From EGA.',
    )
    st.info('Steps 2-5 become available after you fetch study metadata from EGA.')
    st.stop()

initialize_dataset_state(study_context, st.session_state)
initialize_form_state_defaults(st.session_state)
has_custom_site_settings = (
    cast(str, st.session_state.get('site_name', DEFAULT_SITE_NAME)).strip() != DEFAULT_SITE_NAME
    or cast(str, st.session_state.get('site_base_url', DEFAULT_SITE_BASE_URL)).rstrip('/')
    != DEFAULT_SITE_BASE_URL
)

creator_orgs = cast(list[str], st.session_state.get('creator_orgs', []))
publisher_org = cast(str | None, st.session_state.get('publisher_org'))
site_name = cast(str, st.session_state.get('site_name', DEFAULT_SITE_NAME))
site_base_url = cast(str, st.session_state.get('site_base_url', DEFAULT_SITE_BASE_URL))
global_keywords = collect_global_keywords(st.session_state)

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
artifacts = st.session_state.get('artifacts')
project = cast(ExportProject | None, st.session_state.get('project'))
workflow_ready_for_study_metadata = bool(creator_orgs) and publisher_org is not None
workflow_ready_for_datasets = bool(selected_accessions) and not selected_accessions_missing_keywords
current_signature = build_export_request_signature(
    study_id=cast(str, st.session_state.get('loaded_study_id', '')),
    creator_orgs=creator_orgs,
    publisher_org=publisher_org,
    site_name=site_name.strip() or DEFAULT_SITE_NAME,
    site_base_url=site_base_url.rstrip('/'),
    global_keywords=global_keywords,
    dataset_keywords_by_accession=dataset_keywords_by_accession,
    selected_accessions=selected_accessions,
)
last_generated_signature = cast(str | None, st.session_state.get('last_generated_signature'))
last_downloaded_signature = cast(str | None, st.session_state.get('last_downloaded_signature'))
has_pending_changes = (
    artifacts is not None
    and last_generated_signature is not None
    and current_signature != last_generated_signature
)
has_downloaded_current_export = (
    last_generated_signature is not None
    and last_downloaded_signature == last_generated_signature
)

next_step_message = 'Review the preview and download the files you need.'
if not creator_orgs:
    next_step_message = 'Select at least one creator.'
elif publisher_org is None:
    next_step_message = 'Select a publisher.'
elif not selected_accessions:
    next_step_message = 'Select at least one dataset to include.'
elif selected_accessions_missing_keywords:
    next_step_message = 'Add keywords for: ' + ', '.join(selected_accessions_missing_keywords) + '.'
elif artifacts is None:
    next_step_message = 'Click Generate Export Files.'
elif has_pending_changes:
    next_step_message = 'Click Generate Export Files again to refresh preview and downloads.'
elif not has_downloaded_current_export:
    next_step_message = 'Download at least one file to complete the workflow.'
else:
    next_step_message = 'Workflow complete.'

render_sidebar_panel(
    step_statuses=[
        ('Fetch study metadata from EGA', study_context is not None),
        ('Fill in study-level metadata', workflow_ready_for_study_metadata),
        ('Select datasets and add keywords', workflow_ready_for_datasets),
        ('Generate export files', artifacts is not None and not has_pending_changes),
        (
            'Preview and download files',
            artifacts is not None
            and project is not None
            and not has_pending_changes
            and has_downloaded_current_export
        ),
    ],
    next_step_message=next_step_message,
    project=project,
    artifacts=artifacts,
)

st.markdown('**Loaded Study**')
st.caption('Confirm that the loaded study matches what you expect before continuing.')
st.markdown(f"**Study ID**  \n`{st.session_state.get('loaded_study_id', '')}`")
summary_col, details_col = st.columns([1, 2], gap='large')
with summary_col:
    st.metric('Datasets', len(study_context.datasets))
with details_col:
    st.markdown(f'**Study title**  \n{study_context.title}')
    st.markdown(f'**Study identifier**  \n`{study_context.url}`')

st.divider()
st.subheader('Step 2. Fill In Study-Level Metadata')
st.caption('Creators and publisher are required. Global keywords are optional but can save time.')
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
st.caption(
    f'Site metadata defaults to `{DEFAULT_SITE_NAME}` at `{DEFAULT_SITE_BASE_URL}`. '
    'Change it only if this export targets another site.'
)
with st.expander('Advanced site settings', expanded=has_custom_site_settings):
    st.text_input(
        'Site name',
        key='site_name',
        help='Used as the name for includedInDataCatalog and sdPublisher.',
    )
    st.text_input(
        'Site base URL',
        key='site_base_url',
    )
st.text_area(
    'Global keywords',
    key='global_keywords_raw',
    help='Keywords applied to every included dataset. Use commas or new lines.',
)

st.divider()
st.subheader('Step 3. Select Datasets And Add Keywords')
st.caption(
    'Choose which datasets to include in the export. '
    'Additional keywords can be specified per dataset. '
    'Each selected dataset must end up with at least one keyword from global keywords, '
    'dataset-specific keywords, or both.'
)
selection_button_cols = st.columns(2, gap='small')
with selection_button_cols[0]:
    if st.button('Select all', use_container_width=True):
        for dataset in study_context.datasets:
            st.session_state[f"include_{dataset['accession_id']}"] = True
        rerun_app()
with selection_button_cols[1]:
    if st.button('Select none', use_container_width=True):
        for dataset in study_context.datasets:
            st.session_state[f"include_{dataset['accession_id']}"] = False
        rerun_app()

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
workflow_ready_for_datasets = bool(selected_accessions) and not selected_accessions_missing_keywords
current_signature = build_export_request_signature(
    study_id=cast(str, st.session_state.get('loaded_study_id', '')),
    creator_orgs=creator_orgs,
    publisher_org=publisher_org,
    site_name=site_name.strip() or DEFAULT_SITE_NAME,
    site_base_url=site_base_url.rstrip('/'),
    global_keywords=global_keywords,
    dataset_keywords_by_accession=dataset_keywords_by_accession,
    selected_accessions=selected_accessions,
)
last_generated_signature = cast(str | None, st.session_state.get('last_generated_signature'))
has_pending_changes = (
    artifacts is not None
    and last_generated_signature is not None
    and current_signature != last_generated_signature
)

st.divider()
st.subheader('Step 4. Generate Export')
st.caption('Generate export files when all required metadata is in place.')
st.markdown('**Checklist before export**')
render_export_checklist([
    ('At least one creator selected', bool(creator_orgs)),
    ('Publisher selected', publisher_org is not None),
    ('At least one dataset selected', bool(selected_accessions)),
    ('All selected datasets have keywords', not selected_accessions_missing_keywords and bool(selected_accessions)),
])
validation_message = get_export_validation_message(
    creator_orgs=creator_orgs,
    publisher_org=publisher_org,
    selected_accessions=selected_accessions,
    selected_accessions_missing_keywords=selected_accessions_missing_keywords,
)
generate_clicked = st.button(
    'Generate Export Files',
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
        st.session_state['artifacts'] = artifacts
        st.session_state['project_json'] = project_json
        st.session_state['project'] = project
        st.session_state['last_generated_signature'] = current_signature
        st.session_state.pop('last_downloaded_signature', None)
        st.session_state['generate_success_message'] = (
            f'Prepared {len(artifacts.dataset_files)} dataset files.'
        )
        rerun_app()
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

st.divider()
st.subheader('Step 5. Preview And Download Files')
st.caption('Preview and download options appear after export files have been generated.')
if not artifacts or project is None:
    st.info('No export generated yet. Complete steps 1-4 and click Generate Export Files.')
else:
    project_json = cast(str, st.session_state.get('project_json', ''))
    project_filename = build_project_filename(project['study_id'])
    preview_options = [dataset_file.filename for dataset_file in artifacts.dataset_files]
    preview_filename = st.selectbox('Preview dataset file', options=preview_options)
    preview_file = next(
        dataset_file for dataset_file in artifacts.dataset_files if dataset_file.filename == preview_filename
    )
    preview_accession = preview_filename.removesuffix('.qmd')
    preview_dataset = build_preview_dataset_from_project(project, preview_accession)
    st.download_button(
        'Download Selected QMD',
        data=preview_file.content,
        file_name=preview_file.filename,
        mime='text/markdown',
        on_click=mark_generated_export_downloaded,
        use_container_width=True,
    )

    preview_tabs = st.tabs(['QMD', 'JSON-LD'])
    with preview_tabs[0]:
        st.code(preview_file.content, language='markdown')
    with preview_tabs[1]:
        st.json(preview_dataset, expanded=1)
