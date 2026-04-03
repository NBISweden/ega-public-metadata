import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from metadata_export.researchdata_se import (
    EGAClient,
    ExportConfig,
    ExportedDataset,
    GeneratedFile,
    MetadataValidationError,
    StudyContext,
    build_export_artifacts,
    build_export_artifacts_from_project,
    build_export_project,
    build_export_zip_bytes,
    build_sitemap_entries,
    compose_markdown,
    compose_sitemap_xml,
    compose_yaml_front_matter,
    deserialize_export_project,
    export_study_metadata,
    fetch_study_context,
    main,
    normalize_ega_dataset_metadata,
    parse_args,
    serialize_export_project,
    validate_ega_dataset,
    validate_ega_study,
    transform_ega_dataset,
    write_sitemap_file,
)


SITEMAP_XMLNS = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
GOLDEN_EXPORT_DIR = Path(__file__).resolve().parent / 'fixtures' / 'golden_export'


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []
        self.closed = False

    def get(self, url, params=None, timeout=None):
        self.calls.append({
            'url': url,
            'params': params,
            'timeout': timeout,
        })
        return FakeResponse(self.responses.pop(0))

    def close(self) -> None:
        self.closed = True


class FakeAPIClient:
    def __init__(self, study_payload, dataset_payload):
        self.study_payload = study_payload
        self.dataset_payload = dataset_payload
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def get_entity(self, entity_type, accession_id=None, limit=None, offset=None):
        self.calls.append(('get_entity', entity_type, accession_id, limit, offset))
        return self.study_payload

    def get_related_entities(
        self,
        entity_type,
        related_entity_type,
        accession_id,
        limit=None,
        offset=None,
    ):
        self.calls.append((
            'get_related_entities',
            entity_type,
            related_entity_type,
            accession_id,
            limit,
            offset,
        ))
        return self.dataset_payload


