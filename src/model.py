import logging
import os
import shutil
import subprocess
import numpy as np
from datetime import datetime, timezone, timedelta

from functions import verify
from functions.log import Logger
from functions.write import (write_grid, write_bathymetry, write_output_depths, write_output_time_resolution,
                             write_initial_conditions, write_absorption, write_par_file, write_inflows,
                             write_outflow, write_forcing_data)
from functions.bathymetry import bathymetry_from_file, bathymetry_from_datalakes
from functions.grid import grid_from_file
from functions.inflow import collect_inflow_data, interpolate_inflow_data, quality_assurance_inflow_data, fill_inflow_data
from functions.forcing import metadata_from_forcing, download_forcing_data, interpolate_forcing_data, fill_forcing_data, quality_assurance_forcing_data
from functions.par import update_par_file, overwrite_par_file_dates
from functions.observations import (initial_conditions_from_observations, default_initial_conditions,
                                    absorption_from_observations, default_absorption)
from functions.postprocess import convert_to_netcdf, calculate_variables
from functions.general import run_subprocess, upload_files


class Simstrat(object):
    def __init__(self, key, parameters, args):
        self.key = key
        self.args = args
        self.simulation_dir = os.path.join(args["simulation_dir"], key)
        self.required_parameters = {
            "forcing": {"verify": verify.verify_forcing, "desc": "List of dicts describing the input forcing data"},
            "name": {"verify": verify.verify_string, "desc": "Name of the lake"},
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
            "inflow_salinity": {"default": 0.15, "verify": verify.verify_float, "desc": "Default salinity for all river inputs if not available in observations (ppt)"},
            "output_time_resolution": {"default": 10800, "verify": verify.verify_integer,"desc": "Output imestep of the model, should be evenly devisable by the model timestep (s)"},
        }
        self.optional_parameters = {
            "max_depth": {"verify": verify.verify_float, "desc": "Maximum depth of the lake (m)"},
            "grid_resolution": {"verify": verify.verify_float, "desc": "Vertical resolution of the simulation grid (m)"},
            "output_depth_resolution": {"verify": verify.verify_float, "desc": "Vertical resolution of the output file (m)"},
            "bathymetry": {"verify": verify.verify_dict, "desc": "Bathymetry data in the format { area: [12,13,...], depth: [0, 1,...] } where area is in m2 and depth in m"},
            "bathymetry_datalakes_id": {"verify": verify.verify_integer, "desc": "Datalakes ID for bathymetry profile"},
            "inflows": {"verify": verify.verify_inflows, "desc": "List of inflows described by dicts with discharge and temeperature"},
            "forcing_forecast": {"verify": verify.verify_forcing_forecast, "desc": "Dictionary proving source and model"},
            "absorption": {"verify": verify.verify_float, "desc": "Absorption coefficient when observation data not available"},
        }
        self.forcing_parameters = {
            "Time": {"unit": "d", "description": "Time in days since reference date"},
            "u": {"unit": "m/s", "description": "Wind component West to East", "max_interpolate_gap": 7, "fill": "mean", "min": -20, "max": 20},
            "v": {"unit": "m/s", "description": "Wind component South to North", "max_interpolate_gap": 7, "fill": "mean", "min": -20, "max": 20},
            "Tair": {"unit": "°C", "description": "Air temperature adjusted to lake altitude", "max_interpolate_gap": 2, "fill": "doy", "min": -42, "max": 42},
            "sol": {"unit": "W/m2", "description": "Solar irradiance", "max_interpolate_gap": 0.125, "fill": "doy", "negative_to_zero": True, "max": 1200},
            "vap": {"unit": "mbar", "description": "Vapor pressure", "max_interpolate_gap": 2, "fill": "doy", "min": 1, "max": 70},
            "cloud": {"unit": "-", "description": "Cloud cover from 0 to 1", "max_interpolate_gap": None, "fill": None, "min": 0, "max": 1},
            "rain": {"unit": "m/hr", "description": "Precipitation", "max_interpolate_gap": 7, "fill": "mean", "negative_to_zero": True},
        }
        self.inflow_parameters = {
            "depth": {"unit": "m", "description": "Depth of inflow relative to top (river inflow) or surface (lake inflows)"},
            "Q": {"unit": "m3/s", "description": "Flow rate", "max_interpolate_gap": 5, "fill": "doy", "negative_to_zero": True, "max": 1500},
            "T": {"unit": "°C", "description": "Temperature", "max_interpolate_gap": 5, "fill": "doy", "negative_to_zero": True, "max": 30},
            "S": {"unit": "ppt", "description": "Salinity", "max_interpolate_gap": 5, "fill": "doy", "negative_to_zero": True, "max": 0.5}
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
        self.origin_date = self.parameters["reference_date"]
        self.end_date = datetime.now().replace(tzinfo=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        self.forecast = False

        if os.path.exists(self.simulation_dir) and args["overwrite_simulation"]:
            shutil.rmtree(self.simulation_dir)
        if not os.path.exists(self.simulation_dir):
            os.makedirs(self.simulation_dir, exist_ok=True)
        os.makedirs(os.path.join(self.simulation_dir, "Results"), exist_ok=True)
        os.chmod(os.path.join(self.simulation_dir, "Results"), 0o777)

        if args["log"]:
            self.log = Logger(path=self.simulation_dir)
        else:
            self.log = Logger()

        self.log.initialise("Simstrat Operational - {}".format(self.key))
        self.log.inputs("Input Parameters", self.parameters)

    def process(self):
        try:
            self.create_bathymetry_file()
            self.create_grid_file()
            self.create_output_depths_file()
            self.create_output_time_resolution_file()
            self.set_simulation_run_period()
            if self.snapshot:
                self.prepare_snapshot()
            self.create_initial_conditions_file()
            self.create_absorption_file()
            self.create_forcing_file()
            self.create_inflow_files()
            if self.args["couple_aed2"]:
                self.create_aed2_files()
            self.create_par_file()
            if self.args["run"]:
                self.run_simulation()
                self.post_process()
                if self.args["upload"]:
                    self.upload()
        except Exception as e:
            print(e)
            self.log.info(str(e))
            raise ValueError("Processing failed. See log for details.")


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
        forcing_start, forcing_end = metadata_from_forcing(self.parameters["forcing"], self.args["data_api"])
        self.origin_date = forcing_start
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
                if not os.path.exists(os.path.join(self.simulation_dir, "Results", "simulation-snapshot_{}.dat".format(self.args["snapshot_date"]))):
                    self.log.info("Snapshot {} cannot be found, reverting to forcing period".format(self.args["snapshot_date"]), indent=2)
                    self.snapshot = False
                    start_date = forcing_start
                else:
                    self.log.info("Snapshot {} located".format(self.args["snapshot_date"]), indent=2)
                    start_date = datetime.strptime(self.args["snapshot_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
            else:
                self.log.info("Attempting to define start date by most recent snapshot", indent=1)
                snapshots = [f.split(".")[0].split("_")[-1] for f in os.listdir(os.path.join(self.simulation_dir, "Results")) if "simulation-snapshot_" in f]
                if len(snapshots) == 0:
                    self.log.info("No snapshots available, reverting to forcing period", indent=2)
                    self.snapshot = False
                    start_date = forcing_start
                else:
                    snapshots.sort()
                    self.log.info("Snapshot {} located".format(snapshots[-1]), indent=2)
                    self.args["snapshot_date"] = snapshots[-1].split(".")[0].split("_")[-1]
                    start_date = datetime.strptime(self.args["snapshot_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
        else:
            start_date = forcing_start

        if start_date < self.parameters["reference_date"]:
            self.log.info("Start date cannot be before reference date. Setting to reference date.", indent=1)
            start_date = self.parameters["reference_date"]

        end_date = forcing_end
        if self.args["forecast"] and "forcing_forecast" in self.parameters:
            self.forecast = True
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
        snapshot = os.path.join(self.simulation_dir, "Results", "simulation-snapshot_{}.dat".format(self.args["snapshot_date"]))
        self.log.info("Using snapshot: {}".format(snapshot), indent=1)
        shutil.copy(snapshot, os.path.join(self.simulation_dir, "Results", 'simulation-snapshot.dat'))
        with open(os.path.join(self.simulation_dir, "InitialConditions.dat"), 'w') as f:
            f.write("Initialising model from snapshot")
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

    def create_forcing_file(self):
        self.log.begin_stage("create_forcing_file")
        forcing_data = download_forcing_data(self.forcing_parameters,
                                             self.start_date,
                                             self.end_date,
                                             self.parameters["forcing"],
                                             self.forecast,
                                             self.parameters.get('forcing_forecast', False),
                                             self.parameters["elevation"],
                                             self.parameters["latitude"],
                                             self.parameters["longitude"],
                                             self.parameters["reference_date"],
                                             self.args["data_api"],
                                             self.log)
        forcing_data = quality_assurance_forcing_data(forcing_data, self.log)
        self.log.info("Interpolating small data gaps", indent=1)
        forcing_data = interpolate_forcing_data(forcing_data)
        self.log.info("Filling large data gaps", indent=1)
        forcing_data = fill_forcing_data(forcing_data, self.simulation_dir, self.snapshot,
                                         self.parameters["reference_date"], self.log)
        self.log.info("Writing forcing data.", indent=1)
        write_forcing_data(forcing_data, self.simulation_dir, self.log)
        self.log.end_stage()

    def create_inflow_files(self):
        self.log.begin_stage("create_inflow_files")
        if "inflows" in self.parameters and len(self.parameters["inflows"]) > 0:
            self.log.info("Processing {} inflows".format(len(self.parameters["inflows"])), indent=1)
            self.parameters["inflow_mode"] = 2
            inflow_data = collect_inflow_data(self.parameters["inflows"], self.parameters["inflow_salinity"],
                                              self.start_date, self.end_date, self.parameters["reference_date"],
                                              self.simulation_dir, self.args["data_api"], self.log)
            inflow_data = quality_assurance_inflow_data(inflow_data, self.inflow_parameters, self.log)
            self.log.info("Interpolating small data gaps", indent=1)
            inflow_data = interpolate_inflow_data(inflow_data, self.inflow_parameters)
            self.log.info("Filling large data gaps", indent=1)
            inflow_data = fill_inflow_data(inflow_data, self.inflow_parameters, self.simulation_dir, self.snapshot,
                                           self.parameters["reference_date"], self.log)
            write_inflows(2, self.simulation_dir, self.log, inflow_data=inflow_data)
        else:
            self.log.info("No inflows, producing default files", indent=1)
            self.parameters["inflow_mode"] = 0
            write_inflows(0, self.simulation_dir, self.log)
        write_outflow(self.simulation_dir)
        self.log.end_stage()

    def create_absorption_file(self):
        self.log.begin_stage("create_absorption_file")
        self.log.info("Attempting to generate absorption from observation data", indent=1)
        absorption = absorption_from_observations(self.key, self.start_date, self.end_date, self.args["data_api"], self.parameters["reference_date"])
        if not absorption:
            self.log.info("Failed to generate absorption from observation data, generating default absorption",indent=1)
            absorption = default_absorption(self.parameters["trophic_state"], self.parameters["elevation"],
                                            self.start_date, self.end_date, self.parameters.get("absorption", False),
                                            self.parameters["reference_date"])
        write_absorption(absorption, os.path.join(self.simulation_dir, "Absorption.dat"), self.log)
        self.log.end_stage()

    def create_aed2_files(self):
        self.log.begin_stage("create_aed2_files")
        self.log.end_stage()

    def create_par_file(self):
        self.log.begin_stage("create_par_file")
        file_path = os.path.join(self.args["repo_dir"], "par", "simstrat_{}.par".format(self.args["simstrat_version"]))
        if not os.path.exists(file_path):
            raise ValueError("Unable to locate default PAR file for Simstrat version {}".format(self.args["simstrat_version"]))
        self.log.info("Updating default PAR file for {}".format(self.args["simstrat_version"]), indent=1)
        if self.snapshot:
            start = self.origin_date  # Snapshot start date == par start date
        else:
            start = self.start_date
        par = update_par_file(self.args["simstrat_version"], file_path, start, self.end_date, self.args["snapshot"], self.parameters, self.args)
        self.log.info("Writing default PAR file for {}".format(self.args["simstrat_version"]), indent=1)
        write_par_file(self.args["simstrat_version"], par, self.simulation_dir)
        self.log.end_stage()

    def run_simulation(self):
        self.log.begin_stage("run_simulation")
        if self.args["docker_dir"]:
            simulation_dir = os.path.join(self.args["docker_dir"], "runs", self.key)
        else:
            simulation_dir = self.simulation_dir
        command = "docker run --user $(id -u):$(id -g) -v {}:/simstrat/run eawag/simstrat:{} Settings.par".format(simulation_dir, self.args["simstrat_version"])
        month_beginning = self.end_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        snapshot_path = os.path.join(self.simulation_dir, "Results", "simulation-snapshot.dat")
        if self.args["snapshot"] and month_beginning != self.start_date:
            self.log.info("Splitting into two runs to create correct snapshot", indent=1)
            self.log.info("Running from {} - {}".format(self.start_date, month_beginning), indent=1)
            overwrite_par_file_dates(os.path.join(self.simulation_dir, "Settings.par"), self.origin_date, month_beginning, self.parameters["reference_date"])
            run_subprocess(command)
            snapshot_out_path = os.path.join(self.simulation_dir, "Results", "simulation-snapshot_{}.dat".format(month_beginning.strftime("%Y%m%d")))
            shutil.copy(snapshot_path, snapshot_out_path)
            overwrite_par_file_dates(os.path.join(self.simulation_dir, "Settings.par"), self.origin_date, self.end_date, self.parameters["reference_date"])
            self.log.info("Running from {} - {}".format(month_beginning, self.end_date), indent=1)
            run_subprocess(command)
            os.remove(snapshot_path)
        else:
            self.log.info("Running from {} - {}".format(self.start_date, self.end_date), indent=1)
            run_subprocess(command)
            if os.path.exists(snapshot_path):
                os.remove(snapshot_path)
        self.log.end_stage()

    def post_process(self):
        self.log.begin_stage("post_process")
        convert_to_netcdf(self.start_date, os.path.join(self.simulation_dir, "Results"), self.args["simstrat_version"], self.parameters)
        calculate_variables(os.path.join(self.simulation_dir, "Results", "netcdf"))
        self.log.end_stage()

    def upload(self):
        self.log.begin_stage("upload")
        local_folder = os.path.join(self.simulation_dir, "Results", "netcdf")
        remote_folder = os.path.join(self.args["results_folder_api"], self.key)
        upload_files(local_folder, remote_folder, self.args["server_host"], self.args["server_user"], self.args["server_password"], self.log)
        self.log.end_stage()

