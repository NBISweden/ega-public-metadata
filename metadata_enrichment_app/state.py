"""Pure helper functions for Streamlit app state and validation."""

from __future__ import annotations

from datetime import date
import json
import re

from collections.abc import MutableMapping

from metadata_enrichment_core.core import (
    DEFAULT_SITE_NAME,
    DEFAULT_SITEMAP_FILENAME,
    DEFAULT_SITE_BASE_URL,
    ExportProject,
    ORGANISATIONS,
    PUBLISHER_ORGANISATIONS,
    StudyContext,
    merge_keywords,
    transform_ega_dataset,
)


SessionStateMapping = MutableMapping[str, object]


def build_export_archive_filename(study_id: str) -> str:
    return f'fega-sweden-metadata-export-{study_id}.zip'


def build_project_filename(study_id: str) -> str:
    return f'fega-sweden-metadata-project-{study_id}.json'


def get_organisation_display_name(organisation_key: str) -> str:
    return str(ORGANISATIONS[organisation_key]['name'])


def clear_generated_export_state(session_state: SessionStateMapping) -> None:
    session_state.pop('artifacts', None)
    session_state.pop('project_json', None)
    session_state.pop('project', None)
    session_state.pop('last_generated_signature', None)


def build_export_request_signature(
    study_id: str,
    creator_orgs: list[str],
    publisher_org: str | None,
    site_name: str,
    site_base_url: str,
    sitemap_filename: str,
    sitemap_lastmod: str | None,
    global_keywords: list[str],
    dataset_keywords_by_accession: dict[str, list[str]],
    selected_accessions: set[str],
) -> str:
    return json.dumps(
        {
            'study_id': study_id,
            'creator_orgs': creator_orgs,
            'publisher_org': publisher_org,
            'site_name': site_name,
            'site_base_url': site_base_url,
            'sitemap_filename': sitemap_filename,
            'sitemap_lastmod': sitemap_lastmod,
            'global_keywords': global_keywords,
            'dataset_keywords_by_accession': dataset_keywords_by_accession,
            'selected_accessions': sorted(selected_accessions),
        },
        sort_keys=True,
    )


def initialize_form_state_defaults(session_state: SessionStateMapping) -> None:
    if 'creator_orgs' not in session_state:
        session_state['creator_orgs'] = []
    if 'publisher_org' not in session_state:
        session_state['publisher_org'] = None
    if 'global_keywords_raw' not in session_state:
        session_state['global_keywords_raw'] = ''
    if 'site_name' not in session_state:
        session_state['site_name'] = DEFAULT_SITE_NAME
    if 'site_base_url' not in session_state:
        session_state['site_base_url'] = DEFAULT_SITE_BASE_URL
    if 'sitemap_filename' not in session_state:
        session_state['sitemap_filename'] = DEFAULT_SITEMAP_FILENAME
    if 'use_sitemap_lastmod' not in session_state:
        session_state['use_sitemap_lastmod'] = False
    if 'sitemap_lastmod_date' not in session_state:
        session_state['sitemap_lastmod_date'] = date.today()


def parse_keywords(raw_value: str) -> list[str]:
    return [
        keyword
        for keyword in (
            part.strip()
            for part in re.split(r'[\n,]', raw_value)
        )
        if keyword
    ]


def initialize_dataset_state(study_context: StudyContext, session_state: SessionStateMapping) -> None:
    for dataset in study_context.datasets:
        accession_id = dataset['accession_id']
        include_key = f'include_{accession_id}'
        keywords_key = f'keywords_{accession_id}'
        if include_key not in session_state:
            session_state[include_key] = True
        if keywords_key not in session_state:
            session_state[keywords_key] = ''


