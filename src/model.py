import os
import sys
import json
import shutil
from functions.log import Logger


class Simstrat(object):
    def __init__(self, parameters, args):
        self.parameters = parameters
        self.args = args
        self.simulation_dir = os.path.join(args["simulation_dir"], parameters["key"])

        if os.path.exists(self.simulation_dir) and args["overwrite"]:
            shutil.rmtree(self.simulation_dir)
        if not os.path.exists(self.simulation_dir):
            os.makedirs(self.simulation_dir, exist_ok=True)

        if args["log"]:
            self.log = Logger(path=self.simulation_dir)
        else:
            self.log = Logger()

        self.log.initialise("Simstrat Operational - {}".format(self.parameters["key"]))

    def process(self):
        print("Processing")
