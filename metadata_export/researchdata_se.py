#!/usr/bin/env python3

"""CLI tool for exporting metadata to be harvested by https://researchdata.se."""

import argparse
import json
import sys
import xml.etree.ElementTree as ET

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


__author__ = 'Markus Englund'
__license__ = 'MIT'
__version__ = '0.1.0'
DEFAULT_TIMEOUT = 30
SITE_BASE_URL = 'https://fega.nbis.se'
SITEMAP_FILENAME = 'sitemap.xml'
SITEMAP_XMLNS = 'http://www.sitemaps.org/schemas/sitemap/0.9'


ORGANISATIONS = {
    'unspecified': {'@type': None, '@id': None, 'name': None},
    'FEGA-SE': {'@type': 'Organization', '@id': None, 'name': 'FEGA Sweden'},
    'LiU': {'@type': 'Organization', '@id': 'https://ror.org/05ynxx418', 'name': 'Linköping University'},
    'LU': {'@type': 'Organization', '@id': 'https://ror.org/012a77v79', 'name': 'Lund University'},
    'UU': {'@type': 'Organization', '@id': 'https://ror.org/048a87296', 'name': 'Uppsala University'},
    'BTB': {'@type': 'Organization', '@id': None, 'name': 'The Swedish Childhood Tumor Biobank'},
}


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

    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        url = f'{self.base_url}/{endpoint}'
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_entity(
        self,
        entity_type: str,
        accession_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Any:
        params: dict[str, Any] = {}
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
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        endpoint = entity_type
        if accession_id:
            endpoint += f'/{accession_id}/{related_entity_type}'
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset
        return self._get(endpoint, params=params)


def main(args: list[str] | None = None) -> int:
    if args is None:
        args = sys.argv[1:]
    parsed_args = parse_args(args)

    try:
        export_study_metadata(parsed_args)
    except requests.RequestException as exc:
        print(f'Failed to fetch metadata from the EGA API: {exc}', file=sys.stderr)
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
    parser.add_argument('--keywords', nargs='*', help='keywords describing the dataset')
    parser.add_argument('study_id', type=str, help='EGA Study accession number')
    parser.add_argument('output_dir', type=str, help='Path to the output directory')

    return parser.parse_args(args)


def export_study_metadata(args: argparse.Namespace) -> None:
    client = EGAClient()
    ega_study = client.get_entity('studies', accession_id=args.study_id)
    study_title = ega_study['title']
    study_url = build_study_identifier(ega_study['accession_id'])
    ega_datasets = client.get_related_entities(
        entity_type='studies',
        related_entity_type='datasets',
        accession_id=args.study_id,
    )
    output_dir = Path(args.output_dir)
    keywords = args.keywords or []

    if not output_dir.exists():
        print(f"Directory '{output_dir}' does not exist. Creating it...")
    output_dir.mkdir(parents=True, exist_ok=True)

    num_datasets = len(ega_datasets)
    sitemap_entries = []
    for ega_dataset in ega_datasets:
        dataset = transform_ega_dataset(
            ega_dataset,
            num_datasets=num_datasets,
            study_title=study_title,
            study_url=study_url,
            creator_org=args.creator,
            keywords=keywords,
        )
        filepath = output_dir / f'{ega_dataset["accession_id"]}.qmd'
        write_dataset_file(filepath, dataset)
        print(f'Wrote {filepath}')
        sitemap_entries.append(
            SitemapEntry(
                loc=build_dataset_page_url(ega_dataset['accession_id']),
                lastmod=dataset['datePublished'],
            )
        )

    sitemap_path = output_dir / SITEMAP_FILENAME
    write_sitemap_file(sitemap_path, sitemap_entries)
    print(f'Wrote {sitemap_path}')


def transform_ega_dataset(
    ega_dataset: dict[str, Any],
    num_datasets: int,
    study_title: str,
    study_url: str,
    creator_org: str | None = None,
    keywords: list[str] | None = None,
) -> dict[str, Any]:
    description = build_dataset_description(
        ega_dataset['description'],
        num_datasets=num_datasets,
        study_title=study_title,
        study_url=study_url,
    )
    dataset = {
        '@context': 'https://schema.org',
        '@type': 'Dataset',
        'identifier': build_dataset_identifier(ega_dataset['accession_id']),
        'name': ega_dataset['title'].strip(),
        'publisher': dict(ORGANISATIONS['FEGA-SE']),
        'datePublished': parse_iso_date(ega_dataset['released_date']),
        'description': description,
        'inLanguage': [{'@type': 'Language', 'identifier': 'en', 'name': 'English'}],
        'isPartOf': {
            '@id': study_url,
            'name': study_title,
        },
    }
    if creator_org not in (None, 'unspecified'):
        dataset['creator'] = dict(ORGANISATIONS[creator_org])
    if keywords:
        dataset['keywords'] = keywords
    return dataset


def build_dataset_identifier(accession_id: str) -> str:
    return f'http://identifiers.org/ega.dataset:{accession_id}'


def build_study_identifier(accession_id: str) -> str:
    return f'http://identifiers.org/ega.study:{accession_id}'


def build_dataset_page_url(accession_id: str) -> str:
    return f'{SITE_BASE_URL}/catalogue/datasets/{accession_id}.html'


def parse_iso_date(value: str) -> str:
    dt_published = datetime.fromisoformat(value.replace('Z', '+00:00'))
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


def write_dataset_file(filepath: Path, dataset: dict[str, Any]) -> None:
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


def compose_yaml_front_matter(dataset: dict[str, Any]) -> str:
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


def json_ld_as_string(dataset: dict[str, Any]) -> str:
    json_ld_str = (
        '<script type="application/ld+json">\n'
        + json.dumps(dataset, indent=2, ensure_ascii=False)
        + '\n</script>\n'
    )
    return json_ld_str


def yaml_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def indent_string(s: str, spaces: int = 8) -> str:
    indentation = ' ' * spaces
    return '\n'.join(indentation + line for line in s.splitlines())


def compose_markdown(dataset: dict[str, Any]) -> str:
    md = f"""\
{dataset['description']}

**Official landing page:**
<{dataset['identifier']}>
"""
    return md


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