def restore_project_to_session_state(
    project: ExportProject,
    session_state: SessionStateMapping,
) -> None:
    study_context = project['study_context']
    session_state['study_context'] = study_context
    session_state['loaded_study_id'] = project['study_id']
    session_state['creator_orgs'] = project['creator_orgs']
    session_state['publisher_org'] = project['publisher_org']
    session_state['global_keywords_raw'] = ', '.join(project.get('global_keywords', []))
    session_state['site_name'] = project.get('site_name', DEFAULT_SITE_NAME)
    session_state['site_base_url'] = project['site_base_url']
    session_state['sitemap_filename'] = project['sitemap_filename']
    sitemap_lastmod = project.get('sitemap_lastmod')
    session_state['use_sitemap_lastmod'] = sitemap_lastmod is not None
    session_state['sitemap_lastmod_date'] = (
        date.fromisoformat(sitemap_lastmod)
        if sitemap_lastmod is not None else date.today()
    )
    clear_generated_export_state(session_state)
    initialize_dataset_state(study_context, session_state)
    dataset_state = {
        dataset['accession_id']: dataset
        for dataset in project['datasets']
    }
    for dataset in study_context.datasets:
        accession_id = dataset['accession_id']
        project_dataset = dataset_state.get(accession_id)
        if project_dataset is None:
            session_state[f'include_{accession_id}'] = False
            session_state[f'keywords_{accession_id}'] = ''
            continue
        session_state[f'include_{accession_id}'] = project_dataset['include']
        session_state[f'keywords_{accession_id}'] = ', '.join(project_dataset['keywords'])


def build_preview_dataset_from_project(
    project: ExportProject,
    accession_id: str,
) -> dict[str, object]:
    study_context = project['study_context']
    dataset = next(
        dataset for dataset in study_context.datasets
        if dataset['accession_id'] == accession_id
    )
    project_dataset = next(
        dataset_config for dataset_config in project['datasets']
        if dataset_config['accession_id'] == accession_id
    )
    return transform_ega_dataset(
        ega_dataset=dataset,
        num_datasets=len(study_context.datasets),
        study_title=study_context.title,
        study_url=study_context.url,
        creator_orgs=project['creator_orgs'],
        publisher_org=project['publisher_org'],
        keywords=merge_keywords(project.get('global_keywords', []), project_dataset['keywords']),
        site_name=project.get('site_name', DEFAULT_SITE_NAME),
        site_base_url=project['site_base_url'],
    )


def collect_global_keywords(session_state: SessionStateMapping) -> list[str]:
    return parse_keywords(str(session_state.get('global_keywords_raw', '')))


def collect_sitemap_lastmod(session_state: SessionStateMapping) -> str | None:
    if not bool(session_state.get('use_sitemap_lastmod', False)):
        return None
    lastmod_date = session_state.get('sitemap_lastmod_date')
    if isinstance(lastmod_date, date):
        return lastmod_date.isoformat()
    return None


def collect_selected_accessions(
    study_context: StudyContext,
    session_state: SessionStateMapping,
) -> set[str]:
    return {
        dataset['accession_id']
        for dataset in study_context.datasets
        if bool(session_state.get(f"include_{dataset['accession_id']}", False))
    }


def collect_dataset_keywords_by_accession(
    study_context: StudyContext,
    session_state: SessionStateMapping,
) -> dict[str, list[str]]:
    return {
        dataset['accession_id']: parse_keywords(
            str(session_state.get(f"keywords_{dataset['accession_id']}", ''))
        )
        for dataset in study_context.datasets
    }


def collect_effective_dataset_keywords_by_accession(
    study_context: StudyContext,
    global_keywords: list[str],
    dataset_keywords_by_accession: dict[str, list[str]],
) -> dict[str, list[str]]:
    return {
        dataset['accession_id']: merge_keywords(
            global_keywords,
            dataset_keywords_by_accession[dataset['accession_id']],
        )
        for dataset in study_context.datasets
    }


def find_selected_accessions_missing_keywords(
    study_context: StudyContext,
    selected_accessions: set[str],
    effective_dataset_keywords_by_accession: dict[str, list[str]],
) -> list[str]:
    return [
        dataset['accession_id']
        for dataset in study_context.datasets
        if dataset['accession_id'] in selected_accessions
        and not effective_dataset_keywords_by_accession[dataset['accession_id']]
    ]


def get_export_validation_message(
    creator_orgs: list[str],
    publisher_org: str | None,
    selected_accessions: set[str],
    selected_accessions_missing_keywords: list[str],
) -> str | None:
    if not creator_orgs:
        return 'Select at least one creator before generating export files.'
    if not publisher_org:
        return 'Select a publisher before generating export files.'
    if not selected_accessions:
        return 'Select at least one dataset to include in the export.'
    if selected_accessions_missing_keywords:
        return (
            'Add at least one keyword for every selected dataset before generating export files. '
            f'Missing keywords for: {", ".join(selected_accessions_missing_keywords)}.'
        )
    return None


def get_publisher_select_index(publisher_org: str | None) -> int | None:
    if publisher_org in PUBLISHER_ORGANISATIONS:
        return list(PUBLISHER_ORGANISATIONS).index(publisher_org)
    return None
