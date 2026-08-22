import time
import requests

# Gunakan OpenStreetMap API (Nominatim) + OSRM untuk routing.
# OSRM public demo menggunakan HTTP; jadikan variabel agar mudah diganti.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# PENTING: router.project-osrm.org hanya tersedia via HTTP (bukan HTTPS).
# Gunakan API ini untuk lingkungan development/lokal. Untuk production,
# pertimbangkan self-hosted OSRM atau Valhalla.
OSRM_URL = "http://router.project-osrm.org/route/v1/driving"

HEADERS = {
    # Nominatim Policy: wajib ada User-Agent deskriptif. Tanpa ini, request bisa diblokir.
    "User-Agent": "APRIS-VirtualAssistant/3.1 (personal-assistant)"
}

# Nominatim Terms of Use: maksimal 1 request per detik.
_NOMINATIM_DELAY_SEC = 1.2


def get_coordinates(place_name: str):
    """
    Mengubah nama tempat menjadi koordinat (latitude, longitude).
    Menambahkan delay kecil untuk patuh aturan rate-limit Nominatim.
    Returns: dict {"lat": float, "lon": float, "display_name": str} atau None
    """
    if not place_name or not place_name.strip():
        return None
    try:
        time.sleep(_NOMINATIM_DELAY_SEC)  # Patuhi rate-limit Nominatim 1 req/detik
        response = requests.get(
            NOMINATIM_URL,
            params={"q": place_name.strip(), "format": "json", "limit": 1},
            headers=HEADERS,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        if data:
            return {
                "lat": float(data[0]["lat"]),
                "lon": float(data[0]["lon"]),
                "display_name": data[0]["display_name"]
            }
        return None
    except Exception as e:
        print(f"[Location] Gagal mencari koordinat untuk '{place_name}': {e}")
        return None


def search_places(query: str) -> str:
    """
    Mencari lokasi atau tempat umum berdasarkan kueri.
    Contoh: "Rumah Sakit di Jakarta Selatan"
    """
    if not query or not query.strip():
        return "Query pencarian lokasi tidak boleh kosong."
    try:
        time.sleep(_NOMINATIM_DELAY_SEC)
        response = requests.get(
            NOMINATIM_URL,
            params={"q": query.strip(), "format": "json", "limit": 5},
            headers=HEADERS,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            return f"Tidak menemukan hasil untuk pencarian '{query}'."

        result = f"Hasil Pencarian Tempat untuk '{query}':\n"
        for i, item in enumerate(data, 1):
            name = item.get('display_name', '').split(',')[0].strip()
            full_addr = item.get('display_name', '')
            result += f"{i}. *{name}*\n   📍 {full_addr}\n"
        return result.strip()
    except Exception as e:
        return f"Gagal melakukan pencarian tempat: {str(e)}"


def get_route(origin: str, destination: str) -> str:
    """
    Menghitung jarak dan estimasi waktu tempuh (mobil/motor) menggunakan OSRM.
    Catatan: `get_coordinates` dipanggil dua kali; setiap panggilan memiliki delay
    Nominatim, sehingga total minimal ~2.4 detik. Ini disengaja untuk kepatuhan.
    """
    if not origin or not destination:
        return "Titik asal dan tujuan tidak boleh kosong."

    coord_a = get_coordinates(origin)
    coord_b = get_coordinates(destination)

    if not coord_a:
        return f"Gagal menemukan koordinat titik awal: '{origin}'. Coba masukkan nama kota atau alamat yang lebih spesifik."
    if not coord_b:
        return f"Gagal menemukan koordinat titik tujuan: '{destination}'. Coba masukkan nama kota atau alamat yang lebih spesifik."

    try:
        coords_str = f"{coord_a['lon']},{coord_a['lat']};{coord_b['lon']},{coord_b['lat']}"
        req_url = f"{OSRM_URL}/{coords_str}?overview=false"

        response = requests.get(req_url, timeout=15)
        response.raise_for_status()
        data = response.json()

        if data.get("code") == "Ok" and data.get("routes"):
            route = data["routes"][0]
            distance_km = route["distance"] / 1000.0
            duration_min = route["duration"] / 60.0

            # Format durasi yang lebih manusiawi
            if duration_min >= 60:
                hours = int(duration_min // 60)
                mins = int(duration_min % 60)
                duration_str = f"{hours} jam {mins} menit"
            else:
                duration_str = f"{duration_min:.0f} menit"

            return (
                f"Estimasi Rute (Mobil/Motor):\n"
                f"- Dari: {coord_a['display_name'].split(',')[0]}\n"
                f"- Ke: {coord_b['display_name'].split(',')[0]}\n"
                f"- Jarak: {distance_km:.2f} km\n"
                f"- Waktu Tempuh: ±{duration_str} (kondisi normal, di luar kemacetan)"
            )
        else:
            err_code = data.get("code", "UNKNOWN")
            return f"Server routing tidak dapat menemukan rute. Kode: {err_code}. Pastikan kedua lokasi bisa dijangkau dengan kendaraan darat."
    except requests.exceptions.ConnectionError:
        return "Gagal terhubung ke server OSRM. Periksa koneksi internet Anda."
    except Exception as e:
        return f"Gagal menghitung rute tempuh: {str(e)}"
