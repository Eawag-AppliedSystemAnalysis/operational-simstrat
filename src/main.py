# -*- coding: utf-8 -*-
import os
import argparse
from functions.verify import verify_arg_file
from functions.parallel import run_parallel_tasks
from functions.general import process_args
from functions.log import Logger
from configuration import Config
from model import Simstrat


def task(parameters, args):
    model = Simstrat(parameters["key"], parameters, args)
    model.process()


def main(arg_file=False, overwrite_args=False):
    config = Config()
    args, lake_parameters = config.load(arg_file, overwrite_args)
    if args["log"]:
        log = Logger(path=args["simulation_dir"])
    else:
        log = Logger()
    log.initialise("Simstrat Operational")
    log.inputs("Arguments", args)
    run_parallel_tasks(lake_parameters, args, task, log)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Run Simstrat on an operational basis')
    parser.add_argument('arg_file', type=verify_arg_file, help='Name of the argument file in /args')
    parser.add_argument('args', nargs='*', metavar='key=value', help='Additional args in key=value format. This overwrites values in the argument file.')
    args = parser.parse_args()
    arg_file = args.arg_file
    overwrite_args = process_args(args)
    main(arg_file=arg_file, overwrite_args=overwrite_args)

