import tempfile
import unittest
import xml.etree.ElementTree as ET

from pathlib import Path

from metadata_export.researchdata_se import (
    ExportedDataset,
    build_sitemap_entries,
    compose_yaml_front_matter,
    parse_args,
    transform_ega_dataset,
    write_sitemap_file,
)


SITEMAP_XMLNS = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}


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


if __name__ == '__main__':
    unittest.main()
