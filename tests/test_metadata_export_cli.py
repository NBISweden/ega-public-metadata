import io
import tempfile
import unittest
import zipfile
import requests

from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from metadata_enrichment_core.core import ExportConfig, StudyContext, build_export_project, serialize_export_project
from metadata_enrichment_app.cli import (
    build_parser,
    main,
    parse_dataset_keyword_assignments,
    resolve_selected_accessions,
)


class FakeAPIClient:
    def __init__(self, study_payload, dataset_payload):
        self.study_payload = study_payload
        self.dataset_payload = dataset_payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def get_entity(self, entity_type, accession_id=None, limit=None, offset=None):
        return self.study_payload

    def get_related_entities(
        self,
        entity_type,
        related_entity_type,
        accession_id,
        limit=None,
        offset=None,
    ):
        return self.dataset_payload


class MetadataExportCliTests(unittest.TestCase):
    def test_fetch_parser_accepts_app_like_metadata_arguments(self) -> None:
        args = build_parser().parse_args([
            'fetch',
            'EGAS50000000906',
            'tmp',
            '--creator', 'KI',
            '--creator', 'LU',
            '--publisher', 'KI',
            '--source-organization', 'KI',
            '--global-keyword', 'genomics',
            '--dataset-keyword', 'EGAD50000001323=population genetics',
            '--include-dataset', 'EGAD50000001323',
            '--site-name', 'NBIS Data Portal',
            '--site-base-url', 'https://example.org',
            '--project-file', 'project.json',
            '--zip-file', 'export.zip',
        ])

        self.assertEqual(args.command, 'fetch')
        self.assertEqual(args.study_id, 'EGAS50000000906')
        self.assertEqual(args.creator, ['KI', 'LU'])
        self.assertEqual(args.publisher, 'KI')
        self.assertEqual(args.source_orgs, ['KI'])
        self.assertEqual(args.global_keywords, ['genomics'])
        self.assertEqual(args.dataset_keywords, ['EGAD50000001323=population genetics'])
        self.assertEqual(args.include_accessions, ['EGAD50000001323'])
        self.assertEqual(args.site_name, 'NBIS Data Portal')
        self.assertEqual(args.site_base_url, 'https://example.org')

    def test_parse_dataset_keyword_assignments_groups_keywords_by_accession(self) -> None:
        self.assertEqual(
            parse_dataset_keyword_assignments([
                'EGAD50000001323=population genetics',
                'EGAD50000001323=whole genome',
                'EGAD50000001324=reference cohort',
            ]),
            {
                'EGAD50000001323': ['population genetics', 'whole genome'],
                'EGAD50000001324': ['reference cohort'],
            },
        )

    def test_resolve_selected_accessions_applies_include_then_exclude(self) -> None:
        self.assertEqual(
            resolve_selected_accessions(
                {'EGAD1', 'EGAD2', 'EGAD3'},
                ['EGAD1', 'EGAD2'],
                ['EGAD2'],
            ),
            {'EGAD1'},
        )

    def test_main_fetch_writes_outputs_project_and_zip(self) -> None:
        fake_client = FakeAPIClient(
            study_payload={
                'accession_id': 'EGAS50000000906',
                'title': 'SweGen',
            },
            dataset_payload=[
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

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir) / 'out'
            project_file = Path(tmp_dir) / 'saved-project.json'
            zip_file = Path(tmp_dir) / 'export.zip'
            stdout_buffer = io.StringIO()

            with patch('metadata_enrichment_app.cli.EGAClient', return_value=fake_client):
                with redirect_stdout(stdout_buffer):
                    exit_code = main([
                        'fetch',
                        'EGAS50000000906',
                        str(output_dir),
                        '--creator', 'UU',
                        '--publisher', 'LU',
                        '--global-keyword', 'genomics',
                        '--dataset-keyword', 'EGAD50000001324=reference cohort',
                        '--exclude-dataset', 'EGAD50000001323',
                        '--site-name', 'NBIS Data Portal',
                        '--site-base-url', 'https://example.org',
                        '--project-file', str(project_file),
                        '--zip-file', str(zip_file),
                    ])

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                ['EGAD50000001324.qmd'],
            )
            self.assertTrue(project_file.exists())
            self.assertTrue(zip_file.exists())
            self.assertIn('NBIS Data Portal', (output_dir / 'EGAD50000001324.qmd').read_text(encoding='utf-8'))
            with zipfile.ZipFile(zip_file) as archive:
                self.assertEqual(
                    sorted(archive.namelist()),
                    ['EGAD50000001324.qmd'],
                )
            self.assertIn(f'Wrote {project_file}', stdout_buffer.getvalue())

    def test_main_project_regenerates_from_saved_snapshot(self) -> None:
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
        project = build_export_project(
            study_id='EGAS50000000906',
            study_context=study_context,
            creator_orgs=['UU'],
            source_orgs=['LU'],
            publisher_org='LU',
            export_config=ExportConfig(
                site_name='NBIS Data Portal',
                site_base_url='https://example.org',
            ),
            global_keywords=['genomics'],
            dataset_keywords_by_accession={'EGAD50000001323': ['reference cohort']},
            selected_accessions={'EGAD50000001323'},
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            project_path = Path(tmp_dir) / 'project.json'
            output_dir = Path(tmp_dir) / 'out'
            project_path.write_text(serialize_export_project(project), encoding='utf-8')
            stderr_buffer = io.StringIO()

            with redirect_stderr(stderr_buffer):
                exit_code = main([
                    'project',
                    str(project_path),
                    str(output_dir),
                ])

            self.assertEqual(exit_code, 0)
            self.assertTrue((output_dir / 'EGAD50000001323.qmd').exists())
            self.assertEqual(stderr_buffer.getvalue(), '')

    def test_main_fetch_reports_request_exception(self) -> None:
        stderr_buffer = io.StringIO()

        with patch(
            'metadata_enrichment_app.cli.EGAClient',
            side_effect=requests.exceptions.ConnectionError('lookup failed'),
        ):
            with redirect_stderr(stderr_buffer):
                exit_code = main([
                    'fetch',
                    'EGAS50000000906',
                    'tmp',
                    '--creator', 'UU',
                    '--publisher', 'LU',
                ])

        self.assertEqual(exit_code, 1)
        self.assertIn(
            'Failed to fetch metadata from the EGA API: lookup failed',
            stderr_buffer.getvalue(),
        )
