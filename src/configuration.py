import os
import sys
import json
from functions.verify import verify_path, verify_bool, verify_date, verify_list, verify_integer


class Config(object):
    def __init__(self):
        self.repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[0])))
        self.lake_parameters_dir = os.path.join(self.repo_dir, "static", "lake_parameters.json")
        self.lake_parameters = self.parse_lake_parameters()
        self.default_args = {
            "lakes": {"default": [k['key'] for k in self.lake_parameters], "verify": verify_list, "desc": "List of lake keys to be processed"},
            "simulation_dir": {"default": os.path.join(self.repo_dir, "runs"), "verify": verify_path, "desc": "Path to the simulation directory"},
            "max_workers": {"default": 5, "verify": verify_integer, "desc": "Number of parallel workers"},
            "snapshot": {"default": True, "verify": verify_bool, "desc": "Restart run from snapshot if available"},
            "snapshot_date": {"default": False, "verify": verify_date, "desc": "Snapshot date YYYYMMDD defaults to most recent"},
            "log": {"default": True, "verify": verify_bool, "desc": "Output log to file"},
            "debug": {"default": False, "verify": verify_bool, "desc": "Raise any errors in code for easier debugging"},
            "overwrite": {"default": False, "verify": verify_bool, "desc": "Remove existing simulation files and run full simulation"},
        }
        self.args = {k: v["default"] for k, v in self.default_args.items()}

    def load(self, arg_file, overwrite_args):
        if arg_file:
            try:
                with open(arg_file) as f:
                    args = json.load(f)
            except:
                raise ValueError("Failed to parse {}. Verify it is a valid json file.")
            for key in args.keys():
                if key in self.default_args:
                    self.default_args[key]["verify"](args[key])
                self.args[key] = args[key]
        if isinstance(overwrite_args, dict):
            for key in overwrite_args.keys():
                if key in self.default_args:
                    self.default_args[key]["verify"](overwrite_args[key])
                self.args[key] = overwrite_args[key]

        default_lakes = [k['key'] for k in self.lake_parameters]
        for lake in self.args["lakes"]:
            if lake not in default_lakes:
                raise ValueError('Lake key "{}" does not exist in lake_parameters.json, please select a different lake '
                                 'or add its properties to lake_parameters.json'.format(lake))
        lake_parameters = [lake for lake in self.lake_parameters if lake['key'] in self.args["lakes"]]
        return self.args, lake_parameters

    def parse_lake_parameters(self):
        with open(self.lake_parameters_dir) as f:
            lake_parameters = json.load(f)
        return lake_parameters
