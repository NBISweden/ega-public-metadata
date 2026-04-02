#!/usr/bin/env python3

import argparse
import json
import sys

from datetime import datetime
from pathlib import Path

import requests


"""
CLI tool for exporting metadata to be harvested
by https://researchdata.se
"""


__author__ = 'Markus Englund'
__license__ = 'MIT'
__version__ = '0.1.0'
DEFAULT_TIMEOUT = 30


ORGANISATIONS = {
    'unspecified': {'@type': None, '@id': None, 'name': None},
    'FEGA-SE': {'@type': 'Organization', '@id': None, 'name': 'FEGA Sweden'},
    'LiU': {'@type': 'Organization', '@id': 'https://ror.org/05ynxx418', 'name': 'Linköping University'},
    'LU': {'@type': 'Organization', '@id': 'https://ror.org/012a77v79', 'name': 'Lund University'},
    'UU': {'@type': 'Organization', '@id': 'https://ror.org/048a87296', 'name': 'Uppsala University'},
    'BTB': {'@type': 'Organization', '@id': None, 'name': 'The Swedish Childhood Tumor Biobank'},
}


class EGAClient:
    def __init__(self, base_url='https://metadata.ega-archive.org', timeout=DEFAULT_TIMEOUT, session=None):
        self.base_url = base_url
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            'Accept': 'application/json',
            'User-Agent': f'researchdata-export/{__version__}',
        })

    def _get(self, endpoint, params=None):
        url = f'{self.base_url}/{endpoint}'
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_entity(self, entity_type, accession_id=None, limit=None, 
                   offset=None):
        params = {}
        endpoint = entity_type
        if accession_id:
            endpoint += f'/{accession_id}'
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset
        return self._get(endpoint, params=params)

    def get_related_entities(self, entity_type, related_entity_type, 
                             accession_id, limit=None, offset=None):
        params = {}
        endpoint = entity_type
        if accession_id:
            endpoint += f'/{accession_id}/{related_entity_type}'
        if limit is not None:
            params['limit'] = limit
        if offset is not None:
            params['offset'] = offset
        return self._get(endpoint, params=params)


def main(args=None):
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


def parse_args(args):
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


def export_study_metadata(args):
    client = EGAClient()
    ega_study = client.get_entity('studies', accession_id=args.study_id)
    study_title = ega_study['title']
    study_url = 'http://identifiers.org/ega.study:' + ega_study['accession_id']
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
        print(compose_url_xml_entry(ega_dataset['accession_id']))


def transform_ega_dataset(ega_dataset, num_datasets, study_title, study_url, creator_org=None, keywords=None):
    dataset = {
        "@context":"https://schema.org/",
        "@type":"Dataset",
    }
    dataset['identifier'] = 'http://identifiers.org/ega.dataset:' + ega_dataset['accession_id']
    if creator_org is not None:
        dataset['creator'] = ORGANISATIONS[creator_org]
    else:
        dataset['creator'] = ORGANISATIONS['unspecified']
    dataset['name'] = ega_dataset['title']
    dataset['publisher'] = ORGANISATIONS['FEGA-SE']
    dt_published =  datetime.fromisoformat(ega_dataset['released_date'])
    dataset['datePublished'] = dt_published.date().isoformat()
    if keywords is not None:
        dataset['keywords'] = keywords
    else:
        dataset['keywords'] = None
    dataset['inLanguge'] = [{ "@type": "Language", "identifier": "en", "name": "English" }]
    dataset['licence'] = dataset['identifier']
    dataset['description'] = ' '.join(
        [ega_dataset['description'].strip(),
         f'\n\nThis dataset is 1 of {num_datasets} included in the study titled {study_title}, {study_url}.']
    )
    return dataset


def write_dataset_file(filepath, dataset):
    with filepath.open('w', encoding='utf-8') as file_handle:
        file_handle.write(compose_yaml_front_matter(dataset))
        file_handle.write(compose_markdown(dataset))


def compose_yaml_front_matter(dataset):
    categories_str = '\n'.join([f'  - {kw}' for kw in dataset['keywords']])
    json_ld_str = json_ld_as_string(dataset)
    json_ld_indented_str = indent_string(json_ld_str)
    fm = f"""\
---
title: {dataset['name']}
author: {dataset['creator']['name']}
date: {dataset['datePublished']}
description: Dataset
categories:
{categories_str}
format:
  html:
    include-in-header:
      text: |
{json_ld_indented_str}
---
"""
    return fm


def json_ld_as_string(dataset):
    json_ld_str = (
        '<script type="application/ld+json">\n'
        + json.dumps(dataset, indent=4)
        + '\n</script>\n'
    )
    return json_ld_str

def indent_string(s: str, spaces: int = 8) -> str:
    indentation = ' ' * spaces
    return '\n'.join(indentation + line for line in s.splitlines())


def compose_markdown(dataset):
    md = f"""\
{dataset['description']}

**Official landing page:**
<{dataset['identifier']}>
"""
    return md


def compose_url_xml_entry(accession_id):
    url_xml_entry = f"""\
  <url>
    <loc>https://fega.nbis.se/catalogue/datasets/{accession_id}.html</loc>
  </url>
  """
    return url_xml_entry


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
