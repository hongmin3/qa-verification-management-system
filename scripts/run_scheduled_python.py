"""Run scheduled work without a console; retain the child exit code and latest failure."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--error-log', required=True, type=Path)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a child command is required')
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        code, output = result.returncode, result.stdout
    except OSError as exc:
        code, output = 1, type(exc).__name__.encode('utf-8')
    if code:
        args.error_log.parent.mkdir(parents=True, exist_ok=True)
        # Success is already recorded by each worker; idle checks must not grow a log.
        header = f'{datetime.now(timezone.utc).isoformat()} exit={code}\n'.encode('utf-8')
        args.error_log.write_bytes(header + output[-65536:])
    return code


if __name__ == '__main__':
    raise SystemExit(main())
