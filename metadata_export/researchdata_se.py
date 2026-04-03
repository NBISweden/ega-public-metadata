#!/usr/bin/env python3

"""CLI tool for exporting metadata to be harvested by https://researchdata.se."""

import argparse
import json
import sys
import xml.etree.ElementTree as ET

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypedDict, cast

import requests

__author__ = 'Markus Englund'
__license__ = 'MIT'
__version__ = '0.1.0'
DEFAULT_TIMEOUT = 30
DEFAULT_PAGE_SIZE = 100
DEFAULT_SITE_BASE_URL = 'https://fega.nbis.se'
DEFAULT_SITEMAP_FILENAME = 'sitemap.xml'
SITEMAP_XMLNS = 'http://www.sitemaps.org/schemas/sitemap/0.9'


ORGANISATIONS = {
    'unspecified': {'@type': None, '@id': None, 'name': None},
    'FEGA-SE': {'@type': 'Organization', '@id': None, 'name': 'FEGA Sweden'},
    'LiU': {'@type': 'Organization', '@id': 'https://ror.org/05ynxx418', 'name': 'Linköping University'},
    'LU': {'@type': 'Organization', '@id': 'https://ror.org/012a77v79', 'name': 'Lund University'},
    'UU': {'@type': 'Organization', '@id': 'https://ror.org/048a87296', 'name': 'Uppsala University'},
    'BTB': {'@type': 'Organization', '@id': None, 'name': 'The Swedish Childhood Tumor Biobank'},
}


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
    'datePublished': str,
    'description': str,
    'inLanguage': list[dict[str, str]],
    'isPartOf': dict[str, str],
    'creator': Organisation,
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
    in_language: list[dict[str, str]]
    creator: Organisation | None = None
    keywords: list[str] | None = None


@dataclass(frozen=True)
class StudyContext:
    title: str
    url: str
    datasets: list[EGADataset]


@dataclass(frozen=True)
class ExportedDataset:
    accession_id: str
    date_published: str
    file_path: Path
    page_url: str


@dataclass(frozen=True)
class SitemapEntry:
    loc: str
    lastmod: str


@dataclass(frozen=True)
class ExportConfig:
    site_base_url: str
    sitemap_filename: str


class MetadataValidationError(ValueError):
    """Raised when required metadata is missing or malformed."""


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
        prog='researchdata', description=(
            'A command-line utility for preparing FEGA Sweden metadata for researchdata.se'))
    parser.add_argument(
        '-V', '--version', action='version', version='%(prog)s ' + __version__)
    parser.add_argument(
        '--creator', choices=ORGANISATIONS.keys(), help='main organisation that collected the data')
    parser.add_argument(
        '--keyword',
        action='append',
        dest='keywords',
        metavar='KEYWORD',
        help='keyword describing the dataset; repeat the option for multiple keywords',
    )
    parser.add_argument(
        '--site-base-url',
        default=DEFAULT_SITE_BASE_URL,
        help='base URL for generated dataset landing pages',
    )
    parser.add_argument(
        '--sitemap-filename',
        default=DEFAULT_SITEMAP_FILENAME,
        help='filename for the generated sitemap XML',
    )
    parser.add_argument('study_id', type=str, help='EGA Study accession number')
    parser.add_argument('output_dir', type=str, help='Path to the output directory')

    return parser.parse_args(args)


def export_study_metadata(args: argparse.Namespace) -> None:
    export_config = ExportConfig(
        site_base_url=args.site_base_url.rstrip('/'),
        sitemap_filename=args.sitemap_filename,
    )
    with EGAClient() as client:
        study_context = fetch_study_context(client, args.study_id)
    output_dir = ensure_output_dir(args.output_dir)
    exported_datasets = export_dataset_files(
        study_context=study_context,
        output_dir=output_dir,
        creator_org=args.creator,
        keywords=args.keywords or [],
        export_config=export_config,
    )
    sitemap_entries = build_sitemap_entries(exported_datasets)

    sitemap_path = output_dir / export_config.sitemap_filename
    write_sitemap_file(sitemap_path, sitemap_entries)
    print(f'Wrote {sitemap_path}')


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


def export_dataset_files(
    study_context: StudyContext,
    output_dir: Path,
    creator_org: str | None,
    keywords: list[str],
    export_config: ExportConfig,
) -> list[ExportedDataset]:
    num_datasets = len(study_context.datasets)
    exported_datasets = []

    for ega_dataset in study_context.datasets:
        dataset = transform_ega_dataset(
            ega_dataset,
            num_datasets=num_datasets,
            study_title=study_context.title,
            study_url=study_context.url,
            creator_org=creator_org,
            keywords=keywords,
        )
        filepath = output_dir / f'{ega_dataset["accession_id"]}.qmd'
        write_dataset_file(filepath, dataset)
        print(f'Wrote {filepath}')
        exported_datasets.append(
            ExportedDataset(
                accession_id=ega_dataset['accession_id'],
                date_published=dataset['datePublished'],
                file_path=filepath,
                page_url=build_dataset_page_url(
                    ega_dataset['accession_id'],
                    export_config.site_base_url,
                ),
            )
        )

    return exported_datasets


