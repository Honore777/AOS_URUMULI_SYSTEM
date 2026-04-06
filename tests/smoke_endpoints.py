#!/usr/bin/env python3
import json
import urllib.request
import urllib.error


def post(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            print(f"URL: {url}")
            print(f"Status: {resp.status} {getattr(resp, 'reason', '')}")
            print("Content-Type:", resp.getheader('Content-Type'))
            print(body[:4096])
            print()
    except urllib.error.HTTPError as e:
        print(f"URL: {url} -> HTTPError {e.code} {e.reason}")
        try:
            print(e.read().decode('utf-8')[:4096])
        except Exception:
            pass
    except Exception as e:
        print(f"URL: {url} -> ERROR: {e}")


if __name__ == '__main__':
    base = 'http://127.0.0.1:5000'
    payload = {"start_date": "", "end_date": "", "voucher_no": "", "page": 1, "per_page": 5, "include_aggregates": False}
    post(base + '/copper/api/filter_stocks', payload)
    post(base + '/cassiterite/api/filter_stocks', payload)
