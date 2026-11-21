#!/usr/bin/env python3
"""
LED Stream Package - module entrypoint.
Runs the LED Stream Server if no args, otherwise runs CLI.
"""

import asyncio
import sys

if len(sys.argv) == 1:
    # No arguments - run the server
    from .server import LEDStreamServer

    asyncio.run(LEDStreamServer().start())
else:
    # Arguments provided - run CLI
    from .cli import main

    sys.exit(main())
