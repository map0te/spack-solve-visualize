# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
import argparse
import sys
import time
import warnings
from typing import Dict, List, Optional, Tuple

import spack.cmd
import spack.config
import spack.llnl.util.lang
import spack.solver.asp as asp
import spack.spec
from spack.cmd.common.arguments import add_concretizer_args
from spack.llnl.util import tty
from spack.solver.asp import ErrorHandler, PyclingoDriver, Result, SpecBuilder, UnsatisfiableSpecError, build_criteria_names
from spack.solver.core import extract_args


level = "long"
section = "developer"
description = "visualize intermediate models during spec solving"


def setup_parser(subparser: argparse.ArgumentParser):
    subparser.add_argument(
        "spec",
        help="spec to solve and visualize intermediate models",
    )
    subparser.add_argument(
        "-o",
        "--output",
        help="write output to file instead of stdout",
        default=None,
    )
    add_concretizer_args(subparser)


models = []

def capturing_run_clingo(self, specs_arg, setup_arg, problem_str, control_file_paths, timer):
    with timer.measure("load"):
        self.control.add("base", [], problem_str)
        for path in control_file_paths:
            self.control.load(path)

    with timer.measure("ground"):
        self.control.ground([("base", [])])

    def on_model(model):
        models.append((model.cost, model.symbols(shown=True, terms=True), model.number))

    timer.start("solve")
    time_limit = spack.config.CONFIG.get("concretizer:timeout", 0)
    timeout_end = time.monotonic() + time_limit if time_limit > 0 else float("inf")
    error_on_timeout = spack.config.CONFIG.get("concretizer:error_on_timeout", True)

    with self.control.solve(on_model=on_model, async_=True) as handle:
        finished = False
        while not finished and time.monotonic() < timeout_end:
            finished = handle.wait(1.0)

        if not finished:
            specs_str = ", ".join(spack.llnl.util.lang.elide_list([str(s) for s in specs_arg], 4))
            header = f"Spack is taking more than {time_limit} seconds to solve for {specs_str}"
            if error_on_timeout:
                raise UnsatisfiableSpecError(f"{header}, stopping concretization")
            warnings.warn(f"{header}, using the best configuration found so far")
            handle.cancel()

        solve_result = handle.get()
    timer.stop("solve")

    result = Result(specs_arg)
    result.satisfiable = solve_result.satisfiable
    if not result.satisfiable:
        return result

    timer.start("construct_specs")
    builder = SpecBuilder(specs_arg, hash_lookup=setup_arg.reusable_and_possible)
    min_cost, best_model, _ = min(models)

    error_handler = ErrorHandler(best_model, specs_arg)
    error_handler.raise_if_errors()

    spec_attrs = [(name, tuple(rest)) for name, *rest in extract_args(best_model, "attr")]
    spec_dict = builder.build_specs(spec_attrs)

    result.answers.append((list(min_cost), 0, spec_dict))
    criteria_args = extract_args(best_model, "opt_criterion")
    result.criteria = build_criteria_names(min_cost, criteria_args)
    result.nmodels = len(models)
    result.possible_dependencies = setup_arg.pkgs
    timer.stop("construct_specs")
    timer.stop()

    return result


def process_model_to_specs(symbols, original_specs, reusable_and_possible):
    builder = SpecBuilder(original_specs, hash_lookup=reusable_and_possible)
    spec_attrs = [(name, tuple(rest)) for name, *rest in extract_args(symbols, "attr")]
    spec_dict = builder.build_specs(spec_attrs)
    root_names = {s.name for s in original_specs}
    return [spec for key, spec in spec_dict.items() if key.id == "0" and spec.name in root_names]


def format_model_output(
    model_num: int,
    cost: tuple,
    specs: Optional[List[spack.spec.Spec]],
    use_color: bool = True,
) -> str:
    """Format a single model for display."""
    header = "=" * 77
    separator = "-" * 77
    cost_str = "[" + ", ".join(str(c) for c in cost) + "]"
    title = f"Model {model_num} - Cost: {cost_str}"

    output = f"\n{header}\n{title}\n{header}\n"

    if specs is None:
        output += "[Invalid or incomplete model]\n"
    else:
        tree_output = spack.spec.tree(
            specs,
            color=use_color,
            format=spack.spec.DISPLAY_FORMAT,
            hashlen=7,
            hashes=False,
            status_fn=None,
            show_types=False,
        )
        output += tree_output

    output += f"\n{separator}\n"
    return output


def solve_visualize(parser: argparse.ArgumentParser, args):
    """Solve a spec and display all intermediate models."""
    specs = spack.cmd.parse_specs(args.spec)
    if len(specs) != 1:
        tty.die("solve-visualize requires exactly one spec")

    models.clear()
    solver = asp.Solver()
    setup = asp.SpackSolverSetup()
    use_fresh = hasattr(args, 'concretizer_reuse') and args.concretizer_reuse is False
    reuse = [] if use_fresh else solver.selector.reusable_specs(specs)

    original_run_clingo = PyclingoDriver._run_clingo
    try:
        PyclingoDriver._run_clingo = capturing_run_clingo
        result, timer, _ = solver.driver.solve(setup, specs, reuse=reuse)
    except Exception as e:
        tty.die(f"Solve failed: {e}")
    finally:
        PyclingoDriver._run_clingo = original_run_clingo

    if not result.satisfiable:
        tty.warn("Solve was unsatisfiable.")

    use_color = sys.stdout.isatty() and args.output is None

    output_lines = []
    for i, (cost, symbols, num) in enumerate(models):
        specs_list = None
        if result.satisfiable:
            specs_list = process_model_to_specs(
                symbols, specs, setup.reusable_and_possible if hasattr(setup, "reusable_and_possible") else {}
            )

        output_lines.append(
            format_model_output(i + 1, cost, specs_list, use_color)
        )

    output_text = "".join(output_lines)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output_text)
    else:
        print(output_text, end="")
