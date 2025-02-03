from flask import Flask, request, render_template, jsonify
import json
import sqlite3
import plotly.graph_objects as go
import plotly.io as pio
from io import BytesIO
import base64

app = Flask(__name__)

class StravaAPI:
    def __init__(self, db_name="strava_data.db"):
        self.db_name = db_name

    def fetch_activity_zones_from_db(self, activity_id):
        """Fetch activity zones from SQLite for visualization."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT zone_data FROM activity_zones WHERE activity_id = ?", (activity_id,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])  # Convert string back to JSON (dictionary)
            else:
                return None

    def visualize_activity_zones(self, activity_id):
        """Visualizes the activity zones using Plotly."""
        zone_data = self.fetch_activity_zones_from_db(activity_id)

        if not zone_data:
            return None

        # Prepare data for Plotly visualization
        zones = []
        zone_labels = []

        for zone in zone_data:
            zones.append(zone["min"])  # Store min value for each zone
            zone_labels.append(f"Zone {zone['name']}")

        # Create a bar chart using Plotly
        fig = go.Figure([go.Bar(x=zone_labels, y=zones)])
        fig.update_layout(title=f"Heart Rate Zones for Activity {activity_id}",
                          xaxis_title="Zone",
                          yaxis_title="Heart Rate (bpm)")

        # Convert the plot to an image and return it as base64
        img_bytes = pio.to_image(fig, format="png")
        img_base64 = base64.b64encode(img_bytes).decode("utf-8")
        return img_base64

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
        return f"Error: No zone data found for activity ID {activity_id}", 400

if __name__ == "__main__":
    app.run(debug=True)
