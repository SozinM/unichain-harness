#!/usr/bin/env python3
"""Entry point: `python3 run_mock_cl.py [max_blocks]`."""

import sys

from mock_cl.driver import run


if __name__ == "__main__":
    max_blocks = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(max_blocks=max_blocks)
