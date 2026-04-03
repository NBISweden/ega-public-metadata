#!/usr/bin/env python3

"""Command-line interface for the Streamlit-style metadata export workflow."""

from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from metadata_export_core.core import (
    DEFAULT_SITE_BASE_URL,
    DEFAULT_SITE_NAME,
    DEFAULT_SITEMAP_FILENAME,
    EGAClient,
    ExportConfig,
    GeneratedFile,
    MetadataValidationError,
    ORGANISATIONS,
    PUBLISHER_ORGANISATIONS,
    build_export_artifacts_from_project,
    build_export_project,
    build_export_zip_bytes,
    deserialize_export_project,
    ensure_output_dir,
    fetch_study_context,
    serialize_export_project,
    validate_iso_date_string,
    write_export_artifacts,
)
from metadata_export_app.state import build_export_archive_filename, build_project_filename


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='metadata-export-app',
        description='Command-line workflow for the Streamlit-style FEGA Sweden metadata export.',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    fetch_parser = subparsers.add_parser(
        'fetch',
        help='Fetch an EGA study, enrich metadata, and export qmd files plus sitemap.',
    )
    fetch_parser.add_argument('study_id', help='EGA Study accession number')
    fetch_parser.add_argument('output_dir', help='Directory where qmd files and sitemap should be written')
    fetch_parser.add_argument(
        '--creator',
        choices=ORGANISATIONS.keys(),
        required=True,
        action='append',
        help='Organisation that created or collected the data; repeat for multiple creators',
    )
    fetch_parser.add_argument(
        '--publisher',
        choices=PUBLISHER_ORGANISATIONS,
        required=True,
        help='Organisation responsible for publishing the dataset metadata record',
    )
    fetch_parser.add_argument(
        '--global-keyword',
        action='append',
        dest='global_keywords',
        default=[],
        metavar='KEYWORD',
        help='Keyword applied to every selected dataset; repeat for multiple keywords',
    )
    fetch_parser.add_argument(
        '--dataset-keyword',
        action='append',
        dest='dataset_keywords',
        default=[],
        metavar='ACCESSION=KEYWORD',
        help='Additional keyword for a specific dataset; repeat as needed',
    )
    fetch_parser.add_argument(
        '--include-dataset',
        action='append',
        dest='include_accessions',
        default=[],
        metavar='ACCESSION',
        help='Restrict the export to the listed dataset accession(s)',
    )
    fetch_parser.add_argument(
        '--exclude-dataset',
        action='append',
        dest='exclude_accessions',
        default=[],
        metavar='ACCESSION',
        help='Exclude a dataset accession from the export',
    )
    fetch_parser.add_argument(
        '--site-name',
        default=DEFAULT_SITE_NAME,
        help='Catalog name used for includedInDataCatalog and sdPublisher',
    )
    fetch_parser.add_argument(
        '--site-base-url',
        default=DEFAULT_SITE_BASE_URL,
        help='Base URL used for generated dataset landing-page links',
    )
    fetch_parser.add_argument(
        '--sitemap-filename',
        default=DEFAULT_SITEMAP_FILENAME,
        help='Filename for the generated sitemap XML',
    )
    fetch_parser.add_argument(
        '--sitemap-lastmod',
        help='Optional YYYY-MM-DD value used for sitemap <lastmod>',
    )
    fetch_parser.add_argument(
        '--project-file',
        help='Optional path where the generated project snapshot JSON should be written',
    )
    fetch_parser.add_argument(
        '--zip-file',
        help='Optional path where an export archive containing qmd files, sitemap, and project JSON should be written',
    )

    project_parser = subparsers.add_parser(
        'project',
        help='Regenerate export artifacts from a saved project snapshot JSON file.',
    )
    project_parser.add_argument('project_file', help='Saved project snapshot JSON file')
    project_parser.add_argument('output_dir', help='Directory where qmd files and sitemap should be written')
    project_parser.add_argument(
        '--zip-file',
        help='Optional path where an export archive containing qmd files, sitemap, and project JSON should be written',
    )

    return parser


def parse_dataset_keyword_assignments(assignments: list[str]) -> dict[str, list[str]]:
    keywords_by_accession: dict[str, list[str]] = {}
    for assignment in assignments:
        accession_id, separator, keyword = assignment.partition('=')
        accession_id = accession_id.strip()
        keyword = keyword.strip()
        if separator != '=' or not accession_id or not keyword:
            raise MetadataValidationError(
                f'Invalid dataset keyword assignment "{assignment}"; expected ACCESSION=KEYWORD'
            )
        keywords_by_accession.setdefault(accession_id, []).append(keyword)
    return keywords_by_accession


