#!/usr/bin/env python3

"""CLI tool for exporting metadata to be harvested by https://researchdata.se."""

import sys

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from metadata_export_core.core import *  # noqa: F401,F403


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
