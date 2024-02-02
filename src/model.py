import logging
import os
import shutil
import numpy as np
from datetime import datetime, timezone, timedelta

from functions import verify
from functions.log import Logger
from functions.write import (write_grid, write_bathymetry, write_output_depths, write_output_time_resolution,
                             write_initial_conditions, write_absorption, write_par_file_303, write_inflow, write_outflow)
from functions.bathymetry import bathymetry_from_file, bathymetry_from_datalakes
from functions.grid import grid_from_file
from functions.forcing import period_from_forcing
from functions.par import update_par_file_303
from functions.observations import (initial_conditions_from_observations, default_initial_conditions,
                                    absorption_from_observations, default_absorption)


class Simstrat(object):
    def __init__(self, key, parameters, args):
        self.key = key
        self.args = args
        self.simulation_dir = os.path.join(args["simulation_dir"], key)
        self.required_parameters = {
            "forcing": {"verify": verify.verify_forcing, "desc": "List of dicts describing the input forcing data"},
            "elevation": {"verify": verify.verify_float, "desc": "Elevation of lake above sea level (m a.s.l)"},
            "surface_area": {"verify": verify.verify_float, "desc": "Surface area of the lake (km2)"},
            "trophic_state": {"verify": verify.verify_string, "desc": "Trophic state of the lake e.g. Oligotrophic, Eutrophic"},
            "latitude": {"verify": verify.verify_float, "desc": "Latitude of the centroid of the lake (WGS 84)"},
            "longitude": {"verify": verify.verify_float, "desc": "Longitude of the centroid of the lake (WGS 84)"},
        }
        self.default_parameters = {
            "reference_date": {"default": "19810101", "verify": verify.verify_date, "desc": "Reference date YYYYMMDD of the model"},
            "model_time_resolution": {"default": 300, "verify": verify.verify_integer, "desc": "Timestep of the model (s)"},
            "salinity": {"default": 0.15, "verify": verify.verify_float, "desc": "Default salinity for intial conditions if not available in observations (ppt)"},
            "output_time_resolution": {"default": 10800, "verify": verify.verify_integer,"desc": "Output imestep of the model, should be evenly devisable by the model timestep (s)"},
        }
        self.optional_parameters = {
            "max_depth": {"verify": verify.verify_float, "desc": "Maximum depth of the lake (m)"},
            "grid_resolution": {"verify": verify.verify_float, "desc": "Vertical resolution of the simulation grid (m)"},
            "output_depth_resolution": {"verify": verify.verify_float, "desc": "Vertical resolution of the output file (m)"},
            "bathymetry": {"verify": verify.verify_dict, "desc": "Bathymetry data in the format { area: [12,13,...], depth: [0, 1,...] } where area is in m2 and depth in m"},
            "bathymetry_datalakes_id": {"verify": verify.verify_integer, "desc": "Datalakes ID for bathymetry profile"},
            "inputs": {"verify": verify.verify_inputs, "desc": "List of inputs described by dicts with discharge and temeperature"},
            "forcing_forecast": {"verify": verify.verify_forcing_forecast, "desc": "Dictionary proving source and model"},
            "absorption": {"verify": verify.verify_float, "desc": "Absorption coefficient when observation data not available"},
        }
        self.parameters = {k: v["default"] for k, v in self.default_parameters.items()}

        for key in self.required_parameters.keys():
            if key not in parameters:
                raise ValueError("Required parameter: {} not in parameters. {}".format(key, self.required_parameters[key]["desc"]))
            self.required_parameters[key]["verify"](parameters[key])
            self.parameters[key] = parameters[key]

        for key in parameters.keys():
            if key in self.default_parameters.keys():
                self.default_parameters[key]["verify"](parameters[key])
                self.parameters[key] = parameters[key]
            elif key in self.optional_parameters.keys():
                self.optional_parameters[key]["verify"](parameters[key])
                self.parameters[key] = parameters[key]

        self.snapshot = args["snapshot"]
        self.parameters["reference_date"] = datetime.strptime(self.parameters["reference_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
        self.start_date = self.parameters["reference_date"]
        self.end_date = datetime.now().replace(tzinfo=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        if os.path.exists(self.simulation_dir) and args["overwrite_simulation"]:
            shutil.rmtree(self.simulation_dir)
        if not os.path.exists(self.simulation_dir):
            os.makedirs(self.simulation_dir, exist_ok=True)

        if args["log"]:
            self.log = Logger(path=self.simulation_dir)
        else:
            self.log = Logger()

        self.log.initialise("Simstrat Operational - {}".format(self.key))
        self.log.inputs("Input Parameters", self.parameters)

    def process(self):
        self.create_bathymetry_file()
        self.create_grid_file()
        self.create_output_depths_file()
        self.create_output_time_resolution_file()
        self.set_simulation_run_period()
        if self.snapshot:
            self.prepare_snapshot()
        else:
            self.create_initial_conditions_file()
        if self.args["couple_aed2"]:
            self.create_aed2_files()
        self.create_absorption_file()
        self.create_forcing_file()
        self.create_inflow_files()
        self.create_par_file()

    def create_bathymetry_file(self):
        self.log.begin_stage("create_bathymetry_file")
        bathymetry_file = os.path.join(self.simulation_dir, "Bathymetry.dat")
        if os.path.exists(bathymetry_file):
            self.log.info("Bathymetry file exists, reading from file.", indent=1)
            bathymetry = bathymetry_from_file(bathymetry_file)
        else:
            if "bathymetry" in self.parameters:
                self.log.info("Bathymetry defined in parameters.", indent=1)
                bathymetry = self.parameters["bathymetry"]
            elif "bathymetry_datalakes_id" in self.parameters:
                self.log.info("Accessing bathymetry from Datalakes (id={})".format(self.parameters["bathymetry_datalakes_id"]), indent=1)
                bathymetry = bathymetry_from_datalakes(self.parameters["bathymetry_datalakes_id"])
            elif "max_depth" in self.parameters and "surface_area" in self.parameters:
                self.log.info("Using surface_area and max_depth for a simple two-point bathymetry", indent=1)
                bathymetry = {"area": [self.parameters["surface_area"] * 10**6, 0], "depth": [0, self.parameters["max_depth"]]}
            else:
                raise Exception("At least one of the following parameters must be provided: bathymetry, "
                                "bathymetry_datalakes_id, max_depth and surface_area")
            write_bathymetry(bathymetry, bathymetry_file)
        self.parameters["max_depth"] = max(bathymetry["depth"])
        self.log.info("Max depth set to {}m".format(self.parameters["max_depth"]), indent=1)
        self.log.end_stage()

    def create_grid_file(self):
        self.log.begin_stage("create_grid_file")
        grid_file = os.path.join(self.simulation_dir, "Grid.dat")
        if os.path.exists(grid_file):
            self.log.info("Grid file exists, reading from file", indent=1)
            self.parameters["grid_cells"] = grid_from_file(grid_file)
        else:
            if "grid_resolution" not in self.parameters:
                if self.parameters["max_depth"] > 20:
                    self.parameters["grid_resolution"] = 0.5
                elif self.parameters["max_depth"] > 10:
                    self.parameters["grid_resolution"] = 0.25
                elif self.parameters["max_depth"] > 5:
                    self.parameters["grid_resolution"] = 0.125
                else:
                    self.parameters["grid_resolution"] = 0.05
            self.log.info("Grid resolution set to {} m".format(self.parameters["grid_resolution"]), indent=1)
            self.parameters["grid_cells"] = np.ceil(abs(self.parameters["max_depth"] / self.parameters["grid_resolution"]))
            if self.parameters["grid_cells"] > 1000:
                self.log.info('Grid cells limited to 1000', indent=1)
                self.parameters["grid_cells"] = 1000
            write_grid(self.parameters["grid_cells"], grid_file)
        self.log.end_stage()

    def create_output_depths_file(self):
        self.log.begin_stage("create_output_depths_file")
        output_depths_file = os.path.join(self.simulation_dir, "z_out.dat")
        if os.path.exists(output_depths_file):
            self.log.info("Output depth resolution file exists, skipping creation", indent=1)
        else:
            if self.parameters["max_depth"] > 20:
                self.parameters["output_depth_resolution"] = 1
            elif self.parameters["max_depth"] > 10:
                self.parameters["output_depth_resolution"] = 0.5
            elif self.parameters["max_depth"] > 5:
                self.parameters["output_depth_resolution"] = 0.25
            else:
                self.parameters["output_depth_resolution"] = 0.1
            self.log.info("Output depth resolution set to {} m".format(self.parameters["output_depth_resolution"]), indent=1)
            depths = np.arange(0, self.parameters["max_depth"], self.parameters["output_depth_resolution"])
            write_output_depths(depths, output_depths_file)
        self.log.end_stage()

    def create_output_time_resolution_file(self):
        self.log.begin_stage("create_output_time_resolution_file")
        output_time_resolution_file = os.path.join(self.simulation_dir, "t_out.dat")
        if os.path.exists(output_time_resolution_file):
            self.log.info("Output time resolution file exists, skipping creation", indent=1)
        else:
            if not self.parameters["output_time_resolution"] % self.parameters["model_time_resolution"] == 0:
                raise Exception("Output time resolution must be a multiple of the model time resolution")
            output_time_steps = self.parameters["output_time_resolution"] / self.parameters["model_time_resolution"]
            write_output_time_resolution(output_time_steps, output_time_resolution_file)
        self.log.end_stage()

    def set_simulation_run_period(self):
        self.log.begin_stage("set_simulation_run_period")

        self.log.info("Retrieving forcing data extents", indent=1)
        forcing_start, forcing_end = period_from_forcing(self.parameters["forcing"], self.args["data_api"])
        self.log.info("Forcing timeframe: {} - {}".format(forcing_start, forcing_end), indent=2)

        if self.args["overwrite_start_date"]:
            overwrite_start_date = datetime.strptime(self.args["overwrite_start_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
            if overwrite_start_date < forcing_start:
                raise ValueError("Overwrite start date is outside of available forcing data")
            else:
                self.log.info("Setting start date based on overwrite start date {}".format(self.args["overwrite_start_date"]), indent=1)
                self.snapshot = False
                start_date = overwrite_start_date
        elif self.args["snapshot"]:
            if self.args["snapshot_date"]:
                self.log.info("Attempting to define start date by specific snapshot date {}".format(self.args["snapshot_date"]), indent=1)
                if not os.path.exists(os.path.join(self.simulation_dir, "simulation-snapshot_{}.dat".format(self.args["snapshot_date"]))):
                    self.log.info("Snapshot {} cannot be found, reverting to forcing period".format(self.args["snapshot_date"]), indent=2)
                    self.snapshot = False
                    start_date = forcing_start
                else:
                    self.log.info("Snapshot {} located".format(self.args["snapshot_date"]), indent=2)
                    start_date = datetime.strptime(self.args["snapshot_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
            else:
                self.log.info("Attempting to define start date by most recent snapshot", indent=1)
                snapshots = [f.split(".")[0].split("_")[-1] for f in os.listdir(self.simulation_dir) if "simulation-snapshot" in f]
                if len(snapshots) == 0:
                    self.log.info("No snapshots available, reverting to forcing period", indent=2)
                    self.snapshot = False
                    start_date = forcing_start
                else:
                    snapshots.sort()
                    self.log.info("Snapshot {} located".format(snapshots[-1]), indent=2)
                    self.args["snapshot_date"] = snapshots[-1]
                    start_date = datetime.strptime(snapshots[-1], "%Y%m%d").replace(tzinfo=timezone.utc)
        else:
            start_date = forcing_start

        if start_date < self.parameters["reference_date"]:
            raise ValueError("Start date cannot be before reference date")

        end_date = forcing_end
        if self.args["forecast"] and "forcing_forecast" in self.parameters:
            today = datetime.now().replace(tzinfo=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = today + timedelta(days=self.parameters["forcing_forecast"]["days"])
            self.log.info("Using forecast to extend end date by {} days".format(self.parameters["forcing_forecast"]["days"]), indent=1)

        if self.args["overwrite_end_date"]:
            overwrite_end_date = datetime.strptime(self.args["overwrite_end_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
            if overwrite_end_date > end_date:
                raise ValueError("Overwrite end date is outside of available forcing data")
            else:
                self.log.info("Setting end date based on overwrite end date {}".format(self.args["overwrite_start_date"]), indent=1)
                end_date = overwrite_end_date

        if start_date >= end_date:
            raise ValueError("Start date {} cannot be after end date {}".format(start_date, end_date))

        self.log.info("Model timeframe: {} - {}".format(start_date, end_date), indent=1)
        if self.snapshot:
            self.log.info("Model will be initialised from a snapshot", indent=1)
        else:
            self.log.info("Model will be initialised from initial conditions", indent=1)
        self.start_date = start_date
        self.end_date = end_date
        self.log.end_stage()

    def prepare_snapshot(self):
        self.log.begin_stage("prepare_snapshot")
        snapshot = os.path.join(self.simulation_dir, "simulation-snapshot_{}.dat".format(self.args["snapshot_date"]))
        self.log.info("Using snapshot: {}".format(snapshot), indent=1)
        shutil.copy(snapshot, os.path.join(self.simulation_dir, 'simulation-snapshot.dat'))
        self.log.end_stage()

    def create_initial_conditions_file(self):
        self.log.begin_stage("create_initial_conditions_file")
        self.log.info("Attempting to generate initial conditions from observation data", indent=1)
        profile = initial_conditions_from_observations(self.key, self.start_date, salinity=self.parameters["salinity"])
        if not profile:
            self.log.info("Failed to generate initial conditions from observation data, generating default profile", indent=1)
            doy = self.start_date.timetuple().tm_yday
            profile = default_initial_conditions(doy, self.parameters["elevation"], self.parameters["max_depth"], salinity=self.parameters["salinity"])
        write_initial_conditions(profile["depth"], profile["temperature"], profile["salinity"],
                                 os.path.join(self.simulation_dir, "InitialConditions.dat"))
        self.log.end_stage()

    def create_aed2_files(self):
        self.log.begin_stage("create_aed2_files")
        self.log.end_stage()

    def create_forcing_file(self):
        self.log.begin_stage("create_forcing_file")
        self.log.end_stage()

    def create_inflow_files(self):
        self.log.begin_stage("create_inflow_files")
        if "inflows" in self.parameters and len(self.parameters["inflows"]) > 0:
            self.log.info("Processing {} inputs".format(len(self.parameters["inflows"])), indent=1)
            self.parameters["inflow_mode"] = 2
            # Test data for seeing format required.
            time = [0, 500, 15635]
            deep_inflows = [
                {"depth": 0.0, "data": [10.5, 11.2, 16.1]}
            ]
            surface_inflows = [
                {"depth": -5.0, "data": [0, 0, 0]},
                {"depth": -5.0, "data": [10.5, 11.2, 16.1]},
                {"depth": 0.0, "data": [10.5, 11.2, 16.1]}
            ]
            write_inflow("Q", 2, self.simulation_dir, time=time, deep_inflows=deep_inflows, surface_inflows=surface_inflows)
        else:
            self.log.info("No inputs, producing default files", indent=1)
            self.parameters["inflow_mode"] = 0  # No inputs
            write_inflow("Q", 0, self.simulation_dir)
            write_inflow("T", 0, self.simulation_dir)
            write_inflow("S", 0, self.simulation_dir)
        write_outflow(self.simulation_dir)
        self.log.end_stage()

    def create_absorption_file(self):
        self.log.begin_stage("create_absorption_file")
        self.log.info("Attempting to generate absorption from observation data", indent=1)
        absorption = absorption_from_observations(self.key, self.start_date, self.end_date)
        if not absorption:
            self.log.info("Failed to generate absorption from observation data, generating default absorption",indent=1)
            absorption = default_absorption(self.parameters["trophic_state"], self.parameters["elevation"],
                                            self.start_date, self.end_date, self.parameters.get("absorption", False))
        write_absorption(absorption["time"], absorption["depth"], absorption["absorption"],
                         self.parameters["reference_date"], os.path.join(self.simulation_dir, "Absorption.dat"))
        self.log.end_stage()

    def create_par_file(self):
        self.log.begin_stage("create_par_file")
        file_path = os.path.join(self.args["repo_dir"], "par", "simstrat_{}.par".format(self.args["simstrat_version"]))
        if not os.path.exists(file_path):
            raise ValueError("Unable to locate default PAR file for Simstrat version {}".format(self.args["simstrat_version"]))
        if self.args["simstrat_version"] == "3.03":
            self.log.info("Updating default PAR file for {}".format(self.args["simstrat_version"]), indent=1)
            par = update_par_file_303(file_path, self.start_date, self.end_date, self.snapshot, self.parameters, self.args)
            self.log.info("Writing default PAR file for {}".format(self.args["simstrat_version"]), indent=1)
            write_par_file_303(par, self.simulation_dir)
        else:
            raise ValueError("PAR not implemented for Simstrat version {}".format(self.args["simstrat_version"]))
        self.log.end_stage()


