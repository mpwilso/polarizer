"""`polarizer serve` with a shorter interval between progress notifications for held calls, for
the stdio progress test (docs/HOLD-SPEC.md, section 6: "The interval can be injected for
tests"). Usage: fast_serve.py <seconds> serve --config <path>. Everything else is serve as it
ships; only the module constant the gateway reads is replaced, before the gateway is made."""

import sys

from polarizer import cli, proxy

if __name__ == "__main__":
    proxy.HOLD_PROGRESS_INTERVAL = float(sys.argv[1])
    sys.exit(cli.main(sys.argv[2:]))
