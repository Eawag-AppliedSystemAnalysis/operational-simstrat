# -*- coding: utf-8 -*-
import time
import argparse
from functions.verify import verify_arg_file
from functions.parallel import run_parallel_tasks
from functions.log import Logger
from configuration import Config


def task(parameters, args):
    print(parameters["key"])
    time.sleep(2)


def main(arg_file=False, overwrite_args=False):
    config = Config()
    args, lake_parameters = config.load(arg_file, overwrite_args)
    log = Logger(path=args["simulation_dir"])
    log.initialise("Simstrat Operational")
    log.args(args)
    run_parallel_tasks(lake_parameters, args, task, log)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run Simstrat on an operational basis')
    parser.add_argument('arg_file', type=verify_arg_file, help='Name of the argument file in /args')
    parser.add_argument('args', nargs='*', metavar='key=value', help='Additional args in key=value format. This overwrites values in the argument file.')
    args = parser.parse_args()
    arg_file = args.arg_file
    overwrite_args = dict(arg.split('=') for arg in args.args)
    main(arg_file=arg_file, overwrite_args=overwrite_args)
