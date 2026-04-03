import unittest

from metadata_export.researchdata_se import ExportConfig, StudyContext, build_export_project
from metadata_export_app.state import (
    build_preview_dataset_from_project,
    collect_effective_dataset_keywords_by_accession,
    collect_dataset_keywords_by_accession,
    collect_global_keywords,
    collect_selected_accessions,
    find_selected_accessions_missing_keywords,
    get_export_validation_message,
    get_publisher_select_index,
    initialize_dataset_state,
    initialize_form_state_defaults,
    parse_keywords,
    restore_project_to_session_state,
)


class MetadataExportAppStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.study_context = StudyContext(
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

    def test_parse_keywords_splits_on_commas_and_newlines(self) -> None:
        self.assertEqual(
            parse_keywords('genomics, population genetics\nwhole genome'),
            ['genomics', 'population genetics', 'whole genome'],
        )

    def test_initialize_form_state_defaults_sets_empty_publisher_without_overwriting(self) -> None:
        session_state = {
            'site_base_url': 'https://example.org',
            'publisher_org': 'LU',
        }

        initialize_form_state_defaults(session_state)

        self.assertEqual(session_state['creator_orgs'], [])
        self.assertEqual(session_state['global_keywords_raw'], '')
        self.assertEqual(session_state['publisher_org'], 'LU')
        self.assertEqual(session_state['site_base_url'], 'https://example.org')
        self.assertEqual(session_state['sitemap_filename'], 'sitemap.xml')
        self.assertEqual(session_state['output_dir'], 'tmp/streamlit-export')

        fresh_session_state = {}
        initialize_form_state_defaults(fresh_session_state)
        self.assertEqual(fresh_session_state['creator_orgs'], [])
        self.assertEqual(fresh_session_state['global_keywords_raw'], '')
        self.assertIsNone(fresh_session_state['publisher_org'])

    def test_collect_global_keywords_parses_shared_keywords(self) -> None:
        session_state = {'global_keywords_raw': 'genomics\nwhole genome, population genetics'}

        self.assertEqual(
            collect_global_keywords(session_state),
            ['genomics', 'whole genome', 'population genetics'],
        )

    def test_initialize_dataset_state_sets_defaults_without_overwriting_existing_values(self) -> None:
        session_state = {
            'include_EGAD50000001323': False,
            'keywords_EGAD50000001323': 'kept keyword',
        }

        initialize_dataset_state(self.study_context, session_state)

        self.assertEqual(session_state['include_EGAD50000001323'], False)
        self.assertEqual(session_state['keywords_EGAD50000001323'], 'kept keyword')
        self.assertEqual(session_state['include_EGAD50000001324'], True)
        self.assertEqual(session_state['keywords_EGAD50000001324'], '')

    def test_restore_project_to_session_state_restores_selection_and_keywords(self) -> None:
        project = build_export_project(
            study_id='EGAS50000000906',
            study_context=self.study_context,
            creator_orgs=['UU', 'LU'],
            publisher_org='BTB',
            export_config=ExportConfig(
                site_base_url='https://example.org',
                sitemap_filename='catalogue-sitemap.xml',
            ),
            global_keywords=['genomics'],
            dataset_keywords_by_accession={
                'EGAD50000001323': ['population genetics'],
                'EGAD50000001324': ['reference cohort', 'whole genome'],
            },
            selected_accessions={'EGAD50000001324'},
        )
        session_state = {
            'artifacts': 'stale',
            'project_json': 'stale',
            'project': 'stale',
        }

        restore_project_to_session_state(project, session_state)

        self.assertEqual(session_state['loaded_study_id'], 'EGAS50000000906')
        self.assertEqual(session_state['creator_orgs'], ['UU', 'LU'])
        self.assertEqual(session_state['publisher_org'], 'BTB')
        self.assertEqual(session_state['global_keywords_raw'], 'genomics')
        self.assertEqual(session_state['site_base_url'], 'https://example.org')
        self.assertEqual(session_state['sitemap_filename'], 'catalogue-sitemap.xml')
        self.assertEqual(session_state['include_EGAD50000001323'], False)
        self.assertEqual(session_state['include_EGAD50000001324'], True)
        self.assertEqual(session_state['keywords_EGAD50000001323'], 'population genetics')
        self.assertEqual(
            session_state['keywords_EGAD50000001324'],
            'reference cohort, whole genome',
        )
        self.assertNotIn('artifacts', session_state)
        self.assertNotIn('project_json', session_state)
        self.assertNotIn('project', session_state)

    def test_build_preview_dataset_from_project_uses_project_snapshot(self) -> None:
        project = build_export_project(
            study_id='EGAS50000000906',
            study_context=self.study_context,
            creator_orgs=['UU', 'LU'],
            publisher_org='BTB',
            export_config=ExportConfig(
                site_base_url='https://example.org',
                sitemap_filename='catalogue-sitemap.xml',
            ),
            global_keywords=['genomics'],
            dataset_keywords_by_accession={
                'EGAD50000001323': ['population genetics'],
                'EGAD50000001324': ['reference cohort', 'whole genome'],
            },
            selected_accessions={'EGAD50000001324'},
        )

        preview_dataset = build_preview_dataset_from_project(project, 'EGAD50000001324')

        self.assertEqual(preview_dataset['name'], 'Dataset B')
        self.assertEqual(preview_dataset['publisher']['name'], 'The Swedish Childhood Tumor Biobank')
        self.assertEqual(
            preview_dataset['keywords'],
            ['genomics', 'reference cohort', 'whole genome'],
        )
        self.assertEqual(preview_dataset['includedInDataCatalog']['url'], 'https://example.org')

    def test_collect_selected_accessions_and_missing_keywords_follow_dataset_order(self) -> None:
        session_state = {
            'include_EGAD50000001323': True,
            'keywords_EGAD50000001323': '',
            'include_EGAD50000001324': True,
            'keywords_EGAD50000001324': 'reference cohort',
        }

        selected_accessions = collect_selected_accessions(self.study_context, session_state)
        global_keywords = ['genomics']
        dataset_keywords_by_accession = collect_dataset_keywords_by_accession(
            self.study_context,
            session_state,
        )
        effective_dataset_keywords_by_accession = collect_effective_dataset_keywords_by_accession(
            self.study_context,
            global_keywords,
            dataset_keywords_by_accession,
        )
        missing_keywords = find_selected_accessions_missing_keywords(
            self.study_context,
            selected_accessions,
            effective_dataset_keywords_by_accession,
        )

        self.assertEqual(selected_accessions, {'EGAD50000001323', 'EGAD50000001324'})
        self.assertEqual(
            dataset_keywords_by_accession,
            {
                'EGAD50000001323': [],
                'EGAD50000001324': ['reference cohort'],
            },
        )
        self.assertEqual(
            effective_dataset_keywords_by_accession,
            {
                'EGAD50000001323': ['genomics'],
                'EGAD50000001324': ['genomics', 'reference cohort'],
            },
        )
        self.assertEqual(missing_keywords, [])

    def test_get_export_validation_message_checks_active_requirements_in_order(self) -> None:
        self.assertEqual(
            get_export_validation_message([], 'UU', {'EGAD1'}, []),
            'Select at least one creator before generating export files.',
        )
        self.assertEqual(
            get_export_validation_message(['UU'], None, {'EGAD1'}, []),
            'Select a publisher before generating export files.',
        )
        self.assertEqual(
            get_export_validation_message(['UU'], 'LU', set(), []),
            'Select at least one dataset to include in the export.',
        )
        self.assertEqual(
            get_export_validation_message(['UU'], 'LU', {'EGAD1'}, ['EGAD1']),
            'Add at least one keyword for every selected dataset before generating export files. '
            'Missing keywords for: EGAD1.',
        )
        self.assertIsNone(
            get_export_validation_message(['UU'], 'LU', {'EGAD1'}, []),
        )

    def test_get_publisher_select_index_requires_valid_saved_value(self) -> None:
        self.assertIsNone(get_publisher_select_index(None))
        self.assertIsNone(get_publisher_select_index('FEGA-SE'))
        self.assertEqual(get_publisher_select_index('LU'), 1)


if __name__ == '__main__':
    unittest.main()
