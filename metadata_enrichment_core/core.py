#!/usr/bin/env python3

"""Shared metadata export logic for both CLI and Streamlit flows."""

import argparse
import io
import json
import sys
import textwrap
import zipfile

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

import requests

__author__ = 'Markus Englund'
__license__ = 'MIT'
__version__ = '0.1.0'
DEFAULT_TIMEOUT = 30
DEFAULT_PAGE_SIZE = 100
DEFAULT_SITE_BASE_URL = 'https://fega.nbis.se'
DEFAULT_SITE_NAME = 'FEGA Sweden'


ORGANISATIONS = {
    'FEGA-SE': {'@type': 'Organization', '@id': None, 'name': 'FEGA Sweden'},
    'LiU': {'@type': 'Organization', '@id': 'https://ror.org/05ynxx418', 'name': 'Linköping University'},
    'LU': {'@type': 'Organization', '@id': 'https://ror.org/012a77v79', 'name': 'Lund University'},
    'UU': {'@type': 'Organization', '@id': 'https://ror.org/048a87296', 'name': 'Uppsala University'},
    'BTB': {'@type': 'Organization', '@id': None, 'name': 'The Swedish Childhood Tumor Biobank'},
}
PUBLISHER_ORGANISATIONS = tuple(
    organisation_key for organisation_key in ORGANISATIONS if organisation_key != 'FEGA-SE'
)
SOURCE_ORGANISATIONS = tuple(
    organisation_key
    for organisation_key, organisation in ORGANISATIONS.items()
    if organisation.get('@id') is not None
)


Organisation = TypedDict(
    'Organisation',
    {
        '@type': str | None,
        '@id': str | None,
        'name': str | None,
    },
)

EGAStudy = TypedDict(
    'EGAStudy',
    {
        'accession_id': str,
        'title': str,
    },
)

EGADataset = TypedDict(
    'EGADataset',
    {
        'accession_id': str,
        'title': str,
        'released_date': str,
        'description': str,
    },
)

ResearchDataset = TypedDict('ResearchDataset', {
    '@context': str,
    '@type': str,
    'identifier': str,
    'name': str,
    'publisher': Organisation,
    'includedInDataCatalog': dict[str, str],
    'sdPublisher': dict[str, str | None],
    'datePublished': str,
    'description': str,
    'inLanguage': list[dict[str, str]],
    'isPartOf': dict[str, str],
    'creator': list[Organisation],
    'sourceOrganization': list[Organisation],
    'keywords': list[str],
}, total=False)


@dataclass(frozen=True)
class NormalizedDatasetMetadata:
    accession_id: str
    title: str
    date_published: str
    description: str
    study_title: str
    study_identifier: str
    publisher: Organisation
    included_in_data_catalog: dict[str, str]
    sd_publisher: dict[str, str | None]
    in_language: list[dict[str, str]]
    creators: list[Organisation] | None = None
    source_organizations: list[Organisation] | None = None
    keywords: list[str] | None = None


@dataclass(frozen=True)
class StudyContext:
    title: str
    url: str
    datasets: list[EGADataset]


ExportProjectDataset = TypedDict(
    'ExportProjectDataset',
    {
        'accession_id': str,
        'include': bool,
        'keywords': list[str],
    },
)

ExportProject = TypedDict(
    'ExportProject',
    {
        'schema_version': int,
        'created_at': str,
        'study_id': str,
        'study_context': StudyContext,
        'creator_orgs': list[str],
        'source_orgs': list[str],
        'publisher_org': str,
        'global_keywords': list[str],
        'site_name': str,
        'site_base_url': str,
        'datasets': list[ExportProjectDataset],
    },
)

@dataclass(frozen=True)
class ExportConfig:
    site_name: str
    site_base_url: str


@dataclass(frozen=True)
class GeneratedFile:
    filename: str
    content: str


@dataclass(frozen=True)
class ExportArtifacts:
    dataset_files: list[GeneratedFile]


PROJECT_SCHEMA_VERSION = 4


class MetadataValidationError(ValueError):
    """Raised when required metadata is missing or malformed."""


def build_organisation(organisation_key: str) -> Organisation:
    return cast(
        Organisation,
        {key: value for key, value in ORGANISATIONS[organisation_key].items() if value is not None},
    )


