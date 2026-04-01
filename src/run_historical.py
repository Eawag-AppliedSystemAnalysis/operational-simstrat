# -*- coding: utf-8 -*-
"""
Run N one-year (March-to-March) cold-start simulations for a specified lake.
Each year uses its own simulation_dir so outputs are isolated and runs can be parallel.

Usage:
    python src/run_historical.py <lake_key> [options]

Options:
    --start-year YYYY   First year (default: current_year - 20)
    --num-years N       Number of annual simulations (default: 20)
    --max-workers N     Parallel year runs (default: 4)
    --args-file NAME    Args file in /args to use (default: historical)

Outputs: runs/historical/<lake>/<YYYY>-<YYYY+1>/
"""
import os
import sys
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from functions.verify import verify_arg_file
import main as simstrat_main


def parse_args():
    current_year = datetime.now().year
    parser = argparse.ArgumentParser()
    parser.add_argument("lake", type=str)
    parser.add_argument("--start-year", type=int, default=current_year - 21)
    parser.add_argument("--num-years", type=int, default=20)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--args-file", type=str, default="historical")
    return parser.parse_args()


def run_year(lake, year_start, year_end, arg_file, repo_dir):
    year_label = "{}-{}".format(year_start.year, year_end.year)
    simulation_dir = os.path.join(repo_dir, "runs", "historical", lake, year_label)
    simstrat_main.main(
        arg_file=arg_file,
        overwrite_args={
            "lakes": [lake],
            "overwrite_start_date": year_start.strftime("%Y%m%d"),
            "overwrite_end_date": year_end.strftime("%Y%m%d"),
            "simulation_dir": simulation_dir,
        },
    )
    return year_label


def run_historical(lake, start_year, num_years, max_workers, args_file_name):
    arg_file = verify_arg_file(args_file_name)
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    periods = [
        (date(start_year + i, 3, 16), date(start_year + i + 1, 3, 16))
        for i in range(num_years)
    ]

    print("\n" + "=" * 60)
    print("Lake: {}  |  {} years from March {}  |  {} workers".format(
        lake, num_years, start_year, max_workers))
    print("Output: runs/historical/{}/".format(lake))
    print("=" * 60)

    failed = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_year, lake, start, end, arg_file, repo_dir): "{}-{}".format(start.year, end.year)
            for start, end in periods
        }
        for future in as_completed(futures):
            year_label = futures[future]
            try:
                future.result()
                print("OK: {}".format(year_label))
            except Exception as e:
                print("FAILED: {} — {}".format(year_label, e))
                failed.append(year_label)

    print("\n" + "=" * 60)
    print("{}/{} years completed.".format(num_years - len(failed), num_years))
    if failed:
        print("FAILED: {}".format(", ".join(sorted(failed))))
    print("=" * 60)


if __name__ == "__main__":
    args = parse_args()
    run_historical(args.lake, args.start_year, args.num_years, args.max_workers, args.args_file)
