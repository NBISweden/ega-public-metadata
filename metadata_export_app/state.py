"""Pure helper functions for Streamlit app state and validation."""

from __future__ import annotations

import re

from collections.abc import MutableMapping

from metadata_export_core.core import ExportProject, PUBLISHER_ORGANISATIONS, StudyContext


SessionStateMapping = MutableMapping[str, object]


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
    session_state['site_base_url'] = project['site_base_url']
    session_state['sitemap_filename'] = project['sitemap_filename']
    session_state.pop('artifacts', None)
    session_state.pop('project_json', None)
    session_state.pop('project', None)
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


def find_selected_accessions_missing_keywords(
    study_context: StudyContext,
    selected_accessions: set[str],
    dataset_keywords_by_accession: dict[str, list[str]],
) -> list[str]:
    return [
        dataset['accession_id']
        for dataset in study_context.datasets
        if dataset['accession_id'] in selected_accessions
        and not dataset_keywords_by_accession[dataset['accession_id']]
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
