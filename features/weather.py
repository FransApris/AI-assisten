import requests
from datetime import datetime

def get_weather(latitude: float, longitude: float, location_name: str = "") -> str:
    """Mendapatkan cuaca saat ini dari Open-Meteo API."""
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current_weather=true"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        
        cw = data.get("current_weather", {})
        if not cw:
            return f"Maaf, tidak dapat mengambil data cuaca untuk {location_name}."
            
        temp = cw.get("temperature")
        windspeed = cw.get("windspeed")
        time = cw.get("time")
        
        loc_str = location_name if location_name else f"Lat {latitude}, Lon {longitude}"
        return f"Cuaca di {loc_str} (diperbarui {time}): Suhu {temp}°C, Kecepatan Angin {windspeed} km/h."
    except Exception as e:
        return f"[Gagal mengambil cuaca: {e}]"

# Simple geocoding helper using Open-Meteo geocoding API
def get_weather_by_city(city_name: str) -> str:
    try:
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_name}&count=1&language=id&format=json"
        r = requests.get(geo_url, timeout=10)
        r.raise_for_status()
        geo_data = r.json()
        
        results = geo_data.get("results")
        if not results:
            return f"Lokasi '{city_name}' tidak ditemukan."
            
        lat = results[0].get("latitude")
        lon = results[0].get("longitude")
        name = results[0].get("name")
        country = results[0].get("country", "")
        
        full_name = f"{name}, {country}" if country else name
        return get_weather(lat, lon, full_name)
    except Exception as e:
        return f"[Gagal mencari lokasi: {e}]"