def resolve_selected_accessions(
    all_accessions: set[str],
    include_accessions: list[str],
    exclude_accessions: list[str],
) -> set[str]:
    include_set = set(include_accessions)
    exclude_set = set(exclude_accessions)
    unknown_accessions = (include_set | exclude_set) - all_accessions
    if unknown_accessions:
        raise MetadataValidationError(
            f'Unknown dataset accession(s): {", ".join(sorted(unknown_accessions))}'
        )
    selected_accessions = include_set if include_set else set(all_accessions)
    selected_accessions -= exclude_set
    if not selected_accessions:
        raise MetadataValidationError('Select at least one dataset to include in the export.')
    return selected_accessions


def write_text_file(path_str: str, content: str) -> None:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    print(f'Wrote {path}')


def write_bytes_file(path_str: str, content: bytes) -> None:
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    print(f'Wrote {path}')


def run_fetch_command(args: argparse.Namespace) -> None:
    sitemap_lastmod = None
    if args.sitemap_lastmod:
        sitemap_lastmod = validate_iso_date_string(args.sitemap_lastmod, 'sitemap_lastmod')

    with EGAClient() as client:
        study_context = fetch_study_context(client, args.study_id)

    all_accessions = {dataset['accession_id'] for dataset in study_context.datasets}
    dataset_keywords_by_accession = parse_dataset_keyword_assignments(args.dataset_keywords)
    unknown_keyword_accessions = set(dataset_keywords_by_accession) - all_accessions
    if unknown_keyword_accessions:
        raise MetadataValidationError(
            'Dataset keyword assignments reference unknown dataset accession(s): '
            + ', '.join(sorted(unknown_keyword_accessions))
        )
    selected_accessions = resolve_selected_accessions(
        all_accessions,
        args.include_accessions,
        args.exclude_accessions,
    )

    export_config = ExportConfig(
        site_name=args.site_name.strip() or DEFAULT_SITE_NAME,
        site_base_url=args.site_base_url.rstrip('/'),
        sitemap_filename=args.sitemap_filename.strip() or DEFAULT_SITEMAP_FILENAME,
        sitemap_lastmod=sitemap_lastmod,
    )
    project = build_export_project(
        study_id=args.study_id,
        study_context=study_context,
        creator_orgs=args.creator,
        publisher_org=args.publisher,
        export_config=export_config,
        global_keywords=args.global_keywords,
        dataset_keywords_by_accession=dataset_keywords_by_accession,
        selected_accessions=selected_accessions,
    )
    project_json = serialize_export_project(project)
    artifacts = build_export_artifacts_from_project(project)

    output_dir = ensure_output_dir(args.output_dir)
    write_export_artifacts(output_dir, artifacts)

    if args.project_file:
        write_text_file(args.project_file, project_json)
    if args.zip_file:
        zip_bytes = build_export_zip_bytes(
            artifacts,
            extra_files=[
                GeneratedFile(
                    filename=build_project_filename(project['study_id']),
                    content=project_json,
                )
            ],
        )
        write_bytes_file(args.zip_file, zip_bytes)


def run_project_command(args: argparse.Namespace) -> None:
    project_path = Path(args.project_file)
    project_json = project_path.read_text(encoding='utf-8')
    project = deserialize_export_project(project_json)
    artifacts = build_export_artifacts_from_project(project)

    output_dir = ensure_output_dir(args.output_dir)
    write_export_artifacts(output_dir, artifacts)

    if args.zip_file:
        zip_bytes = build_export_zip_bytes(
            artifacts,
            extra_files=[
                GeneratedFile(
                    filename=build_project_filename(project['study_id']),
                    content=project_json,
                )
            ],
        )
        write_bytes_file(args.zip_file, zip_bytes)


def main(args: list[str] | None = None) -> int:
    parsed_args = build_parser().parse_args(args)
    try:
        if parsed_args.command == 'fetch':
            run_fetch_command(parsed_args)
        elif parsed_args.command == 'project':
            run_project_command(parsed_args)
        else:  # pragma: no cover
            raise MetadataValidationError(f'Unsupported command "{parsed_args.command}"')
    except FileNotFoundError as exc:
        print(f'File error: {exc}', file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f'Failed to fetch metadata from the EGA API: {exc}', file=sys.stderr)
        return 1
    except MetadataValidationError as exc:
        print(f'Metadata validation failed: {exc}', file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f'Failed to process metadata export: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
