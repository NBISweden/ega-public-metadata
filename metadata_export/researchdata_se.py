#!/usr/bin/env python3

import argparse
import json
import os
import sys

from datetime import datetime

import requests


"""
CLI tool for exporting metadata to be harvested
by https://researchdata.se
"""


__author__ = 'Markus Englund'
__license__ = 'MIT'
__version__ = '0.1.0'


ORGANISATIONS = {
    'unspecified': {'@type': None, '@id': None, 'name': None},
    'FEGA-SE': {'@type': 'Organization', '@id': None, 'name': 'FEGA Sweden'},
    'LiU': {'@type': 'Organization', '@id': 'https://ror.org/05ynxx418', 'name': 'Linköping University'},
    'LU': {'@type': 'Organization', '@id': 'https://ror.org/012a77v79', 'name': 'Lund University'},
    'UU': {'@type': 'Organization', '@id': 'https://ror.org/048a87296', 'name': 'Uppsala University'},
    'BTB': {'@type': 'Organization', '@id': None, 'name': 'The Swedish Childhood Tumor Biobank'},
}


class EGAClient:
    def __init__(self, base_url='https://metadata.ega-archive.org'):
        self.base_url = base_url

    def _get(self, endpoint, params=None):
        url = f'{self.base_url}/{endpoint}'
        response = requests.get(url, params=params)
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
    parser = parse_args(args)

    client = EGAClient()
    ega_study = client.get_entity('studies', accession_id=parser.study_id)
    study_title = ega_study['title']
    study_url = 'http://identifiers.org/ega.study:' + ega_study['accession_id']
    ega_datasets = client.get_related_entities(
        entity_type='studies', related_entity_type='datasets', accession_id=parser.study_id)
    num_datasets = len(ega_datasets)
    output_dir = parser.output_dir

    # Check if directory exists, if not create it
    if not os.path.exists(output_dir):
        print(f"Directory '{output_dir}' does not exist. Creating it...")
        os.makedirs(output_dir, exist_ok=True)

    for ega_dataset in ega_datasets:
        dataset = transform_ega_dataset(
            ega_dataset, num_datasets=num_datasets, study_title=study_title,
            study_url=study_url, creator_org=parser.creator, keywords=parser.keywords)
        filename = f'{ega_dataset["accession_id"]}.qmd'
        filepath = os.path.join(output_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            fm = compose_yaml_front_matter(dataset)
            f.write(fm)
            md = compose_markdown(dataset)
            f.write(md)
            print(compose_url_xml_entry(ega_dataset["accession_id"]))


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
    main()
