import os
import sys
import json
from functions import verify


class Config(object):
    def __init__(self):
        self.repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[0])))
        self.lake_parameters_dir = os.path.join(self.repo_dir, "static", "lake_parameters.json")
        self.lake_parameters = self.parse_lake_parameters()
        self.default_args = {
            "lakes": {"default": [k['key'] for k in self.lake_parameters], "verify": verify.verify_list, "desc": "List of lake keys to be processed"},
            "simulation_dir": {"default": os.path.join(self.repo_dir, "runs"), "verify": verify.verify_path, "desc": "Path to the simulation directory"},
            "simstrat_version": {"default": "3.0.4", "verify": verify.verify_string, "desc": "Version of Simstrat"},
            "couple_aed2": {"default": True, "verify": verify.verify_bool, "desc": "Couple water quality model AED2"},
            "forecast": {"default": True, "verify": verify.verify_bool, "desc": "Forecast values using meteo forecast"},
            "max_workers": {"default": 5, "verify": verify.verify_integer, "desc": "Number of parallel workers"},
            "snapshot": {"default": True, "verify": verify.verify_bool, "desc": "Restart run from snapshot if available"},
            "snapshot_date": {"default": False, "verify": verify.verify_date, "desc": "Snapshot date YYYYMMDD defaults to most recent"},
            "data_api": {"default": "http://eaw-alplakes2:8000", "verify": verify.verify_string, "desc": "Base URL for the Alplakes API"},
            "log": {"default": True, "verify": verify.verify_bool, "desc": "Output log to file"},
            "run": {"default": True, "verify": verify.verify_bool, "desc": "Run simulations"},
            "upload": {"default": False, "verify": verify.verify_bool, "desc": "Upload results to server"},
            "server_host": {"default": "eaw-alplakes2", "verify": verify.verify_string, "desc": "Upload server host name"},
            "server_user": {"default": "alplakes", "verify": verify.verify_string, "desc": "Upload server user name"},
            "server_password": {"default": False, "verify": verify.verify_string, "desc": "Upload server password"},
            "debug": {"default": False, "verify": verify.verify_bool, "desc": "Raise any errors in code for easier debugging"},
            "overwrite_simulation": {"default": False, "verify": verify.verify_bool, "desc": "Remove existing simulation files and run full simulation"},
            "overwrite_start_date": {"default": False, "verify": verify.verify_date, "desc": "Overwrites the default start date and initialises from initial conditions and NOT a snapshot"},
            "overwrite_end_date": {"default": False, "verify": verify.verify_date, "desc": "Overwrite the default end date"},
            "results_folder_api": {"default": "/nfsmount/filesystem/media/simulations/simstrat", "verify": verify.verify_string, "desc": "Server path to upload results"},
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
        self.args["repo_dir"] = self.repo_dir
        return self.args, lake_parameters

    def parse_lake_parameters(self):
        with open(self.lake_parameters_dir) as f:
            lake_parameters = json.load(f)
        return lake_parameters
