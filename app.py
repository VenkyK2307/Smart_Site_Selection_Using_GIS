import math, requests, zipfile, os, pandas as pd, geopandas as gpd, rasterio
from shapely.geometry import Point
from flask import Flask, request, jsonify, render_template
import logging
import csv

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ----------------------------
# CONFIG
# ----------------------------
GOOGLE_API_KEY = "AIzaSyBbmUFrNucT0E3Mqfh5oovYspjEhOttc6c" # replace with your key
POP_TIFF = "ind_pd_2020_1km.tif"
SEISMIC_ZIP = "Seismic_Zones (1).zip"
EARTH_RADIUS = 6371.0

# ----------------------------
# Load Seismic & Population Data
# ----------------------------
try:
    if not os.path.exists("seismic"):
        with zipfile.ZipFile(SEISMIC_ZIP, 'r') as z:
            z.extractall("seismic")
    seismic_gdf = gpd.read_file("seismic")
    if seismic_gdf.crs is None or seismic_gdf.crs.to_epsg() != 4326:
        seismic_gdf = seismic_gdf.to_crs(epsg=4326)
    logging.info("Seismic data loaded.")
except Exception as e:
    logging.error(f"Error loading seismic data: {e}")
    seismic_gdf = None

try:
    pop_src = rasterio.open(POP_TIFF)
    logging.info("Population TIFF loaded.")
except Exception as e:
    logging.error(f"Error loading population TIFF: {e}")
    pop_src = None

# ----------------------------
# Helper Functions
# ----------------------------
def move_point(lat, lon, bearing_deg, distance_km):
    bearing = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    lat2 = math.asin(math.sin(lat1)*math.cos(distance_km/EARTH_RADIUS) +
                     math.cos(lat1)*math.sin(distance_km/EARTH_RADIUS)*math.cos(bearing))
    lon2 = lon1 + math.atan2(math.sin(bearing)*math.sin(distance_km/EARTH_RADIUS)*math.cos(lat1),
                             math.cos(distance_km/EARTH_RADIUS)-math.sin(lat1)*math.sin(lat2))
    return round(math.degrees(lat2),4), round(math.degrees(lon2),4)

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2-lat1)
    dlon = math.radians(lon2-lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def get_places(lat, lon, place_type, radius):
    try:
        url = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json"
        params = {"location": f"{lat},{lon}", "radius": radius, "type": place_type, "key": GOOGLE_API_KEY}
        r = requests.get(url, params=params).json()
        dists = []
        for p in r.get("results", []):
            pl_lat = p["geometry"]["location"]["lat"]
            pl_lon = p["geometry"]["location"]["lng"]
            dists.append(haversine(lat, lon, pl_lat, pl_lon))
        return dists
    except:
        return []

def nearest_road_distance(lat, lon):
    try:
        url = f"https://roads.googleapis.com/v1/nearestRoads"
        params = {"points": f"{lat},{lon}", "key": GOOGLE_API_KEY}
        r = requests.get(url, params=params).json()
        if "snappedPoints" in r:
            snapped = r["snappedPoints"][0]["location"]
            return haversine(lat, lon, snapped["latitude"], snapped["longitude"])*1000
        return None
    except:
        return None

def get_elevation(lat, lon):
    try:
        url = f"https://maps.googleapis.com/maps/api/elevation/json"
        params = {"locations": f"{lat},{lon}", "key": GOOGLE_API_KEY}
        r = requests.get(url, params=params).json()
        if r["results"]:
            return r["results"][0]["elevation"]
        return None
    except:
        return None

def get_popdensity(lat, lon):
    try:
        if pop_src is None:
            return 0
        row, col = pop_src.index(lon, lat)
        val = pop_src.read(1)[row, col]
        return float(val) if val else 0
    except:
        return 0

def get_seismic_zone(lat, lon):
    try:
        if seismic_gdf is None:
            return "Unknown"
        pt = gpd.GeoDataFrame(geometry=[Point(lon, lat)], crs=seismic_gdf.crs)
        join = gpd.sjoin(pt, seismic_gdf, predicate="within", how="left")
        if not join.empty:
            return join.iloc[0].get("seismic_zo","Unknown")
        return "Unknown"
    except:
        return "Unknown"

def get_air_pollution_score(lat, lon):
    try:
        url = f"https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&hourly=us_aqi"
        resp = requests.get(url).json()
        h = resp.get("hourly",{})
        aqi = h.get("us_aqi",[None])[-1]
        if aqi is None: return 200
        if aqi <= 50: return 100
        if aqi <= 100: return 80
        if aqi <= 150: return 60
        if aqi <= 200: return 40
        if aqi <= 300: return 20
        return 0
    except:
        return 200

def get_protection_score(lat, lon):
    try:
        def nearby_count(place_type):
            url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
            params = {"key": GOOGLE_API_KEY, "location": f"{lat},{lon}", "radius": 5000, "type": place_type}
            resp = requests.get(url, params=params).json()
            return len(resp.get("results",[]))
        parks = nearby_count("park")
        veg_index = min(parks/5,1.0)
        roads = nearby_count("road")
        urban = nearby_count("locality")
        railways = nearby_count("train_station")
        bus_stands = nearby_count("bus_station")
        airports = nearby_count("airport")
        score = 100
        score -= min(roads*5,30)
        score -= min(urban*10,30)
        score -= min(railways*5,10)
        score -= min(bus_stands*5,10)
        score -= min(airports*5,10)
        score += int(veg_index*20)
        return max(0,min(100,score))
    except:
        return 50

# ----------------------------
# Routes
# ----------------------------
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/analyze-location", methods=["POST"])
def analyze_location():
    data = request.get_json()
    center_lat = data.get("lat")
    center_lon = data.get("lon")
    if center_lat is None or center_lon is None:
        return jsonify({"error":"Provide lat/lon"}),400

    bearings = [0,45,90,135,180,225,270,315]
    locations = [(round(center_lat,4), round(center_lon,4))]
    locations += [move_point(center_lat, center_lon,b,2) for b in bearings]

    records = []
    for i,(lat,lon) in enumerate(locations,start=1):
        hosp = get_places(lat,lon,"hospital",5000)
        trans = get_places(lat,lon,"bus_station",2000)+get_places(lat,lon,"train_station",2000)
        road = nearest_road_distance(lat,lon) or 0
        elev = get_elevation(lat,lon) or 0
        pop = get_popdensity(lat,lon)
        seismic = get_seismic_zone(lat,lon)
        air = get_air_pollution_score(lat,lon)
        prot = get_protection_score(lat,lon)

        records.append({
            "ID": i,
            "Lat": lat,
            "Lon": lon,
            "Total_Hospital_km": round(sum(hosp),2),
            "Nearest_Road_m": round(road,2),
            "Avg_Transport_km": round(sum(trans)/len(trans),2) if trans else 0,
            "Elevation_m": round(elev,2),
            "Pop_Density": round(pop,2),
            "Protection_Score": round(prot,2),
            "Air_Quality": round(air,2),
            "Seismic_Zone": seismic
        })

    csv_filename = "location_analysis_results.csv"
    try:
        if records:
            headers = records[0].keys()
            with open(csv_filename, 'w', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=headers)
                writer.writeheader()
                writer.writerows(records)
            logging.info(f"Analysis data saved to {csv_filename}")
        else:
            logging.warning("No data to save to CSV.")
    except Exception as e:
        logging.error(f"Failed to save CSV file: {e}")

    return jsonify(records)

if __name__=="__main__":
    app.run(debug=True)