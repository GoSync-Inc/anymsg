"""Stable public name for the existing local-only operator CLI."""
import sys


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--version"]:
        from . import __version__
        print("anymsg " + __version__)
        return 0
    if arguments and arguments[0] == "whatsapp-assets":
        import argparse
        import json
        from .assets import export_whatsapp_assets
        parser=argparse.ArgumentParser(prog='anymsg whatsapp-assets',
            description='Export Node sources to a new private directory; no install or login is performed.')
        parser.add_argument('--directory',required=True)
        args=parser.parse_args(arguments[1:])
        try:result=export_whatsapp_assets(args.directory)
        except (ValueError,OSError):
            parser.exit(1,'anymsg: asset_export_failed (use a new directory under existing nonsymlink parents)\n')
        print(json.dumps(result))
        return 0
    if arguments in (["--help"], ["-h"]):
        print("AnyMsg extras: --version; whatsapp-assets --directory <new private directory>\n")
    from messaging_service.__main__ import main as operator_main
    previous = sys.argv
    try:
        sys.argv = ["anymsg", *arguments]
        return operator_main()
    finally:
        sys.argv = previous
