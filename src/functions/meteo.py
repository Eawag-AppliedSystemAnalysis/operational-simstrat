import requests
from datetime import datetime


def period_from_meteo(stations, api):
    start_dates = []
    end_dates = []
    for station in stations:
        if station["source"].lower() == "meteoswiss":
            start, end = period_from_meteo_meteoswiss(station["station_id"], api)
            start_dates.append(start)
            end_dates.append(end)
        else:
            raise ValueError("start_from_meteo not implemented for data source {}".format(station["source"]))
    return min(start_dates), max(end_dates)


def period_from_meteo_meteoswiss(station_id, api):
    endpoint = "{}/meteoswiss/meteodata/metadata/{}".format(api, station_id)
    response = requests.get(endpoint)
    if response.status_code == 200:
        data = response.json()
        start = datetime.strptime(data["parameters"][0]["start_date"], '%Y-%m-%dT%H:%M:%S%z')
        end = datetime.strptime(data["parameters"][0]["end_date"], '%Y-%m-%dT%H:%M:%S%z')
        return start, end
    else:
        raise ValueError("Unable to access endpoint {}".format(endpoint))
