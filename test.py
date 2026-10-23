import requests

ip = "129.226.195.146"

try:
    url = f"https://ipapi.co/{ip}/json/"
    print(f"Testing URL: {url}")

    response = requests.get(url, timeout=10)
    print(f"HTTP Status: {response.status_code}")
    response.raise_for_status()

    data = response.json()
    print("API Response:", data)

    location = ", ".join(value for value in (
        data.get("city"),
        data.get("region"),
        data.get("country_name")
    ) if value)

    print(f"Resolved Location: {location or 'Unknown'}")

except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")