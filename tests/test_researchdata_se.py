import io
import tempfile
import unittest
import xml.etree.ElementTree as ET

from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from metadata_export.researchdata_se import (
    EGAClient,
    ExportedDataset,
    MetadataValidationError,
    build_sitemap_entries,
    compose_yaml_front_matter,
    export_study_metadata,
    fetch_study_context,
    main,
    parse_args,
    validate_ega_dataset,
    validate_ega_study,
    transform_ega_dataset,
    write_sitemap_file,
)


SITEMAP_XMLNS = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}


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
            '--keyword', 'genomics',
            '--keyword', 'reference dataset',
            '--site-base-url', 'https://example.org',
            '--sitemap-filename', 'custom-sitemap.xml',
            'EGAS50000000906',
            'tmp',
        ])

        self.assertEqual(args.keywords, ['genomics', 'reference dataset'])
        self.assertEqual(args.site_base_url, 'https://example.org')
        self.assertEqual(args.sitemap_filename, 'custom-sitemap.xml')
        self.assertEqual(args.study_id, 'EGAS50000000906')
        self.assertEqual(args.output_dir, 'tmp')

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
            creator_org='UU',
            keywords=['genomics', 'reference dataset'],
        )

        self.assertEqual(dataset['identifier'], 'http://identifiers.org/ega.dataset:EGAD50000001323')
        self.assertEqual(dataset['datePublished'], '2024-01-02')
        self.assertEqual(dataset['creator']['name'], 'Uppsala University')
        self.assertEqual(dataset['keywords'], ['genomics', 'reference dataset'])
        self.assertEqual(
            dataset['isPartOf']['@id'],
            'http://identifiers.org/ega.study:EGAS50000000906',
        )
        self.assertIn('This dataset is one of 2 datasets', dataset['description'])

    def test_compose_yaml_front_matter_handles_missing_keywords(self) -> None:
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
        )
        front_matter = compose_yaml_front_matter(dataset)

        self.assertIn('title: "Dataset: with colon"', front_matter)
        self.assertIn('categories: []', front_matter)

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
            ])

            write_sitemap_file(sitemap_path, entries)

            document = ET.parse(sitemap_path)
            locations = document.findall('.//sm:loc', SITEMAP_XMLNS)
            last_modified = document.findall('.//sm:lastmod', SITEMAP_XMLNS)

            self.assertEqual(len(locations), 1)
            self.assertEqual(
                locations[0].text,
                'https://example.org/catalogue/datasets/EGAD50000001323.html',
            )
            self.assertEqual(last_modified[0].text, '2024-01-02')

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
                '--keyword', 'genomics',
                '--site-base-url', 'https://example.org',
                '--sitemap-filename', 'catalogue-sitemap.xml',
                'EGAS50000000906',
                tmp_dir,
            ])
            stdout_buffer = io.StringIO()

            with patch('metadata_export.researchdata_se.EGAClient', return_value=fake_client):
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
            with patch('metadata_export.researchdata_se.EGAClient', return_value=fake_client):
                with redirect_stderr(stderr_buffer):
                    exit_code = main(['EGAS50000000906', tmp_dir])

            self.assertEqual(exit_code, 1)
            self.assertIn(
                'Metadata validation failed: dataset EGAD50000001323 has empty required field "description"',
                stderr_buffer.getvalue(),
            )


if __name__ == '__main__':
    unittest.main()
