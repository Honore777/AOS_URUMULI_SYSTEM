from flask import Flask
app = Flask(__name__)

@app.route('/a/<x>')
def a(x):
    pass

with app.test_request_context():
    print(repr(app.url_for('a', x='')))
    print(repr(app.url_for('a', x='John Doe')))
