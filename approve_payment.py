import requests
import http.cookiejar

# Create a session with cookie support
session = requests.Session()
session.cookies = http.cookiejar.CookieJar()

# Login first
login_url = 'http://127.0.0.1:5000/login'
login_data = {'username': 'boss_test', 'password': 'password123'}
r = session.post(login_url, data=login_data, allow_redirects=True)
print(f'Login response: {r.status_code}')

# Now approve the payment
approve_url = 'http://127.0.0.1:5000/boss/payment_review/106/approve'
r = session.post(approve_url, allow_redirects=True)
print(f'Approve response: {r.status_code}')
print(f'Approve URL: {r.url}')
