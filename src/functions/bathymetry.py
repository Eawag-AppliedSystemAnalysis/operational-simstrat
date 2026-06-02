import numpy as np
import pandas as pd
from ast import literal_eval

from functions.general import urlopen_with_retry


def bathymetry_from_file(file_path):
    df = pd.read_csv(file_path, sep=r'\s+')
    area = np.array(df[df.columns[1]])
    depth = np.array(df[df.columns[0]]) * -1
    return {"area": area, "depth": depth}


def bathymetry_from_datalakes(lake_id):
    with urlopen_with_retry('https://api.datalakes-eawag.ch/externaldata/morphology/' + str(lake_id)) as response:
        my_bytes = response.read()
    data = literal_eval(my_bytes.decode('utf-8'))
    return {"area": list(map(float, data["Area"]["values"])), "depth": list(map(float, data["Depth"]["values"]))}
