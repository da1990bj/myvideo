import requests
import sys

def test_api(token):
    url = "http://localhost:8000/users/me/videos"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        res = requests.get(url, headers=headers)
        print(f"Status: {res.status_code}")
        print(f"Body: {res.text}")
    except Exception as e:
        print(e)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_api(sys.argv[1])
    else:
        print("Usage: python test_api.py <TOKEN>")
