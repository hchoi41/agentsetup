"""rotm.py: the one command line of rule-of-thumb-model (design section 3).

Run:  python -P -X utf8 -B <SKILL>/scripts/rotm.py <subcommand> [options]
Not with -I: on the build PC openpyxl lives in the user site-packages, which -I hides (design D1).

The subcommand table below is fixed by the design. Module lib/<m>.py defines add_<cmd>_arguments(parser)
and run_<cmd>(args) -> exit code for each subcommand it serves. Only the chosen module is imported.
Exit codes (every subcommand): 0 OK, 1 findings, 2 refused, 3 not available / not verified,
4 internal error, 5 Excel busy. Owner: INTEGRATOR (first version by the architect).
"""
from __future__ import annotations

import argparse
import difflib
import importlib
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# subcommand: (module in lib/, function prefix, one-line help, owner)
COMMANDS: dict[str, tuple[str, str, str, str]] = {
    "init": ("modelfolder", "init", "create a model folder and its marker file rotm_model.json", "CORE-IO"),
    "status": ("modelfolder", "status", "show contracts, last build, audit and check of a model folder", "CORE-IO"),
    "contract": ("contract", "contract", "propose a column contract, record the user's agreement, show it", "M5"),
    "land": ("land", "land", "land raw data per the agreed contract; write the SQL and Power Query texts", "M3"),
    "build": ("build", "build", "compile a model specification into the workbook", "M3"),
    "theme": ("theme", "theme", "apply the style dictionary to an existing workbook", "M2"),
    "audit": ("audit", "audit", "audit any workbook against the rules", "M4"),
    "check": ("check", "check", "raw against contracts, coverage, recalculation in Excel, every check read back", "M5"),
    "extend": ("check", "extend", "copy the last helper row down so every raw row is covered", "M5"),
    "compat": ("compat", "compat", "make a workbook work in both Excel and Google Sheets", "M7"),
    "trace": ("trace", "trace", "the chain from one cell back to the raw rows, in words", "M7"),
    "split": ("split", "split", "rewrite one over-limit formula as helper cells", "M7"),
    "lift": ("lift", "lift", "write a Python script that reproduces the helpers and ties out to the workbook", "M8"),
    "diff": ("diff", "diff", "formulas, inputs and outputs that changed between two workbooks", "M8"),
    "document": ("document", "document", "write the model map (MODEL_MAP.md)", "M8"),
    "recalc": ("excel", "recalc", "recalculate in Excel, save, read check cells and error values", "CORE-X"),
    "render": ("excel", "render", "render one PNG per sheet through Excel", "CORE-X"),
    "formula": ("formula", "formula", "measure one formula: depth, chain, length, literals, the part to move", "CORE-F"),
    "selftest": ("selftest", "selftest", "run the plugin's own tests and checks", "INTEGRATOR"),
}

EXIT_REFUSED = 2
EXIT_NOT_AVAILABLE = 3
EXIT_INTERNAL = 4


def usage() -> str:
    lines = ["usage: python -P -X utf8 -B rotm.py <subcommand> [options]", "",
             "subcommands:"]
    width = max(len(k) for k in COMMANDS)
    for name, (_mod, _prefix, text, _owner) in COMMANDS.items():
        lines.append(f"  {name.ljust(width)}  {text}")
    lines += ["", "rotm.py <subcommand> -h shows the options of one subcommand.",
              "exit codes: 0 OK, 1 findings, 2 refused, 3 not available or not verified, 4 internal error, 5 Excel busy"]
    return "\n".join(lines)


def _streams_safe() -> None:
    """Never fail on a character the console cannot show."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _streams_safe()
    if sys.version_info < (3, 11):
        sys.stderr.write("rotm.py: needs Python 3.11 or later; this is "
                         f"{sys.version_info.major}.{sys.version_info.minor}.\n")
        return EXIT_NOT_AVAILABLE
    args_in = list(sys.argv[1:] if argv is None else argv)
    if not args_in or args_in[0] in ("-h", "--help", "help"):
        print(usage())
        return 0
    if args_in[0] == "--version":
        from lib import common
        print(f"{common.TOOL} {common.VERSION}")
        return 0
    name = args_in[0]
    if name not in COMMANDS:
        near = difflib.get_close_matches(name, list(COMMANDS), n=1, cutoff=0.5)
        hint = f"; did you mean '{near[0]}'?" if near else "; run rotm.py -h for the list"
        sys.stderr.write(f"rotm.py: unknown subcommand '{name}'{hint}\n")
        return EXIT_REFUSED
    module_name, prefix, help_text, _owner = COMMANDS[name]
    try:
        from lib import common
        module = importlib.import_module(f"lib.{module_name}")
    except Exception as exc:  # noqa: BLE001 - one line, no traceback (design 3.2)
        sys.stderr.write(f"rotm.py {name}: cannot load lib.{module_name}: {type(exc).__name__}: {exc}\n")
        return EXIT_INTERNAL
    parser = argparse.ArgumentParser(prog=f"rotm.py {name}", description=help_text)
    getattr(module, f"add_{prefix}_arguments")(parser)
    args = parser.parse_args(args_in[1:])
    args.command = name
    return common.run_command(getattr(module, f"run_{prefix}"), args, name=name)


if __name__ == "__main__":
    sys.exit(main())
