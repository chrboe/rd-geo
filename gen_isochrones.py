import requests
import sys
import json
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection
from shapely.ops import unary_union
from pykml import parser
import time
import glob


def convert_border(filename, invert=False):
    coords = []
    with open(filename) as f:
        doc = parser.parse(f)
        for pm in doc.iterfind('.//{http://www.opengis.net/kml/2.2}Placemark'):
            cs = str(pm.Polygon[0].outerBoundaryIs.LinearRing.coordinates)
            for coord in cs.split():
                lonlat = coord.split(',')
                lat = lonlat[1]
                lon = lonlat[0]
                coords.append((float(lat), float(lon)))
    if invert:
        return Polygon([(90, -180), (90, 180), (-90, 180), (-90, -180)], [coords])
    return Polygon(coords)


vie = convert_border('kml/vie-border.kml')
noe = convert_border('kml/noe-border.kml')
noe_inv = convert_border('kml/noe-border.kml', invert=True)


def poly_from_coords(coords):
    poly_coords = []
    for coord in coords:
        poly_coords.append((coord[1], coord[0]))
    poly = Polygon(poly_coords)
    poly = poly.difference(vie)
    return poly


i = 0


def request_coords(locations):
    global i

    options = {}
    body = {"locations": locations, "range": [10 * 60, 15 * 60, 20 * 60], "range_type": "time", "options": options}
    headers = {
        'Accept': 'application/json, application/geo+json, application/gpx+xml, img/png; charset=utf-8',
        'Authorization': os.getenv('ORS_AUTH'),
        'Content-Type': 'application/json; charset=utf-8'
    }

    print("sending request: {}".format(body))
    response = requests.post('https://api.openrouteservice.org/v2/isochrones/driving-car', json=body, headers=headers)

    # rate limiting...
    time.sleep(5)

    with open("raw-isochrone-responses/response-{}.json".format(i), "w") as f:
        f.write(response.text)
        i = i + 1


def get_poly_coords(poly_map):
    result_map = {}
    for key in poly_map:
        poly = poly_map[key]
        result = []
        if isinstance(poly, MultiPolygon) or isinstance(poly, GeometryCollection):
            exterior_interior = []
            for single in poly.geoms:
                single_coords = []
                for coord in single.exterior.coords:
                    single_coords.append([coord[0], coord[1]])
                exterior_interior.append(single_coords)
                for interior in single.interiors:
                    interior_coords = []
                    for coord in interior.coords:
                        interior_coords.append([coord[0], coord[1]])
                    exterior_interior.append(interior_coords)
            result.append(exterior_interior)
        else:
            exterior_interior = []
            single_coords = []
            for coord in poly.exterior.coords:
                single_coords.append([coord[0], coord[1]])
            exterior_interior.append(single_coords)
            for interior in single.interiors:
                interior_coords = []
                for coord in interior.coords:
                    interior_coords.append([coord[0], coord[1]])
                exterior_interior.append(interior_coords)
            result.append(exterior_interior)
        result_map[key] = result
    return result_map


cached = True

if not cached:
    filename = "standorte.kml"

    locations = []
    with open(filename) as f:
        doc = parser.parse(f)
        for pm in doc.iterfind('.//{http://www.opengis.net/kml/2.2}Placemark'):
            name = pm.name
            organization = pm.ExtendedData.Data.value
            coords = str(pm.Point.coordinates).strip()
            split = coords.split(',')
            lon = float(split[0])
            lat = float(split[1])

            locations.append([lon, lat])
            print("adding {}".format([lon, lat]))
            if len(locations) == 5:
                request_coords(locations)
                locations = []

    request_coords(locations)

    sys.exit(0)
else:
    polys = [[], [], []]
    for filename in glob.glob('raw-isochrone-responses/response-*.json'):
        with open(filename, 'r') as f:
            data = json.loads(f.read())
        counts = {}
        for feature in data["features"]:
            group_index = feature["properties"]["group_index"]
            if group_index not in counts:
                counts[group_index] = 0
            poly = poly_from_coords(feature["geometry"]["coordinates"][0])
            polys[counts[group_index]].append(poly)
            counts[group_index] = counts[group_index] + 1

    # merge individual polys together
    unions = {
        "green": unary_union(polys[0]),
        "orange": unary_union(polys[1]),
        "red": unary_union(polys[2])
    }

    # avoid overlapping colors by cutting out the polys of "upper" colors from the "lower" ones.
    # red is all the way at the bottom, then orange, then green
    unions["red"] = unions["red"].difference(unions["orange"])
    unions["orange"] = unions["orange"].difference(unions["green"])

    # avoid the polygons "leaking" out of the map bounds (out of Lower Austria/into Vienna)
    unions["red"] = unions["red"].difference(vie)
    unions["orange"] = unions["orange"].difference(vie)
    unions["green"] = unions["green"].difference(vie)
    unions["red"] = unions["red"].difference(noe_inv)
    unions["orange"] = unions["orange"].difference(noe_inv)
    unions["green"] = unions["green"].difference(noe_inv)

    # there is actually a fourth color, purple, which denotes all the places that cannot even be reached in 20 minutes
    # (or cannot be reached at all). this is just "the rest", so start with Lower Austria and subtract all polys
    unions["purple"] = noe.difference(vie).difference(unions["red"]).difference(unions["orange"]).difference(
        unions["green"])

    isos = get_poly_coords(unions)

    print('// generated by gen_isochrones.py')
    print('var isochrones = ' + json.dumps(isos) + ';')
