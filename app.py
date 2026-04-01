from flask import Flask, request, render_template
import requests
import os
import sqlite3
import json
import plotly.graph_objects as go
import plotly.io as pio
import base64
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)

class StravaAPI:
    def __init__(self, db_name="strava_data.db"):
        # DB
        self.db_name = db_name

        # Strava API creds (set these as environment variables)
        self.client_id = os.getenv("STRAVA_CLIENT_ID")
        self.client_secret = os.getenv("STRAVA_CLIENT_SECRET")
        self.refresh_token = os.getenv("STRAVA_REFRESH_TOKEN")
        self.token_url = "https://www.strava.com/oauth/token"
        self.base_url = "https://www.strava.com/api/v3"

        # Ensure DB table exists
        self._init_db()

    def _init_db(self):
        """Create the activity_zones table if it doesn't exist."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS activity_zones (
                    activity_id TEXT PRIMARY KEY,
                    zone_data TEXT
                )
            """)
            conn.commit()

    # ========== STRAVA API ==========
    def get_access_token(self):
        """Fetch a new Strava API access token using refresh token."""
        response = requests.post(self.token_url, data={
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': self.refresh_token,
            'grant_type': 'refresh_token'
        })
        if response.status_code != 200:
            return None
        return response.json().get('access_token')

    def fetch_activity_zones_from_api(self, activity_id):
        """Fetch activity zones directly from Strava API."""
        access_token = self.get_access_token()
        if not access_token:
            return None

        url = f"{self.base_url}/activities/{activity_id}/zones"
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            return None
        return response.json()

    # ========== DATABASE ==========
    def fetch_activity_zones_from_db(self, activity_id):
        """Fetch activity zones from SQLite DB."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT zone_data FROM activity_zones WHERE activity_id = ?", (activity_id,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
        return None

    def save_activity_zones_to_db(self, activity_id, zone_data):
        """Save API-fetched activity zones into DB for future use."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO activity_zones (activity_id, zone_data)
                VALUES (?, ?)
            """, (activity_id, json.dumps(zone_data)))
            conn.commit()

    # ========== VISUALIZATION ==========
    def visualize_activity_zones(self, activity_id):
        """Try DB first, then API (and save result to DB)."""
        zone_data = self.fetch_activity_zones_from_db(activity_id)

        if not zone_data:
            zone_data = self.fetch_activity_zones_from_api(activity_id)
            if zone_data:
                self.save_activity_zones_to_db(activity_id, zone_data)

        if not zone_data:
            return None

        zones = []
        zone_labels = []

        # Handle API data (list of objects) or cached DB data (JSON string)
        if isinstance(zone_data, list):  
            # API response
            for zone in zone_data:
                if zone.get("type") == "heartrate":
                    for j, z in enumerate(zone.get("distribution_buckets", [])):
                        zones.append(z.get("min", 0))
                        zone_labels.append(f"Zone {j+1}")
        else:
            # DB cached data
            for zone in zone_data:
                zones.append(zone["min"])
                zone_labels.append(f"Zone {zone['name']}")

        if not zones:
            return None

        # Create Plotly chart
        fig = go.Figure([go.Bar(x=zone_labels, y=zones)])
        fig.update_layout(title=f"Heart Rate Zones for Activity {activity_id}",
                          xaxis_title="Zone",
                          yaxis_title="Heart Rate (bpm)")

        # Convert chart → base64 image
        img_bytes = pio.to_image(fig, format="png")
        img_base64 = base64.b64encode(img_bytes).decode("utf-8")
        return img_base64


# ========== FLASK ROUTES ==========
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/visualize', methods=['POST'])
def visualize():
    activity_id = request.form['activity_id']
    strava_api = StravaAPI()
    plot_image = strava_api.visualize_activity_zones(activity_id)

    if plot_image:
        return render_template('visualize.html', plot_image=plot_image, activity_id=activity_id)
    else:
        return f"Error: No zone data available for activity ID {activity_id}", 400


if __name__ == "__main__":
    app.run(debug=True)