def build_sitemap_entries(exported_datasets: list[ExportedDataset]) -> list[SitemapEntry]:
    return sorted([
        SitemapEntry(loc=dataset.page_url, lastmod=dataset.date_published)
        for dataset in exported_datasets
    ], key=lambda entry: entry.loc)


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
    normalized_value = value.strip()
    if not normalized_value:
        raise MetadataValidationError(
            f'{context} has empty required field "{field_name}"'
        )
    return normalized_value


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
    creator_org: str | None = None,
    keywords: list[str] | None = None,
) -> ResearchDataset:
    normalized = normalize_ega_dataset_metadata(
        accession_id=ega_dataset['accession_id'],
        title=ega_dataset['title'],
        released_date=ega_dataset['released_date'],
        description=ega_dataset['description'],
        study_title=study_title,
        study_url=study_url,
        num_datasets=num_datasets,
        creator_org=creator_org,
        keywords=keywords,
    )
    dataset: ResearchDataset = {
        '@context': 'https://schema.org',
        '@type': 'Dataset',
        'identifier': build_dataset_identifier(normalized.accession_id),
        'name': normalized.title,
        'publisher': normalized.publisher,
        'datePublished': normalized.date_published,
        'description': normalized.description,
        'inLanguage': normalized.in_language,
        'isPartOf': {
            '@id': normalized.study_identifier,
            'name': normalized.study_title,
        },
    }
    if normalized.creator:
        dataset['creator'] = normalized.creator
    if normalized.keywords:
        dataset['keywords'] = normalized.keywords
    return dataset


def build_dataset_identifier(accession_id: str) -> str:
    return f'http://identifiers.org/ega.dataset:{accession_id}'


def build_study_identifier(accession_id: str) -> str:
    return f'http://identifiers.org/ega.study:{accession_id}'


def build_dataset_page_url(accession_id: str, site_base_url: str) -> str:
    return f'{site_base_url}/catalogue/datasets/{accession_id}.html'


def normalize_ega_dataset_metadata(
    accession_id: str,
    title: str,
    released_date: str,
    description: str,
    study_title: str,
    study_url: str,
    num_datasets: int,
    creator_org: str | None = None,
    keywords: list[str] | None = None,
) -> NormalizedDatasetMetadata:
    creator = None
    if creator_org not in (None, 'unspecified'):
        creator = dict(ORGANISATIONS[creator_org])
    normalized_keywords = list(keywords or [])

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
        publisher=dict(ORGANISATIONS['FEGA-SE']),
        in_language=[{'@type': 'Language', 'identifier': 'en', 'name': 'English'}],
        creator=creator,
        keywords=normalized_keywords,
    )


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
    description = description.strip()
    dataset_label = 'dataset' if num_datasets == 1 else 'datasets'
    study_summary = (
        f'This dataset is one of {num_datasets} {dataset_label} included in the '
        f'study {study_title} ({study_url}).'
    )
    if description:
        return f'{description}\n\n{study_summary}'
    return study_summary


def write_dataset_file(filepath: Path, dataset: ResearchDataset) -> None:
    with filepath.open('w', encoding='utf-8') as file_handle:
        file_handle.write(compose_yaml_front_matter(dataset))
        file_handle.write(compose_markdown(dataset))


def write_sitemap_file(filepath: Path, entries: list[SitemapEntry]) -> None:
    urlset = ET.Element('urlset', xmlns=SITEMAP_XMLNS)
    for entry in entries:
        url_element = ET.SubElement(urlset, 'url')
        ET.SubElement(url_element, 'loc').text = entry.loc
        ET.SubElement(url_element, 'lastmod').text = entry.lastmod

    ET.indent(urlset, space='  ')
    tree = ET.ElementTree(urlset)
    tree.write(filepath, encoding='utf-8', xml_declaration=True)


def compose_yaml_front_matter(dataset: ResearchDataset) -> str:
    json_ld_str = json_ld_as_string(dataset)
    json_ld_indented_str = indent_string(json_ld_str)
    lines = [
        '---',
        f'title: {yaml_string(dataset["name"])}',
    ]
    creator_name = dataset.get('creator', {}).get('name')
    if creator_name:
        lines.append(f'author: {yaml_string(creator_name)}')
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
    json_ld_str = (
        '<script type="application/ld+json">\n'
        + json.dumps(dataset, indent=2, ensure_ascii=False)
        + '\n</script>\n'
    )
    return json_ld_str


def yaml_string(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def indent_string(s: str, spaces: int = 8) -> str:
    indentation = ' ' * spaces
    return '\n'.join(indentation + line for line in s.splitlines())


def compose_markdown(dataset: ResearchDataset) -> str:
    md = f"""\
{dataset['description']}

**Official landing page:**
<{dataset['identifier']}>
"""
    return md


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
