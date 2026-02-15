import sqlite3

class Database:
    def __init__(self, db_name):
        self.connection = sqlite3.connect(db_name)
        self.cursor = self.connection.cursor()

    def create_table(self):
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, name TEXT, done BOOLEAN)''')
        self.connection.commit()

    def add_task(self, name):
        self.cursor.execute('''INSERT INTO tasks (name, done) VALUES (?, ?)''', (name, False))
        self.connection.commit()

    def get_tasks(self):
        self.cursor.execute('''SELECT * FROM tasks''')
        return self.cursor.fetchall()

    def close(self):
        self.connection.close()

# Example of using the Database class
if __name__ == '__main__':
    db = Database('tasks.db')
    db.create_table()
    db.add_task('Example Task')
    tasks = db.get_tasks()
    print(tasks)
    db.close()