class EGAClient:
    def __init__(
        self,
        base_url: str = 'https://metadata.ega-archive.org',
        timeout: int = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': f'researchdata-export/{__version__}',
        })

    def __enter__(self) -> 'EGAClient':
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self.session.close()

    def _get(self, endpoint: str, params: dict[str, int] | None = None) -> object:
        url = f'{self.base_url}/{endpoint}'
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _extract_collection_items(self, response_json: object) -> list[dict[str, object]]:
        if isinstance(response_json, list):
            return cast(list[dict[str, object]], response_json)
        if isinstance(response_json, dict):
            for key in ('results', 'items', 'data'):
                value = response_json.get(key)
                if isinstance(value, list):
                    return cast(list[dict[str, object]], value)
        raise TypeError('Expected a list response or a paginated object containing items')

    def _has_more_pages(
        self,
        response_json: object,
        offset: int,
        limit: int,
        items_in_page: int,
    ) -> bool:
        if items_in_page == 0:
            return False
        if isinstance(response_json, dict):
            next_page = response_json.get('next')
            if next_page:
                return True
            total_count = response_json.get('total', response_json.get('count'))
            if isinstance(total_count, int):
                return offset + items_in_page < total_count
        return items_in_page == limit

    def _get_paginated_collection(
        self,
        endpoint: str,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        current_offset = offset

        while True:
            response_json = self._get(endpoint, params={'limit': limit, 'offset': current_offset})
            page_items = self._extract_collection_items(response_json)
            items.extend(page_items)
            if not self._has_more_pages(response_json, current_offset, limit, len(page_items)):
                return items
            current_offset += len(page_items)

    def get_entity(
        self,
        entity_type: str,
        accession_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict[str, object]:
        params: dict[str, int] = {}
        endpoint = entity_type
        if accession_id:
            endpoint += f'/{accession_id}'
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset
        return self._get(endpoint, params=params)

    def get_related_entities(
        self,
        entity_type: str,
        related_entity_type: str,
        accession_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, object]]:
        params: dict[str, int] = {}
        endpoint = entity_type
        if accession_id:
            endpoint += f'/{accession_id}/{related_entity_type}'
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset
        if params:
            return self._extract_collection_items(self._get(endpoint, params=params))
        return self._get_paginated_collection(endpoint)


def main(args: list[str] | None = None) -> int:
    if args is None:
        args = sys.argv[1:]
    parsed_args = parse_args(args)

    try:
        export_study_metadata(parsed_args)
    except requests.RequestException as exc:
        print(f'Failed to fetch metadata from the EGA API: {exc}', file=sys.stderr)
        return 1
    except MetadataValidationError as exc:
        print(f'Metadata validation failed: {exc}', file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError) as exc:
        print(f'Failed to transform metadata: {exc}', file=sys.stderr)
        return 1
    return 0


def parse_args(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='researchdata',
        description='A command-line utility for preparing FEGA Sweden metadata for researchdata.se',
    )
    parser.add_argument(
        '-V', '--version', action='version', version='%(prog)s ' + __version__)
    parser.add_argument(
        '--creator',
        choices=ORGANISATIONS.keys(),
        required=True,
        action='append',
        help='main organisation that collected the data',
    )
    parser.add_argument(
        '--publisher',
        choices=PUBLISHER_ORGANISATIONS,
        required=True,
        help='organisation responsible for publishing the dataset metadata record',
    )
    parser.add_argument(
        '--keyword',
        action='append',
        dest='keywords',
        metavar='KEYWORD',
        required=True,
        help='keyword describing the dataset; repeat the option for multiple keywords',
    )
    parser.add_argument(
        '--site-base-url',
        default=DEFAULT_SITE_BASE_URL,
        help='base URL for generated dataset landing pages',
    )
    parser.add_argument('study_id', type=str, help='EGA Study accession number')
    parser.add_argument('output_dir', type=str, help='Path to the output directory')

    return parser.parse_args(args)


def export_study_metadata(args: argparse.Namespace) -> None:
    export_config = ExportConfig(
        site_name=DEFAULT_SITE_NAME,
        site_base_url=args.site_base_url.rstrip('/'),
    )
    with EGAClient() as client:
        study_context = fetch_study_context(client, args.study_id)
    artifacts = build_export_artifacts(
        study_context=study_context,
        creator_orgs=args.creator,
        source_orgs=None,
        publisher_org=args.publisher,
        export_config=export_config,
        default_keywords=args.keywords or [],
    )
    output_dir = ensure_output_dir(args.output_dir)
    write_export_artifacts(output_dir, artifacts)


def build_export_project(
    study_id: str,
    study_context: StudyContext,
    creator_orgs: list[str],
    publisher_org: str,
    export_config: ExportConfig,
    source_orgs: list[str] | None = None,
    global_keywords: list[str] | None = None,
    dataset_keywords_by_accession: dict[str, list[str]] | None = None,
    selected_accessions: set[str] | None = None,
) -> ExportProject:
    keywords_by_accession = dataset_keywords_by_accession or {}
    datasets: list[ExportProjectDataset] = []
    for dataset in study_context.datasets:
        accession_id = dataset['accession_id']
        datasets.append(
            ExportProjectDataset(
                accession_id=accession_id,
                include=selected_accessions is None or accession_id in selected_accessions,
                keywords=list(keywords_by_accession.get(accession_id, [])),
            )
        )
    return ExportProject(
        schema_version=PROJECT_SCHEMA_VERSION,
        created_at=datetime.now(timezone.utc).isoformat(),
        study_id=study_id,
        study_context=study_context,
        creator_orgs=list(creator_orgs),
        source_orgs=list(source_orgs or []),
        publisher_org=publisher_org,
        global_keywords=list(global_keywords or []),
        site_name=export_config.site_name,
        site_base_url=export_config.site_base_url,
        datasets=datasets,
    )


def serialize_export_project(project: ExportProject) -> str:
    return json.dumps(project, indent=2, ensure_ascii=False, default=_json_default)


def _json_default(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')


def deserialize_export_project(project_json: str) -> ExportProject:
    payload = json.loads(project_json)
    if not isinstance(payload, dict):
        raise MetadataValidationError('Project file must contain a JSON object')
    schema_version = payload.get('schema_version')
    if schema_version != PROJECT_SCHEMA_VERSION:
        raise MetadataValidationError(
            f'Unsupported project schema version "{schema_version}"'
        )
    study_id = require_non_empty_string(payload.get('study_id'), 'study_id', 'project file')
    creator_orgs_raw = payload.get('creator_orgs')
    if not isinstance(creator_orgs_raw, list) or not creator_orgs_raw:
        raise MetadataValidationError('Project file must include one or more creator_orgs')
    creator_orgs = [
        require_non_empty_string(creator_org, 'creator_orgs', 'project file')
        for creator_org in creator_orgs_raw
    ]
    source_orgs_raw = payload.get('source_orgs')
    if not isinstance(source_orgs_raw, list):
        raise MetadataValidationError('Project file source_orgs must be a list')
    source_orgs = [
        require_non_empty_string(source_org, 'source_orgs', 'project file')
        for source_org in source_orgs_raw
    ]
    invalid_source_orgs = [
        source_org for source_org in source_orgs
        if source_org not in SOURCE_ORGANISATIONS
    ]
    if invalid_source_orgs:
        raise MetadataValidationError(
            'Project file contains invalid source organization(s): '
            + ', '.join(invalid_source_orgs)
        )
    publisher_org = require_non_empty_string(payload.get('publisher_org'), 'publisher_org', 'project file')
    if publisher_org not in PUBLISHER_ORGANISATIONS:
        raise MetadataValidationError(
            f'Project file contains invalid publisher "{publisher_org}"'
        )
    global_keywords_raw = payload.get('global_keywords')
    if not isinstance(global_keywords_raw, list):
        raise MetadataValidationError('Project file global_keywords must be a list')
    global_keywords = [
        require_non_empty_string(keyword, 'global_keywords', 'project file')
        for keyword in global_keywords_raw
    ]
    site_name = require_non_empty_string(payload.get('site_name'), 'site_name', 'project file')
    site_base_url = require_non_empty_string(payload.get('site_base_url'), 'site_base_url', 'project file')
    raw_study_context = payload.get('study_context')
    if not isinstance(raw_study_context, dict):
        raise MetadataValidationError('Project file is missing study_context')
    raw_datasets = raw_study_context.get('datasets')
    if not isinstance(raw_datasets, list):
        raise MetadataValidationError('Project file study_context is missing datasets')
    study_context = StudyContext(
        title=require_non_empty_string(raw_study_context.get('title'), 'title', 'project study_context'),
        url=require_non_empty_string(raw_study_context.get('url'), 'url', 'project study_context'),
        datasets=parse_ega_dataset_collection(
            [cast(dict[str, object], dataset) for dataset in raw_datasets],
            study_id=study_id,
        ),
    )
    raw_project_datasets = payload.get('datasets')
    if not isinstance(raw_project_datasets, list):
        raise MetadataValidationError('Project file is missing datasets configuration')
    project_datasets: list[ExportProjectDataset] = []
    for dataset in raw_project_datasets:
        if not isinstance(dataset, dict):
            raise MetadataValidationError('Project file contains an invalid dataset entry')
        raw_keywords = dataset.get('keywords', [])
        if not isinstance(raw_keywords, list):
            raise MetadataValidationError('Project dataset keywords must be a list')
        project_datasets.append(
            ExportProjectDataset(
                accession_id=require_non_empty_string(
                    dataset.get('accession_id'),
                    'accession_id',
                    'project dataset',
                ),
                include=bool(dataset.get('include', False)),
                keywords=[
                    require_non_empty_string(keyword, 'keywords', 'project dataset')
                    for keyword in raw_keywords
                ],
            )
        )
    return ExportProject(
        schema_version=PROJECT_SCHEMA_VERSION,
        created_at=require_non_empty_string(payload.get('created_at'), 'created_at', 'project file'),
        study_id=study_id,
        study_context=study_context,
        creator_orgs=creator_orgs,
        source_orgs=source_orgs,
        publisher_org=publisher_org,
        global_keywords=global_keywords,
        site_name=site_name,
        site_base_url=site_base_url,
        datasets=project_datasets,
    )


def build_export_artifacts_from_project(project: ExportProject) -> ExportArtifacts:
    return build_export_artifacts(
        study_context=project['study_context'],
        creator_orgs=project['creator_orgs'],
        source_orgs=project['source_orgs'],
        publisher_org=project['publisher_org'],
        export_config=ExportConfig(
            site_name=project['site_name'],
            site_base_url=project['site_base_url'],
        ),
        default_keywords=project['global_keywords'],
        dataset_keywords_by_accession={
            dataset['accession_id']: dataset['keywords']
            for dataset in project['datasets']
        },
        selected_accessions={
            dataset['accession_id']
            for dataset in project['datasets']
            if dataset['include']
        },
    )


def fetch_study_context(client: EGAClient, study_id: str) -> StudyContext:
    raw_study = client.get_entity('studies', accession_id=study_id)
    raw_datasets = client.get_related_entities(
        entity_type='studies',
        related_entity_type='datasets',
        accession_id=study_id,
    )
    ega_study = parse_ega_study_response(raw_study, study_id=study_id)
    datasets = parse_ega_dataset_collection(raw_datasets, study_id=study_id)
    return StudyContext(
        title=ega_study['title'],
        url=build_study_identifier(ega_study['accession_id']),
        datasets=datasets,
    )


def ensure_output_dir(output_dir: str) -> Path:
    path = Path(output_dir)
    if not path.exists():
        print(f"Directory '{path}' does not exist. Creating it...")
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_export_artifacts(
    study_context: StudyContext,
    creator_orgs: list[str] | None,
    publisher_org: str,
    export_config: ExportConfig,
    source_orgs: list[str] | None = None,
    default_keywords: list[str] | None = None,
    dataset_keywords_by_accession: dict[str, list[str]] | None = None,
    selected_accessions: set[str] | None = None,
) -> ExportArtifacts:
    num_datasets = len(study_context.datasets)
    dataset_files: list[GeneratedFile] = []
    keywords_by_accession = dataset_keywords_by_accession or {}

    for ega_dataset in study_context.datasets:
        accession_id = ega_dataset['accession_id']
        if selected_accessions is not None and accession_id not in selected_accessions:
            continue
        keywords = merge_keywords(default_keywords or [], keywords_by_accession.get(accession_id, []))
        if not keywords:
            raise MetadataValidationError(
                f'dataset {accession_id} must include at least one keyword for Researchdata.se export'
            )
        dataset = transform_ega_dataset(
            ega_dataset=ega_dataset,
            num_datasets=num_datasets,
            study_title=study_context.title,
            study_url=study_context.url,
            creator_orgs=creator_orgs,
            source_orgs=source_orgs,
            publisher_org=publisher_org,
            keywords=keywords,
            site_name=export_config.site_name,
            site_base_url=export_config.site_base_url,
        )
        filename = f'{accession_id}.qmd'
        dataset_files.append(
            GeneratedFile(
                filename=filename,
                content=compose_dataset_document(dataset),
            )
        )
    return ExportArtifacts(dataset_files=dataset_files)


def write_export_artifacts(output_dir: Path, artifacts: ExportArtifacts) -> None:
    for dataset_file in artifacts.dataset_files:
        output_path = output_dir / dataset_file.filename
        output_path.write_text(dataset_file.content, encoding='utf-8')
        print(f'Wrote {output_path}')


def build_export_zip_bytes(
    artifacts: ExportArtifacts,
    extra_files: list[GeneratedFile] | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode='w', compression=zipfile.ZIP_DEFLATED) as archive:
        for dataset_file in artifacts.dataset_files:
            archive.writestr(dataset_file.filename, dataset_file.content)
        for extra_file in extra_files or []:
            archive.writestr(extra_file.filename, extra_file.content)
    return buffer.getvalue()


def parse_ega_study_response(response: dict[str, object], study_id: str) -> EGAStudy:
    return validate_ega_study(cast(EGAStudy, response), study_id=study_id)


def parse_ega_dataset_collection(
    response: list[dict[str, object]],
    study_id: str,
) -> list[EGADataset]:
    return [
        validate_ega_dataset(cast(EGADataset, dataset), study_id=study_id)
        for dataset in response
    ]


def require_non_empty_string(value: object, field_name: str, context: str) -> str:
    if not isinstance(value, str):
        raise MetadataValidationError(
            f'{context} is missing required string field "{field_name}"'
        )
    normalized_value = normalize_line_endings(value).strip()
    if not normalized_value:
        raise MetadataValidationError(
            f'{context} has empty required field "{field_name}"'
        )
    return normalized_value


def normalize_line_endings(value: str) -> str:
    return value.replace('\r\n', '\n').replace('\r', '\n')


def validate_ega_study(ega_study: EGAStudy, study_id: str) -> EGAStudy:
    return EGAStudy(
        accession_id=require_non_empty_string(
            ega_study.get('accession_id'),
            'accession_id',
            f'study {study_id}',
        ),
        title=require_non_empty_string(
            ega_study.get('title'),
            'title',
            f'study {study_id}',
        ),
    )


def validate_ega_dataset(ega_dataset: EGADataset, study_id: str) -> EGADataset:
    dataset_accession = require_non_empty_string(
        ega_dataset.get('accession_id'),
        'accession_id',
        f'dataset in study {study_id}',
    )
    dataset_context = f'dataset {dataset_accession}'
    return EGADataset(
        accession_id=dataset_accession,
        title=require_non_empty_string(
            ega_dataset.get('title'),
            'title',
            dataset_context,
        ),
        released_date=require_non_empty_string(
            ega_dataset.get('released_date'),
            'released_date',
            dataset_context,
        ),
        description=require_non_empty_string(
            ega_dataset.get('description'),
            'description',
            dataset_context,
        ),
    )


def transform_ega_dataset(
    ega_dataset: EGADataset,
    num_datasets: int,
    study_title: str,
    study_url: str,
    creator_orgs: list[str] | None = None,
    source_orgs: list[str] | None = None,
    publisher_org: str | None = None,
    keywords: list[str] | None = None,
    site_base_url: str = DEFAULT_SITE_BASE_URL,
    site_name: str = DEFAULT_SITE_NAME,
) -> ResearchDataset:
    normalized = normalize_ega_dataset_metadata(
        accession_id=ega_dataset['accession_id'],
        title=ega_dataset['title'],
        released_date=ega_dataset['released_date'],
        description=ega_dataset['description'],
        study_title=study_title,
        study_url=study_url,
        num_datasets=num_datasets,
        creator_orgs=creator_orgs,
        source_orgs=source_orgs,
        publisher_org=publisher_org,
        keywords=keywords,
        site_base_url=site_base_url,
        site_name=site_name,
    )
    dataset: ResearchDataset = {
        '@context': 'https://schema.org',
        '@type': 'Dataset',
        'identifier': build_dataset_identifier(normalized.accession_id),
        'name': normalized.title,
        'publisher': normalized.publisher,
        'includedInDataCatalog': normalized.included_in_data_catalog,
        'sdPublisher': normalized.sd_publisher,
        'datePublished': normalized.date_published,
        'description': normalized.description,
        'inLanguage': normalized.in_language,
        'isPartOf': {
            '@id': normalized.study_identifier,
            'name': normalized.study_title,
        },
    }
    if normalized.creators:
        dataset['creator'] = normalized.creators
    if normalized.source_organizations:
        dataset['sourceOrganization'] = normalized.source_organizations
    if normalized.keywords:
        dataset['keywords'] = normalized.keywords
    return dataset


def build_dataset_identifier(accession_id: str) -> str:
    return f'http://identifiers.org/ega.dataset:{accession_id}'


def build_study_identifier(accession_id: str) -> str:
    return f'http://identifiers.org/ega.study:{accession_id}'


def merge_keywords(global_keywords: list[str], local_keywords: list[str]) -> list[str]:
    merged_keywords: list[str] = []
    for keyword in [*global_keywords, *local_keywords]:
        if keyword not in merged_keywords:
            merged_keywords.append(keyword)
    return merged_keywords


def normalize_ega_dataset_metadata(
    accession_id: str,
    title: str,
    released_date: str,
    description: str,
    study_title: str,
    study_url: str,
    num_datasets: int,
    creator_orgs: list[str] | None = None,
    source_orgs: list[str] | None = None,
    publisher_org: str | None = None,
    keywords: list[str] | None = None,
    site_base_url: str = DEFAULT_SITE_BASE_URL,
    site_name: str = DEFAULT_SITE_NAME,
) -> NormalizedDatasetMetadata:
    creators = None
    if creator_orgs is not None:
        creators = [build_organisation(creator_org) for creator_org in creator_orgs]
    source_organizations = None
    if source_orgs:
        invalid_source_orgs = [
            source_org for source_org in source_orgs
            if source_org not in SOURCE_ORGANISATIONS
        ]
        if invalid_source_orgs:
            raise MetadataValidationError(
                'source organizations must be legal entities for Researchdata.se export: '
                + ', '.join(invalid_source_orgs)
            )
        source_organizations = [build_organisation(source_org) for source_org in source_orgs]
    if publisher_org is None:
        raise MetadataValidationError(
            'publisher must be specified for Researchdata.se export'
        )
    if publisher_org == 'FEGA-SE':
        raise MetadataValidationError(
            'FEGA-SE cannot be used as publisher for Researchdata.se export'
        )
    publisher = build_organisation(cast(str, publisher_org))
    normalized_keywords = list(keywords or [])
    if not normalized_keywords:
        raise MetadataValidationError(
            'keywords must be specified for Researchdata.se export'
        )

    return NormalizedDatasetMetadata(
        accession_id=accession_id,
        title=title.strip(),
        date_published=parse_iso_date(released_date),
        description=build_dataset_description(
            description,
            num_datasets=num_datasets,
            study_title=study_title,
            study_url=study_url,
        ),
        study_title=study_title,
        study_identifier=study_url,
        publisher=publisher,
        included_in_data_catalog=build_site_data_catalog(site_name, site_base_url),
        sd_publisher=build_site_sd_publisher(site_name, site_base_url),
        in_language=[{'@type': 'Language', 'identifier': 'en', 'name': 'English'}],
        creators=creators,
        source_organizations=source_organizations,
        keywords=normalized_keywords,
    )


def build_site_data_catalog(site_name: str, site_base_url: str) -> dict[str, str]:
    return {
        '@type': 'DataCatalog',
        '@id': site_base_url,
        'name': site_name,
        'url': site_base_url,
    }


def build_site_sd_publisher(site_name: str, site_base_url: str) -> dict[str, str | None]:
    publisher = dict(build_organisation('FEGA-SE'))
    publisher['name'] = site_name
    publisher['url'] = site_base_url
    return publisher


def parse_iso_date(value: str) -> str:
    try:
        dt_published = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise MetadataValidationError(
            f'Invalid released_date value "{value}"'
        ) from exc
    return dt_published.date().isoformat()


def build_dataset_description(
    description: str,
    num_datasets: int,
    study_title: str,
    study_url: str,
) -> str:
    description = normalize_line_endings(description).strip()
    if num_datasets == 1:
        study_summary = (
            f'This dataset is included in the study "{study_title}" ({study_url}).'
        )
    else:
        study_summary = (
            f'This dataset is one of {num_datasets} datasets included in the '
            f'study "{study_title}" ({study_url}).'
        )
    if description:
        return f'{description}\n\n{study_summary}'
    return study_summary


def write_dataset_file(filepath: Path, dataset: ResearchDataset) -> None:
    filepath.write_text(compose_dataset_document(dataset), encoding='utf-8')


def compose_dataset_document(dataset: ResearchDataset) -> str:
    return compose_yaml_front_matter(dataset) + compose_markdown(dataset)


def compose_yaml_front_matter(dataset: ResearchDataset) -> str:
    json_ld_str = json_ld_as_string(dataset)
    json_ld_indented_str = indent_string(json_ld_str)
    accession_id = extract_accession_id_from_identifier(dataset['identifier'])
    lines = [
        '---',
        f'title: {yaml_string(dataset["name"])}',
        f'accession: {accession_id}',
    ]
    publisher = dataset.get('publisher', {})
    publisher_name = publisher.get('name') if isinstance(publisher, dict) else None
    if publisher_name:
        lines.append(f'author: {yaml_string(publisher_name)}')
    lines.extend([
        f'date: {yaml_string(dataset["datePublished"])}',
        'description: Dataset',
    ])
    keywords = dataset.get('keywords', [])
    if keywords:
        lines.append('categories:')
        lines.extend(f'  - {yaml_string(keyword)}' for keyword in keywords)
    else:
        lines.append('categories: []')
    lines.extend([
        'format:',
        '  html:',
        '    include-in-header:',
        '      text: |',
        json_ld_indented_str,
        '---',
        '',
    ])
    return '\n'.join(lines)


def json_ld_as_string(dataset: ResearchDataset) -> str:
    return (
        '<script type="application/ld+json">\n'
        + json.dumps(dataset, indent=2, ensure_ascii=False)
        + '\n</script>\n'
    )


def yaml_string(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def extract_accession_id_from_identifier(identifier: object) -> str:
    if not isinstance(identifier, str) or ':' not in identifier:
        raise MetadataValidationError('Dataset identifier is missing or malformed')
    return identifier.rsplit(':', 1)[-1]


def indent_string(s: str, spaces: int = 8) -> str:
    indentation = ' ' * spaces
    return '\n'.join(indentation + line for line in s.splitlines())


def wrap_markdown_text(text: str, width: int = 88) -> str:
    wrapped_lines: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            wrapped_lines.append('')
            continue
        stripped_line = line.lstrip()
        if stripped_line.startswith(('#', '>', '-', '*', '<', '|')):
            wrapped_lines.append(line)
            continue
        wrapped_lines.append(textwrap.fill(
            line,
            width=width,
            break_long_words=False,
            break_on_hyphens=False,
        ))
    return '\n'.join(wrapped_lines)


def compose_markdown(dataset: ResearchDataset) -> str:
    description = dataset['description']
    study_url = dataset.get('isPartOf', {}).get('@id')
    if isinstance(study_url, str):
        description = description.replace(
            f'({study_url})',
            f'([{study_url}]({study_url}))',
        )
    description = wrap_markdown_text(description)
    return f"""\
{description}

**Official landing page:**
<{dataset['identifier']}>
"""
