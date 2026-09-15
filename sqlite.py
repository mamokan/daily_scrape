import sqlite3
import csv
import os
import glob
csv_file = glob.glob('*.csv')[0]  # Path to your CSV file
scrape_db = 'scrape_data.db'  # Path to your SQLite database

def load_from_csv_to_sqlite(csv_file, scrape_db):
    # Connect to the SQLite database (or create it if it doesn't exist)
    conn = sqlite3.connect(scrape_db)
    cursor = conn.cursor()

    # Create a table (if it doesn't exist)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS data (
            reference TEXT,
            acheteur TEXT,
            objet TEXT,
            date_publication TEXT,
            date_limite TEXT,
            lien_detail TEXT
        )
    '''
                   )

    # Read data from the CSV file and insert it into the SQLite database
    with open(csv_file, 'r',encoding='utf-8') as file:
        reader = csv.reader(file) 
        next(reader) 
        for line in reader:
            values = line[0:6]  # Adjust the slice based on the number of columns in your CSV
            cursor.execute('REPLACE INTO data ( reference, date_publication, acheteur, objet, date_limite, lien_detail ) VALUES ( ?, ?, ?, ?, ?,?)', values)

    # Commit the changes and close the connection
    conn.commit()
    conn.close()
    os.remove(csv_file)  # Delete the CSV file after loading data

if __name__ == '__main__':
    load_from_csv_to_sqlite(csv_file, scrape_db)