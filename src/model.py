import os
import shutil
import numpy as np
from datetime import datetime

from functions import verify
from functions.log import Logger
from functions.write import write_grid, write_bathymetry, write_output_depths, write_output_time_resolution
from functions.bathymetry import bathymetry_from_file, bathymetry_from_datalakes


class Simstrat(object):
    def __init__(self, key, parameters, args):
        self.key = key
        self.args = args
        self.simulation_dir = os.path.join(args["simulation_dir"], key)
        self.default_parameters = {
            "reference_date": {"default": "19810101", "verify": verify.verify_date, "desc": "Reference date YYYYMMDD of the model"},
            "model_time_resolution": {"default": 300, "verify": verify.verify_integer, "desc": "Timestep of the model (s)"},
            "output_time_resolution": {"default": 10800, "verify": verify.verify_integer,"desc": "Output imestep of the model, should be evenly devisable by the model timestep (s)"},
        }
        self.optional_parameters = {
            "max_depth": {"verify": verify.verify_float, "desc": "Maximum depth of the lake (m)"},
            "surface_area": {"verify": verify.verify_float, "desc": "Surface area of the lake (m2)"},
            "grid_resolution": {"verify": verify.verify_float, "desc": "Vertical resolution of the simulation grid (m)"},
            "output_depth_resolution": {"verify": verify.verify_float, "desc": "Vertical resolution of the output file (m)"},
            "bathymetry": {"verify": verify.verify_dict, "desc": "Bathymetry data in the format { area: [12,13,...], depth: [0, 1,...] } where area is in m2 and depth in m"},
            "bathymetry_datalakes_id": {"verify": verify.verify_integer, "desc": "Datalakes ID for bathymetry profile"},
        }
        self.parameters = {k: v["default"] for k, v in self.default_parameters.items()}
        for key in parameters.keys():
            if key in self.default_parameters.keys():
                self.default_parameters[key]["verify"](parameters[key])
                self.parameters[key] = parameters[key]
            elif key in self.optional_parameters.keys():
                self.optional_parameters[key]["verify"](parameters[key])
                self.parameters[key] = parameters[key]

        if os.path.exists(self.simulation_dir) and args["overwrite"]:
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

    def create_bathymetry_file(self):
        self.log.begin_stage("Creating the bathymetry file")
        bathymetry_file = os.path.join(self.simulation_dir, "Bathymetry.dat")
        if os.path.exists(bathymetry_file):
            self.log.info("Bathymetry file exists, reading from file.")
            bathymetry = bathymetry_from_file(bathymetry_file)
        else:
            if "bathymetry" in self.parameters:
                self.log.info("Bathymetry defined in parameters.")
                bathymetry = self.parameters["bathymetry"]
            elif "bathymetry_datalakes_id" in self.parameters:
                self.log.info("Accessing bathymetry from Datalakes (id={})".format(self.parameters["bathymetry_datalakes_id"]))
                bathymetry = bathymetry_from_datalakes(self.parameters["bathymetry_datalakes_id"])
            elif "max_depth" in self.parameters and "surface_area" in self.parameters:
                self.log.info("Using surface_area and max_depth for a simple two-point bathymetry")
                bathymetry = {"area": [self.parameters["surface_area"], 0], "depth": [0, self.parameters["max_depth"]]}
            else:
                raise Exception("At least one of the following parameters must be provided: bathymetry, "
                                "bathymetry_datalakes_id, max_depth and surface_area")
            write_bathymetry(bathymetry, bathymetry_file)
        self.parameters["max_depth"] = max(bathymetry["depth"])
        self.log.info("Max depth set to {}m".format(self.parameters["max_depth"]))
        self.log.end_stage()

    def create_grid_file(self):
        self.log.begin_stage("Creating the grid file")
        grid_file = os.path.join(self.simulation_dir, "Grid.dat")
        if os.path.exists(grid_file):
            self.log.info("Grid file exists")
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
            self.log.info("Grid resolution set to {} m".format(self.parameters["grid_resolution"]))
            grid_cells = np.ceil(abs(self.parameters["max_depth"] / self.parameters["grid_resolution"]))
            if grid_cells > 1000:
                self.log.info('Grid cells limited to 1000')
                grid_cells = 1000
            write_grid(grid_cells, grid_file)
        self.log.end_stage()

    def create_output_depths_file(self):
        self.log.begin_stage("Creating the output depths file")
        output_depths_file = os.path.join(self.simulation_dir, "z_out.dat")
        if os.path.exists(output_depths_file):
            self.log.info("Output depth resolution file exists")
        else:
            if self.parameters["max_depth"] > 20:
                self.parameters["output_depth_resolution"] = 1
            elif self.parameters["max_depth"] > 10:
                self.parameters["output_depth_resolution"] = 0.5
            elif self.parameters["max_depth"] > 5:
                self.parameters["output_depth_resolution"] = 0.25
            else:
                self.parameters["output_depth_resolution"] = 0.1
            self.log.info("Output depth resolution set to {} m".format(self.parameters["output_depth_resolution"]))
            depths = np.arange(0, self.parameters["max_depth"], self.parameters["output_depth_resolution"])
            write_output_depths(depths, output_depths_file)

        self.log.end_stage()

    def create_output_time_resolution_file(self):
        self.log.begin_stage("Creating the output time resolution file")
        output_time_resolution_file = os.path.join(self.simulation_dir, "t_out.dat")
        if os.path.exists(output_time_resolution_file):
            self.log.info("Output time resolution file exists")
        else:
            if not self.parameters["output_time_resolution"] % self.parameters["model_time_resolution"] == 0:
                raise Exception("Output time resolution must be a multiple of the model time resolution")
            output_time_steps = self.parameters["output_time_resolution"] / self.parameters["model_time_resolution"]
            write_output_time_resolution(output_time_steps, output_time_resolution_file)
        self.log.end_stage()

