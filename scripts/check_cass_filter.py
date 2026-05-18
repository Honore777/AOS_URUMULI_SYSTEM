from app import app
import json

with app.test_client() as c:
    rv = c.post('/cassiterite/api/filter_stocks', json={'start_date': '', 'end_date': ''})
    print('STATUS', rv.status_code)
    try:
        data = rv.get_json()
        print('JSON:', json.dumps(data or {}, indent=2, default=str))
    except Exception as e:
        print('GET_JSON_FAILED:', e)
        print('DATA_TEXT:', rv.data[:1000])
