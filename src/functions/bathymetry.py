import numpy as np
import pandas as pd
from ast import literal_eval
from urllib.request import urlopen


def bathymetry_from_file(file_path):
    df = pd.read_csv(file_path, delim_whitespace=True)
    area = np.array(df[df.columns[1]])
    depth = np.array(df[df.columns[0]]) * -1
    return {"area": area, "depth": depth}


def bathymetry_from_datalakes(lake_id):
    my_bytes = urlopen('https://api.datalakes-eawag.ch/externaldata/morphology/' + str(lake_id)).read()
    data = literal_eval(my_bytes.decode('utf-8'))
    return {"area": list(map(float, data["Area"]["values"])), "depth": list(map(float, data["Depth"]["values"]))}
