"""Explicit local operator commands; runtime inference never downloads files."""
import argparse
import json
from pathlib import Path
import time
import sys

from app.embeddings.artifacts import download_artifact
from app.embeddings.experiments import synthetic_benchmark, token_inventory


def main():
    parser = argparse.ArgumentParser(description='Qwen local technical experiments')
    sub = parser.add_subparsers(dest='command', required=True)
    download = sub.add_parser('download')
    download.add_argument('--output-root', type=Path, default=Path('data/models'))
    for name in ('benchmark', 'inventory'):
        command = sub.add_parser(name)
        command.add_argument('--artifact', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True)
        if name == 'inventory':
            command.add_argument('--staging', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'download':
        last = 0
        def progress(name, received, size):
            nonlocal last
            now = time.monotonic()
            if now - last >= 10 or received == size:
                print(json.dumps({'file': name, 'downloaded_bytes': received, 'expected_bytes': size}), flush=True)
                last = now
        directory = download_artifact(args.output_root, progress)
        print(json.dumps({'artifact_directory': str(directory), 'verified': True}))
        return
    if args.output.exists():
        parser.error('Report destination already exists; use a new run path')
    report = (synthetic_benchmark(args.artifact) if args.command == 'benchmark'
              else token_inventory(args.artifact, args.staging))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'report': str(args.output), 'published': False, 'legal_quality_certified': False}))


def run_cli():
    try:
        main()
        return 0
    except Exception:
        # Validation exceptions may contain original contract text. Do not render
        # arbitrary exception messages or tracebacks in the operator flow.
        print('Qwen diagnostic failed; no successful report was published.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(run_cli())