class ResearchDataExportTests(unittest.TestCase):
    def test_parse_args_accepts_repeated_keywords_and_export_config(self) -> None:
        args = parse_args([
            '--creator', 'UU',
            '--publisher', 'LU',
            '--keyword', 'genomics',
            '--keyword', 'reference dataset',
            '--site-base-url', 'https://example.org',
            '--sitemap-filename', 'custom-sitemap.xml',
            'EGAS50000000906',
            'tmp',
        ])

        self.assertEqual(args.creator, ['UU'])
        self.assertEqual(args.publisher, 'LU')
        self.assertEqual(args.keywords, ['genomics', 'reference dataset'])
        self.assertEqual(args.site_base_url, 'https://example.org')
        self.assertEqual(args.sitemap_filename, 'custom-sitemap.xml')
        self.assertEqual(args.study_id, 'EGAS50000000906')
        self.assertEqual(args.output_dir, 'tmp')

    def test_parse_args_accepts_repeated_creators(self) -> None:
        args = parse_args([
            '--creator', 'UU',
            '--creator', 'LU',
            '--publisher', 'BTB',
            '--keyword', 'genomics',
            'EGAS50000000906',
            'tmp',
        ])

        self.assertEqual(args.creator, ['UU', 'LU'])
        self.assertEqual(args.publisher, 'BTB')

    def test_parse_args_requires_keyword(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args([
                '--creator', 'UU',
                '--publisher', 'LU',
                'EGAS50000000906',
                'tmp',
            ])

    def test_parse_args_requires_creator(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args([
                '--keyword', 'genomics',
                'EGAS50000000906',
                'tmp',
            ])

    def test_parse_args_requires_publisher(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args([
                '--creator', 'UU',
                '--keyword', 'genomics',
                'EGAS50000000906',
                'tmp',
            ])

    def test_parse_args_rejects_fega_se_as_publisher(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args([
                '--creator', 'FEGA-SE',
                '--publisher', 'FEGA-SE',
                '--keyword', 'genomics',
                'EGAS50000000906',
                'tmp',
            ])

    def test_transform_ega_dataset_builds_expected_schema_org_payload(self) -> None:
        ega_dataset = {
            'accession_id': 'EGAD50000001323',
            'title': 'SweGen reference dataset',
            'released_date': '2024-01-02T03:04:05Z',
            'description': 'Population-scale whole genome variation.',
        }

        dataset = transform_ega_dataset(
            ega_dataset,
            num_datasets=2,
            study_title='SweGen',
            study_url='http://identifiers.org/ega.study:EGAS50000000906',
            creator_orgs=['UU', 'BTB'],
            publisher_org='LU',
            keywords=['genomics', 'reference dataset'],
        )

        self.assertEqual(dataset['identifier'], 'http://identifiers.org/ega.dataset:EGAD50000001323')
        self.assertEqual(dataset['datePublished'], '2024-01-02')
        self.assertEqual(
            [creator['name'] for creator in dataset['creator']],
            ['Uppsala University', 'The Swedish Childhood Tumor Biobank'],
        )
        self.assertEqual(dataset['keywords'], ['genomics', 'reference dataset'])
        self.assertEqual(dataset['publisher']['name'], 'Lund University')
        self.assertEqual(dataset['publisher']['@id'], 'https://ror.org/012a77v79')
        self.assertEqual(dataset['includedInDataCatalog']['@type'], 'DataCatalog')
        self.assertEqual(dataset['includedInDataCatalog']['url'], 'https://fega.nbis.se')
        self.assertEqual(dataset['sdPublisher']['name'], 'FEGA Sweden')
        self.assertEqual(dataset['sdPublisher']['url'], 'https://fega.nbis.se')
        self.assertNotIn('@id', dataset['sdPublisher'])
        self.assertEqual(
            dataset['isPartOf']['@id'],
            'http://identifiers.org/ega.study:EGAS50000000906',
        )
        self.assertIn('This dataset is one of 2 datasets', dataset['description'])
        self.assertIn('study "SweGen"', dataset['description'])

    def test_transform_ega_dataset_normalizes_crlf_in_description(self) -> None:
        dataset = transform_ega_dataset(
            {
                'accession_id': 'EGAD50000001323',
                'title': 'SweGen reference dataset',
                'released_date': '2024-01-02T03:04:05Z',
                'description': 'Line one.\r\n\r\nLine two.',
            },
            num_datasets=1,
            study_title='SweGen',
            study_url='http://identifiers.org/ega.study:EGAS50000000906',
            creator_orgs=['UU'],
            publisher_org='LU',
            keywords=['genomics'],
        )

        self.assertIn('Line one.\n\nLine two.', dataset['description'])
        self.assertNotIn('\r', dataset['description'])

    def test_normalize_ega_dataset_metadata_builds_internal_metadata_model(self) -> None:
        normalized = normalize_ega_dataset_metadata(
            accession_id='EGAD50000001323',
            title=' SweGen reference dataset ',
            released_date='2024-01-02T03:04:05Z',
            description='Population-scale whole genome variation.',
            study_title='SweGen',
            study_url='http://identifiers.org/ega.study:EGAS50000000906',
            num_datasets=2,
            creator_orgs=['UU', 'BTB'],
            publisher_org='LU',
            keywords=['genomics', 'reference dataset'],
        )

        self.assertEqual(normalized.accession_id, 'EGAD50000001323')
        self.assertEqual(normalized.title, 'SweGen reference dataset')
        self.assertEqual(normalized.date_published, '2024-01-02')
        self.assertEqual(normalized.study_identifier, 'http://identifiers.org/ega.study:EGAS50000000906')
        self.assertEqual(normalized.publisher['name'], 'Lund University')
        self.assertEqual(normalized.publisher['@id'], 'https://ror.org/012a77v79')
        self.assertEqual(normalized.included_in_data_catalog['url'], 'https://fega.nbis.se')
        self.assertEqual(normalized.sd_publisher['url'], 'https://fega.nbis.se')
        self.assertNotIn('@id', normalized.sd_publisher)
        self.assertEqual(
            [creator['name'] for creator in normalized.creators],
            ['Uppsala University', 'The Swedish Childhood Tumor Biobank'],
        )
        self.assertEqual(normalized.keywords, ['genomics', 'reference dataset'])
        self.assertIn('This dataset is one of 2 datasets', normalized.description)
        self.assertIn('study "SweGen"', normalized.description)

    def test_compose_markdown_makes_study_url_clickable_in_body(self) -> None:
        dataset = transform_ega_dataset(
            {
                'accession_id': 'EGAD50000001323',
                'title': 'SweGen reference dataset',
                'released_date': '2024-01-02T03:04:05Z',
                'description': 'Population-scale whole genome variation.',
            },
            num_datasets=2,
            study_title='SweGen',
            study_url='http://identifiers.org/ega.study:EGAS50000000906',
            creator_orgs=['UU'],
            publisher_org='LU',
            keywords=['genomics'],
        )

        markdown = compose_markdown(dataset)

        self.assertIn(
            'study "SweGen" ([http://identifiers.org/ega.study:EGAS50000000906](http://identifiers.org/ega.study:EGAS50000000906)).',
            markdown,
        )
        self.assertIn(
            'study "SweGen" (http://identifiers.org/ega.study:EGAS50000000906).',
            dataset['description'],
        )

    def test_transform_ega_dataset_allows_publisher_without_creator_in_isolation(self) -> None:
        dataset = transform_ega_dataset(
            {
                'accession_id': 'EGAD50000001323',
                'title': 'SweGen reference dataset',
                'released_date': '2024-01-02T03:04:05Z',
                'description': 'Population-scale whole genome variation.',
            },
            num_datasets=1,
            study_title='SweGen',
            study_url='http://identifiers.org/ega.study:EGAS50000000906',
            publisher_org='UU',
            keywords=['genomics'],
        )

        self.assertEqual(dataset['publisher']['name'], 'Uppsala University')
        self.assertEqual(dataset['publisher']['@id'], 'https://ror.org/048a87296')
        self.assertNotIn('creator', dataset)
        self.assertEqual(dataset['keywords'], ['genomics'])
        self.assertIn(
            'This dataset is included in the study "SweGen" (http://identifiers.org/ega.study:EGAS50000000906).',
            dataset['description'],
        )

    def test_transform_ega_dataset_requires_publisher_in_isolation(self) -> None:
        with self.assertRaisesRegex(
            MetadataValidationError,
            'publisher must be specified for Researchdata.se export',
        ):
            transform_ega_dataset(
                {
                    'accession_id': 'EGAD50000001323',
                    'title': 'SweGen reference dataset',
                    'released_date': '2024-01-02T03:04:05Z',
                    'description': 'Population-scale whole genome variation.',
                },
                num_datasets=1,
                study_title='SweGen',
                study_url='http://identifiers.org/ega.study:EGAS50000000906',
            )

    def test_transform_ega_dataset_requires_keywords_in_isolation(self) -> None:
        with self.assertRaisesRegex(
            MetadataValidationError,
            'keywords must be specified for Researchdata.se export',
        ):
            transform_ega_dataset(
                {
                    'accession_id': 'EGAD50000001323',
                    'title': 'SweGen reference dataset',
                    'released_date': '2024-01-02T03:04:05Z',
                    'description': 'Population-scale whole genome variation.',
                },
                num_datasets=1,
                study_title='SweGen',
                study_url='http://identifiers.org/ega.study:EGAS50000000906',
                publisher_org='UU',
            )

    def test_transform_ega_dataset_rejects_fega_se_as_publisher(self) -> None:
        with self.assertRaisesRegex(
            MetadataValidationError,
            'FEGA-SE cannot be used as publisher for Researchdata.se export',
        ):
            transform_ega_dataset(
                {
                    'accession_id': 'EGAD50000001323',
                    'title': 'SweGen reference dataset',
                    'released_date': '2024-01-02T03:04:05Z',
                    'description': 'Population-scale whole genome variation.',
                },
                num_datasets=1,
                study_title='SweGen',
                study_url='http://identifiers.org/ega.study:EGAS50000000906',
                creator_orgs=['FEGA-SE'],
                publisher_org='FEGA-SE',
                keywords=['genomics'],
            )

    def test_transform_ega_dataset_uses_export_site_base_url_for_fega_roles(self) -> None:
        dataset = transform_ega_dataset(
            {
                'accession_id': 'EGAD50000001323',
                'title': 'SweGen reference dataset',
                'released_date': '2024-01-02T03:04:05Z',
                'description': 'Population-scale whole genome variation.',
            },
            num_datasets=1,
            study_title='SweGen',
            study_url='http://identifiers.org/ega.study:EGAS50000000906',
            publisher_org='UU',
            keywords=['genomics'],
            site_base_url='https://example.org',
        )

        self.assertEqual(dataset['includedInDataCatalog']['@id'], 'https://example.org')
        self.assertEqual(dataset['includedInDataCatalog']['url'], 'https://example.org')
        self.assertEqual(dataset['sdPublisher']['url'], 'https://example.org')
        self.assertEqual(dataset['publisher']['name'], 'Uppsala University')
        self.assertEqual(dataset['publisher']['@id'], 'https://ror.org/048a87296')

    def test_transform_ega_dataset_omits_null_organisation_fields(self) -> None:
        dataset = transform_ega_dataset(
            {
                'accession_id': 'EGAD50000001323',
                'title': 'SweGen reference dataset',
                'released_date': '2024-01-02T03:04:05Z',
                'description': 'Population-scale whole genome variation.',
            },
            num_datasets=1,
            study_title='SweGen',
            study_url='http://identifiers.org/ega.study:EGAS50000000906',
            creator_orgs=['BTB', 'FEGA-SE'],
            publisher_org='BTB',
            keywords=['genomics'],
        )

        self.assertEqual(
            [creator['name'] for creator in dataset['creator']],
            ['The Swedish Childhood Tumor Biobank', 'FEGA Sweden'],
        )
        self.assertEqual(dataset['publisher']['name'], 'The Swedish Childhood Tumor Biobank')
        self.assertNotIn('@id', dataset['creator'][0])
        self.assertNotIn('@id', dataset['creator'][1])
        self.assertNotIn('@id', dataset['publisher'])

    def test_compose_yaml_front_matter_includes_keywords(self) -> None:
        ega_dataset = {
            'accession_id': 'EGAD50000001323',
            'title': 'Dataset: with colon',
            'released_date': '2024-01-02T03:04:05Z',
            'description': 'Description.',
        }

        dataset = transform_ega_dataset(
            ega_dataset,
            num_datasets=1,
            study_title='Study Alpha',
            study_url='http://identifiers.org/ega.study:EGAS50000000906',
            creator_orgs=['UU', 'LU'],
            publisher_org='UU',
            keywords=['genomics', 'reference dataset'],
        )
        front_matter = compose_yaml_front_matter(dataset)

        self.assertIn('title: "Dataset: with colon"', front_matter)
        self.assertIn('author:\n  - "Uppsala University"\n  - "Lund University"', front_matter)
        self.assertIn('categories:\n  - "genomics"\n  - "reference dataset"', front_matter)

    def test_build_sitemap_entries_sorts_by_location(self) -> None:
        entries = build_sitemap_entries([
            ExportedDataset(
                accession_id='EGAD2',
                date_published='2024-01-02',
                file_path=Path('/tmp/EGAD2.qmd'),
                page_url='https://example.org/catalogue/datasets/EGAD2.html',
            ),
            ExportedDataset(
                accession_id='EGAD1',
                date_published='2024-01-01',
                file_path=Path('/tmp/EGAD1.qmd'),
                page_url='https://example.org/catalogue/datasets/EGAD1.html',
            ),
        ])

        self.assertEqual([entry.loc for entry in entries], [
            'https://example.org/catalogue/datasets/EGAD1.html',
            'https://example.org/catalogue/datasets/EGAD2.html',
        ])
        self.assertEqual([entry.lastmod for entry in entries], [None, None])

    def test_build_sitemap_entries_uses_explicit_lastmod_when_provided(self) -> None:
        entries = build_sitemap_entries([
            ExportedDataset(
                accession_id='EGAD1',
                date_published='2024-01-01',
                file_path=Path('/tmp/EGAD1.qmd'),
                page_url='https://example.org/catalogue/datasets/EGAD1.html',
            ),
        ], sitemap_lastmod='2026-04-03')

        self.assertEqual(entries[0].lastmod, '2026-04-03')

    def test_build_export_artifacts_supports_dataset_specific_keywords(self) -> None:
        study_context = StudyContext(
            title='SweGen',
            url='http://identifiers.org/ega.study:EGAS50000000906',
            datasets=[
                {
                    'accession_id': 'EGAD50000001323',
                    'title': 'Dataset A',
                    'released_date': '2024-01-02T10:00:00Z',
                    'description': 'First dataset description.',
                },
                {
                    'accession_id': 'EGAD50000001324',
                    'title': 'Dataset B',
                    'released_date': '2024-01-03T10:00:00Z',
                    'description': 'Second dataset description.',
                },
            ],
        )

        artifacts = build_export_artifacts(
            study_context=study_context,
            creator_orgs=['UU', 'LU'],
            publisher_org='BTB',
            export_config=ExportConfig(
                site_base_url='https://example.org',
                sitemap_filename='catalogue-sitemap.xml',
                sitemap_lastmod='2026-04-03',
            ),
            dataset_keywords_by_accession={
                'EGAD50000001323': ['population genetics'],
                'EGAD50000001324': ['reference cohort', 'whole genome'],
            },
            selected_accessions={'EGAD50000001323', 'EGAD50000001324'},
        )

        self.assertEqual(
            [dataset_file.filename for dataset_file in artifacts.dataset_files],
            ['EGAD50000001323.qmd', 'EGAD50000001324.qmd'],
        )
        self.assertIn(
            'categories:\n  - "population genetics"',
            artifacts.dataset_files[0].content,
        )
        self.assertIn(
            'categories:\n  - "reference cohort"\n  - "whole genome"',
            artifacts.dataset_files[1].content,
        )
        self.assertIn(
            '<loc>https://example.org/catalogue/datasets/EGAD50000001323.html</loc>',
            artifacts.sitemap_file.content,
        )
        self.assertIn('<lastmod>2026-04-03</lastmod>', artifacts.sitemap_file.content)

        zip_bytes = build_export_zip_bytes(artifacts)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            self.assertEqual(
                sorted(archive.namelist()),
                ['EGAD50000001323.qmd', 'EGAD50000001324.qmd', 'catalogue-sitemap.xml'],
            )

        zip_bytes_with_project = build_export_zip_bytes(
            artifacts,
            extra_files=[
                GeneratedFile(
                    filename='fega-sweden-metadata-project-EGAS50000000906.json',
                    content='{"study_id": "EGAS50000000906"}',
                ),
            ],
        )
        with zipfile.ZipFile(io.BytesIO(zip_bytes_with_project)) as archive:
            self.assertEqual(
                sorted(archive.namelist()),
                [
                    'EGAD50000001323.qmd',
                    'EGAD50000001324.qmd',
                    'catalogue-sitemap.xml',
                    'fega-sweden-metadata-project-EGAS50000000906.json',
                ],
            )

    def test_build_export_artifacts_requires_keywords_for_selected_dataset(self) -> None:
        study_context = StudyContext(
            title='SweGen',
            url='http://identifiers.org/ega.study:EGAS50000000906',
            datasets=[
                {
                    'accession_id': 'EGAD50000001323',
                    'title': 'Dataset A',
                    'released_date': '2024-01-02T10:00:00Z',
                    'description': 'First dataset description.',
                },
            ],
        )

        with self.assertRaisesRegex(
            MetadataValidationError,
            'dataset EGAD50000001323 must include at least one keyword for Researchdata.se export',
        ):
            build_export_artifacts(
                study_context=study_context,
                creator_orgs=['UU'],
                publisher_org='LU',
                export_config=ExportConfig(
                    site_base_url='https://example.org',
                    sitemap_filename='catalogue-sitemap.xml',
                ),
                dataset_keywords_by_accession={'EGAD50000001323': []},
                selected_accessions={'EGAD50000001323'},
            )

    def test_export_project_roundtrip_preserves_snapshot_and_artifacts(self) -> None:
        study_context = StudyContext(
            title='SweGen',
            url='http://identifiers.org/ega.study:EGAS50000000906',
            datasets=[
                {
                    'accession_id': 'EGAD50000001323',
                    'title': 'Dataset A',
                    'released_date': '2024-01-02T10:00:00Z',
                    'description': 'First dataset description.',
                },
                {
                    'accession_id': 'EGAD50000001324',
                    'title': 'Dataset B',
                    'released_date': '2024-01-03T10:00:00Z',
                    'description': 'Second dataset description.',
                },
            ],
        )
        project = build_export_project(
            study_id='EGAS50000000906',
            study_context=study_context,
            creator_orgs=['UU', 'LU'],
            publisher_org='BTB',
                export_config=ExportConfig(
                    site_base_url='https://example.org',
                    sitemap_filename='catalogue-sitemap.xml',
                    sitemap_lastmod='2026-04-03',
                ),
            global_keywords=['genomics'],
            dataset_keywords_by_accession={
                'EGAD50000001323': ['population genetics'],
                'EGAD50000001324': ['reference cohort'],
            },
            selected_accessions={'EGAD50000001324'},
        )

        project_json = serialize_export_project(project)
        restored_project = deserialize_export_project(project_json)
        artifacts = build_export_artifacts_from_project(restored_project)

        self.assertEqual(restored_project['study_id'], 'EGAS50000000906')
        self.assertEqual(restored_project['creator_orgs'], ['UU', 'LU'])
        self.assertEqual(restored_project['publisher_org'], 'BTB')
        self.assertEqual(restored_project['global_keywords'], ['genomics'])
        self.assertEqual(restored_project['sitemap_lastmod'], '2026-04-03')
        self.assertEqual(
            restored_project['datasets'],
            [
                {
                    'accession_id': 'EGAD50000001323',
                    'include': False,
                    'keywords': ['population genetics'],
                },
                {
                    'accession_id': 'EGAD50000001324',
                    'include': True,
                    'keywords': ['reference cohort'],
                },
            ],
        )
        self.assertEqual(
            [dataset_file.filename for dataset_file in artifacts.dataset_files],
            ['EGAD50000001324.qmd'],
        )
        self.assertIn(
            'categories:\n  - "genomics"\n  - "reference cohort"',
            artifacts.dataset_files[0].content,
        )

    def test_deserialize_export_project_supports_schema_version_1_without_global_keywords(self) -> None:
        project_json = """
        {
          "schema_version": 1,
          "created_at": "2026-04-03T12:00:00+00:00",
          "study_id": "EGAS50000000906",
          "study_context": {
            "title": "SweGen",
            "url": "http://identifiers.org/ega.study:EGAS50000000906",
            "datasets": [
              {
                "accession_id": "EGAD50000001323",
                "title": "Dataset A",
                "released_date": "2024-01-02T10:00:00Z",
                "description": "First dataset description."
              }
            ]
          },
          "creator_orgs": ["UU"],
          "publisher_org": "LU",
          "site_base_url": "https://example.org",
          "sitemap_filename": "catalogue-sitemap.xml",
          "datasets": [
            {
              "accession_id": "EGAD50000001323",
              "include": true,
              "keywords": ["population genetics"]
            }
          ]
        }
        """

        restored_project = deserialize_export_project(project_json)

        self.assertEqual(restored_project['schema_version'], 3)
        self.assertEqual(restored_project['global_keywords'], [])
        self.assertIsNone(restored_project['sitemap_lastmod'])
        self.assertEqual(restored_project['datasets'][0]['keywords'], ['population genetics'])

    def test_build_export_artifacts_matches_golden_output(self) -> None:
        study_context = StudyContext(
            title='SweGen',
            url='http://identifiers.org/ega.study:EGAS50000000906',
            datasets=[
                {
                    'accession_id': 'EGAD50000001323',
                    'title': 'Dataset A',
                    'released_date': '2024-01-02T10:00:00Z',
                    'description': 'First dataset description.',
                },
                {
                    'accession_id': 'EGAD50000001324',
                    'title': 'Dataset B',
                    'released_date': '2024-01-03T10:00:00Z',
                    'description': 'Second dataset description.',
                },
            ],
        )

        artifacts = build_export_artifacts(
            study_context=study_context,
            creator_orgs=['UU', 'LU'],
            publisher_org='BTB',
            export_config=ExportConfig(
                site_base_url='https://example.org',
                sitemap_filename='catalogue-sitemap.xml',
            ),
            dataset_keywords_by_accession={
                'EGAD50000001323': ['population genetics'],
                'EGAD50000001324': ['reference cohort', 'whole genome'],
            },
            selected_accessions={'EGAD50000001323', 'EGAD50000001324'},
        )

        actual_files = {
            dataset_file.filename: dataset_file.content
            for dataset_file in artifacts.dataset_files
        }
        actual_files[artifacts.sitemap_file.filename] = artifacts.sitemap_file.content
        expected_files = {
            fixture_path.name: fixture_path.read_text(encoding='utf-8')
            for fixture_path in GOLDEN_EXPORT_DIR.iterdir()
            if fixture_path.is_file()
        }

        self.assertEqual(actual_files, expected_files)

    def test_write_sitemap_file_writes_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            sitemap_path = Path(tmp_dir) / 'sitemap.xml'
            entries = build_sitemap_entries([
                ExportedDataset(
                    accession_id='EGAD50000001323',
                    date_published='2024-01-02',
                    file_path=Path(tmp_dir) / 'EGAD50000001323.qmd',
                    page_url='https://example.org/catalogue/datasets/EGAD50000001323.html',
                ),
            ], sitemap_lastmod='2026-04-03')

            write_sitemap_file(sitemap_path, entries)

            document = ET.parse(sitemap_path)
            locations = document.findall('.//sm:loc', SITEMAP_XMLNS)
            last_modified = document.findall('.//sm:lastmod', SITEMAP_XMLNS)

            self.assertEqual(len(locations), 1)
            self.assertEqual(
                locations[0].text,
                'https://example.org/catalogue/datasets/EGAD50000001323.html',
            )
            self.assertEqual(last_modified[0].text, '2026-04-03')

    def test_compose_sitemap_xml_omits_lastmod_when_not_provided(self) -> None:
        entries = build_sitemap_entries([
            ExportedDataset(
                accession_id='EGAD50000001323',
                date_published='2024-01-02',
                file_path=Path('/tmp/EGAD50000001323.qmd'),
                page_url='https://example.org/catalogue/datasets/EGAD50000001323.html',
            ),
        ])

        sitemap_xml = compose_sitemap_xml(entries)

        self.assertIn('<loc>https://example.org/catalogue/datasets/EGAD50000001323.html</loc>', sitemap_xml)
        self.assertNotIn('<lastmod>', sitemap_xml)

    def test_get_related_entities_fetches_all_pages(self) -> None:
        fake_session = FakeSession([
            {
                'count': 3,
                'results': [
                    {'accession_id': 'EGAD1'},
                    {'accession_id': 'EGAD2'},
                ],
            },
            {
                'count': 3,
                'results': [
                    {'accession_id': 'EGAD3'},
                ],
            },
        ])

        client = EGAClient(session=fake_session)
        datasets = client.get_related_entities('studies', 'datasets', 'EGAS1')

        self.assertEqual(
            [dataset['accession_id'] for dataset in datasets],
            ['EGAD1', 'EGAD2', 'EGAD3'],
        )
        self.assertEqual(fake_session.calls[0]['params'], {'limit': 100, 'offset': 0})
        self.assertEqual(fake_session.calls[1]['params'], {'limit': 100, 'offset': 2})

    def test_get_related_entities_with_limit_returns_single_page(self) -> None:
        fake_session = FakeSession([
            [
                {'accession_id': 'EGAD1'},
                {'accession_id': 'EGAD2'},
            ],
        ])

        client = EGAClient(session=fake_session)
        datasets = client.get_related_entities('studies', 'datasets', 'EGAS1', limit=2, offset=4)

        self.assertEqual(
            [dataset['accession_id'] for dataset in datasets],
            ['EGAD1', 'EGAD2'],
        )
        self.assertEqual(len(fake_session.calls), 1)
        self.assertEqual(fake_session.calls[0]['params'], {'limit': 2, 'offset': 4})

    def test_fetch_study_context_returns_validated_study_and_datasets(self) -> None:
        fake_client = FakeAPIClient(
            study_payload={
                'accession_id': 'EGAS50000000906',
                'title': '  SweGen  ',
            },
            dataset_payload=[
                {
                    'accession_id': 'EGAD50000001323',
                    'title': ' Dataset A ',
                    'released_date': '2024-01-02T10:00:00Z',
                    'description': ' First dataset description. ',
                },
            ],
        )

        study_context = fetch_study_context(fake_client, 'EGAS50000000906')

        self.assertEqual(study_context.title, 'SweGen')
        self.assertEqual(
            study_context.url,
            'http://identifiers.org/ega.study:EGAS50000000906',
        )
        self.assertEqual(len(study_context.datasets), 1)
        self.assertEqual(study_context.datasets[0]['title'], 'Dataset A')
        self.assertEqual(
            fake_client.calls,
            [
                ('get_entity', 'studies', 'EGAS50000000906', None, None),
                (
                    'get_related_entities',
                    'studies',
                    'datasets',
                    'EGAS50000000906',
                    None,
                    None,
                ),
            ],
        )

    def test_validate_ega_study_reports_missing_title(self) -> None:
        with self.assertRaisesRegex(
            MetadataValidationError,
            'study EGAS50000000906 is missing required string field "title"',
        ):
            validate_ega_study({'accession_id': 'EGAS50000000906'}, study_id='EGAS50000000906')

    def test_validate_ega_dataset_reports_empty_description(self) -> None:
        with self.assertRaisesRegex(
            MetadataValidationError,
            'dataset EGAD50000001323 has empty required field "description"',
        ):
            validate_ega_dataset(
                {
                    'accession_id': 'EGAD50000001323',
                    'title': 'Example dataset',
                    'released_date': '2024-01-02T03:04:05Z',
                    'description': '   ',
                },
                study_id='EGAS50000000906',
            )

    def test_transform_ega_dataset_reports_invalid_released_date(self) -> None:
        with self.assertRaisesRegex(
            MetadataValidationError,
            'Invalid released_date value "not-a-date"',
        ):
            transform_ega_dataset(
                {
                    'accession_id': 'EGAD50000001323',
                    'title': 'Example dataset',
                    'released_date': 'not-a-date',
                    'description': 'Description.',
                },
                num_datasets=1,
                study_title='Study Alpha',
                study_url='http://identifiers.org/ega.study:EGAS50000000906',
                publisher_org='UU',
                keywords=['genomics'],
            )

    def test_export_study_metadata_writes_dataset_files_and_sitemap_with_mocked_client(self) -> None:
        fake_client = FakeAPIClient(
            study_payload={
                'accession_id': 'EGAS50000000906',
                'title': 'SweGen',
            },
            dataset_payload=[
                {
                    'accession_id': 'EGAD50000001324',
                    'title': 'Dataset B',
                    'released_date': '2024-01-03T10:00:00Z',
                    'description': 'Second dataset description.',
                },
                {
                    'accession_id': 'EGAD50000001323',
                    'title': 'Dataset A',
                    'released_date': '2024-01-02T10:00:00Z',
                    'description': 'First dataset description.',
                },
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            args = parse_args([
                '--creator', 'UU',
                '--creator', 'LU',
                '--publisher', 'LU',
                '--keyword', 'genomics',
                '--site-base-url', 'https://example.org',
                '--sitemap-filename', 'catalogue-sitemap.xml',
                'EGAS50000000906',
                tmp_dir,
            ])
            stdout_buffer = io.StringIO()

            with patch('metadata_export_core.core.EGAClient', return_value=fake_client):
                with redirect_stdout(stdout_buffer):
                    export_study_metadata(args)

            dataset_file = Path(tmp_dir) / 'EGAD50000001323.qmd'
            sitemap_file = Path(tmp_dir) / 'catalogue-sitemap.xml'

            self.assertTrue(dataset_file.exists())
            self.assertTrue(sitemap_file.exists())
            self.assertIn('Dataset A', dataset_file.read_text(encoding='utf-8'))
            self.assertIn('categories:\n  - "genomics"', dataset_file.read_text(encoding='utf-8'))

            document = ET.parse(sitemap_file)
            locations = [
                node.text for node in document.findall('.//sm:loc', SITEMAP_XMLNS)
            ]
            self.assertEqual(locations, [
                'https://example.org/catalogue/datasets/EGAD50000001323.html',
                'https://example.org/catalogue/datasets/EGAD50000001324.html',
            ])

            stdout_value = stdout_buffer.getvalue()
            self.assertIn(f'Wrote {Path(tmp_dir) / "EGAD50000001323.qmd"}', stdout_value)
            self.assertIn(f'Wrote {Path(tmp_dir) / "catalogue-sitemap.xml"}', stdout_value)

    def test_main_matches_core_export_artifacts_for_same_input(self) -> None:
        study_payload = {
            'accession_id': 'EGAS50000000906',
            'title': 'SweGen',
        }
        dataset_payload = [
            {
                'accession_id': 'EGAD50000001324',
                'title': 'Dataset B',
                'released_date': '2024-01-03T10:00:00Z',
                'description': 'Second dataset description.',
            },
            {
                'accession_id': 'EGAD50000001323',
                'title': 'Dataset A',
                'released_date': '2024-01-02T10:00:00Z',
                'description': 'First dataset description.',
            },
        ]
        study_context = fetch_study_context(
            FakeAPIClient(
                study_payload=study_payload,
                dataset_payload=dataset_payload,
            ),
            'EGAS50000000906',
        )
        expected_artifacts = build_export_artifacts(
            study_context=study_context,
            creator_orgs=['UU', 'LU'],
            publisher_org='BTB',
            export_config=ExportConfig(
                site_base_url='https://example.org',
                sitemap_filename='catalogue-sitemap.xml',
            ),
            default_keywords=['genomics'],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            stdout_buffer = io.StringIO()
            fake_client = FakeAPIClient(
                study_payload=study_payload,
                dataset_payload=dataset_payload,
            )

            with patch('metadata_export_core.core.EGAClient', return_value=fake_client):
                with redirect_stdout(stdout_buffer):
                    exit_code = main([
                        '--creator', 'UU',
                        '--creator', 'LU',
                        '--publisher', 'BTB',
                        '--keyword', 'genomics',
                        '--site-base-url', 'https://example.org',
                        '--sitemap-filename', 'catalogue-sitemap.xml',
                        'EGAS50000000906',
                        tmp_dir,
                    ])

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                sorted(path.name for path in Path(tmp_dir).iterdir()),
                ['EGAD50000001323.qmd', 'EGAD50000001324.qmd', 'catalogue-sitemap.xml'],
            )

            actual_files = {
                path.name: path.read_text(encoding='utf-8')
                for path in Path(tmp_dir).iterdir()
            }
            expected_files = {
                dataset_file.filename: dataset_file.content
                for dataset_file in expected_artifacts.dataset_files
            }
            expected_files[expected_artifacts.sitemap_file.filename] = (
                expected_artifacts.sitemap_file.content
            )

            self.assertEqual(actual_files, expected_files)

    def test_main_returns_validation_error_with_mocked_client(self) -> None:
        fake_client = FakeAPIClient(
            study_payload={
                'accession_id': 'EGAS50000000906',
                'title': 'SweGen',
            },
            dataset_payload=[
                {
                    'accession_id': 'EGAD50000001323',
                    'title': 'Broken dataset',
                    'released_date': '2024-01-02T10:00:00Z',
                    'description': '',
                },
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            stderr_buffer = io.StringIO()
            with patch('metadata_export_core.core.EGAClient', return_value=fake_client):
                with redirect_stderr(stderr_buffer):
                    exit_code = main([
                        '--creator', 'UU',
                        '--creator', 'LU',
                        '--publisher', 'LU',
                        '--keyword', 'genomics',
                        'EGAS50000000906',
                        tmp_dir,
                    ])

            self.assertEqual(exit_code, 1)
            self.assertIn(
                'Metadata validation failed: dataset EGAD50000001323 has empty required field "description"',
                stderr_buffer.getvalue(),
            )


if __name__ == '__main__':
    unittest.main()
