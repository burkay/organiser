import sqlite3
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# Connect to SQLite database
DATABASE = 'documents.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    return conn

# Create the database table for documents
def init_db():
    with app.app_context():
        db = get_db()
        db.execute('''CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            content TEXT NOT NULL
        )''')
        db.commit()

init_db()

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    content = file.read().decode('utf-8')
    db = get_db()
    db.execute('INSERT INTO documents (filename, content) VALUES (?, ?)', (file.filename, content))
    db.commit()
    return jsonify({'message': 'File uploaded successfully!'}), 200

@app.route('/search', methods=['GET'])
def search_documents():
    query = request.args.get('query')
    db = get_db()
    cursor = db.execute('SELECT * FROM documents WHERE content LIKE ?', ('%' + query + '%',))
    results = cursor.fetchall()
    return jsonify(results), 200

@app.route('/panel')
def management_panel():
    db = get_db()
    cursor = db.execute('SELECT * FROM documents')
    documents = cursor.fetchall()
    return render_template('panel.html', documents=documents)

if __name__ == '__main__':
    app.run(debug=True)