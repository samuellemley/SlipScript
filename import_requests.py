import requests

API_KEY = "YOUR_API_KEY"
BASE_URL = "https://api-na.hosted.exlibrisgroup.com/almaws/v1"
BARCODE = "38482018967766"

resp = requests.get(
    f"{BASE_URL}/items",
    params={"item_barcode": BARCODE},
    headers={"Authorization": f"apikey {API_KEY}"},
    timeout=30,
)

print(resp.status_code)
print(resp.text)