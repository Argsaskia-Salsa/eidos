#!/usr/bin/env python3
"""
Pose Stream Package - module entrypoint.
Runs the Pose Stream Server if no args, otherwise runs CLI.
"""

import asyncio
import sys

if len(sys.argv) == 1:
    # No arguments - run the server
    from .server import PoseStreamServer

    asyncio.run(PoseStreamServer().start())
else:
    # Arguments provided - run CLI
    from .cli import main

    sys.exit(main())
