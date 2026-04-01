from flask import Flask, request, render_template, redirect, url_for, flash
import requests
import os
import sqlite3
import json
import time
import plotly.graph_objects as go
import plotly.io as pio
from collections import Counter, defaultdict
from datetime import datetime
from dotenv import load_dotenv

load_dotenv('first.env')

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "levitate-dev-key")


# ─── Jinja2 Filters ─────────────────────────────────────────────────────────

@app.template_filter('duration')
def fmt_duration(seconds):
    if not seconds:
        return '--'
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


@app.template_filter('pace')
def fmt_pace(mps):
    """m/s → MM:SS /km string"""
    if not mps or mps < 0.3:
        return '--'
    pk = 1000 / (mps * 60)
    return f"{int(pk)}:{int((pk % 1) * 60):02d}"


@app.template_filter('km')
def fmt_km(meters):
    if not meters:
        return '--'
    return f"{meters / 1000:.2f}"


@app.template_filter('fdate')
def fmt_date(s):
    if not s:
        return '--'
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%b %-d, %Y")
    except Exception:
        return s


@app.template_filter('ftime')
def fmt_time_of_day(s):
    if not s:
        return '--'
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").strftime("%-I:%M %p")
    except Exception:
        return s


@app.template_filter('sport_icon')
def sport_icon(sport):
    icons = {
        "Run": "🏃", "TrailRun": "🏔️", "VirtualRun": "🖥️",
        "Ride": "🚴", "VirtualRide": "🖥️", "MountainBikeRide": "🚵",
        "Swim": "🏊", "Walk": "🚶", "Hike": "🥾",
        "WeightTraining": "🏋️", "Yoga": "🧘", "Rowing": "🚣",
        "Kayaking": "🛶", "Skiing": "⛷️", "Snowboard": "🏂",
    }
    return icons.get(sport, "🏅")


# ─── StravaAPI ───────────────────────────────────────────────────────────────

class StravaAPI:
    _DARK = dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#adb5bd"),
        margin=dict(l=50, r=20, t=45, b=45),
        height=300,
    )

    def __init__(self, db_name="strava_data.db"):
        self.db_name = db_name
        self.client_id = os.getenv("STRAVA_CLIENT_ID")
        self.client_secret = os.getenv("STRAVA_CLIENT_SECRET")
        self.refresh_token = os.getenv("STRAVA_REFRESH_TOKEN")
        self.token_url = "https://www.strava.com/oauth/token"
        self.base_url = "https://www.strava.com/api/v3"
        # Token cache — avoid one refresh per API call
        self._access_token = None
        self._token_expires_at = 0
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_name) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS activities_list (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    data        TEXT,
                    fetched_at  INTEGER
                );
                CREATE TABLE IF NOT EXISTS activity_details (
                    activity_id TEXT PRIMARY KEY,
                    data        TEXT
                );
                CREATE TABLE IF NOT EXISTS activity_streams (
                    activity_id TEXT PRIMARY KEY,
                    data        TEXT
                );
                CREATE TABLE IF NOT EXISTS activity_zones (
                    activity_id TEXT PRIMARY KEY,
                    zone_data   TEXT
                );
                CREATE TABLE IF NOT EXISTS activity_laps (
                    activity_id TEXT PRIMARY KEY,
                    data        TEXT
                );
            """)
            conn.commit()

    # ── Auth ─────────────────────────────────────────────────────────────────

    def get_access_token(self):
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        resp = requests.post(self.token_url, data={
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': self.refresh_token,
            'grant_type': 'refresh_token',
        })
        if resp.status_code != 200:
            return None
        data = resp.json()
        self._access_token = data.get('access_token')
        self._token_expires_at = data.get('expires_at', 0)
        return self._access_token

    def _get(self, path, params=None):
        token = self.get_access_token()
        if not token:
            return None
        resp = requests.get(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params or {},
        )
        return resp.json() if resp.status_code == 200 else None

    # ── Activities List ──────────────────────────────────────────────────────

    def get_activities(self, force_refresh=False):
        if not force_refresh:
            with sqlite3.connect(self.db_name) as conn:
                row = conn.execute(
                    "SELECT data FROM activities_list ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if row:
                    return json.loads(row[0])

        all_acts = []
        page = 1
        while True:
            batch = self._get("/athlete/activities", {"per_page": 100, "page": page})
            if not batch:
                break
            all_acts.extend(batch)
            if len(batch) < 100:
                break
            page += 1

        if all_acts:
            with sqlite3.connect(self.db_name) as conn:
                conn.execute("DELETE FROM activities_list")
                conn.execute(
                    "INSERT INTO activities_list (data, fetched_at) VALUES (?, ?)",
                    (json.dumps(all_acts), int(time.time())),
                )
                conn.commit()
        return all_acts

    # ── Activity Detail ──────────────────────────────────────────────────────

    def get_activity_detail(self, activity_id):
        aid = str(activity_id)
        with sqlite3.connect(self.db_name) as conn:
            row = conn.execute(
                "SELECT data FROM activity_details WHERE activity_id = ?", (aid,)
            ).fetchone()
            if row:
                return json.loads(row[0])
        data = self._get(f"/activities/{aid}")
        if data:
            with sqlite3.connect(self.db_name) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO activity_details (activity_id, data) VALUES (?, ?)",
                    (aid, json.dumps(data)),
                )
                conn.commit()
        return data

    # ── Streams ──────────────────────────────────────────────────────────────

    def get_activity_streams(self, activity_id):
        aid = str(activity_id)
        with sqlite3.connect(self.db_name) as conn:
            row = conn.execute(
                "SELECT data FROM activity_streams WHERE activity_id = ?", (aid,)
            ).fetchone()
            if row:
                return json.loads(row[0])
        data = self._get(
            f"/activities/{aid}/streams",
            {
                "keys": "time,distance,altitude,velocity_smooth,heartrate,cadence,watts,grade_smooth",
                "key_by_type": "true",
            },
        )
        if data:
            with sqlite3.connect(self.db_name) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO activity_streams (activity_id, data) VALUES (?, ?)",
                    (aid, json.dumps(data)),
                )
                conn.commit()
        return data

    # ── Zones ────────────────────────────────────────────────────────────────

    def get_activity_zones(self, activity_id):
        aid = str(activity_id)
        with sqlite3.connect(self.db_name) as conn:
            row = conn.execute(
                "SELECT zone_data FROM activity_zones WHERE activity_id = ?", (aid,)
            ).fetchone()
            if row:
                return json.loads(row[0])
        data = self._get(f"/activities/{aid}/zones")
        if data:
            with sqlite3.connect(self.db_name) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO activity_zones (activity_id, zone_data) VALUES (?, ?)",
                    (aid, json.dumps(data)),
                )
                conn.commit()
        return data

    # ── Laps ─────────────────────────────────────────────────────────────────

    def get_activity_laps(self, activity_id):
        aid = str(activity_id)
        with sqlite3.connect(self.db_name) as conn:
            row = conn.execute(
                "SELECT data FROM activity_laps WHERE activity_id = ?", (aid,)
            ).fetchone()
            if row:
                return json.loads(row[0])
        data = self._get(f"/activities/{aid}/laps")
        if data:
            with sqlite3.connect(self.db_name) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO activity_laps (activity_id, data) VALUES (?, ?)",
                    (aid, json.dumps(data)),
                )
                conn.commit()
        return data

    # ── Chart Helpers ────────────────────────────────────────────────────────

    def _chart(self, fig, div_id=None):
        fig.update_layout(**self._DARK)
        return pio.to_html(
            fig,
            full_html=False,
            include_plotlyjs=False,
            div_id=div_id,
            config={"displayModeBar": False},
        )

    # ── Activity Charts ──────────────────────────────────────────────────────

    def chart_zones(self, zones_data):
        """Time in HR zones — bar chart."""
        labels, times = [], []
        for obj in (zones_data or []):
            if obj.get("type") == "heartrate":
                for i, b in enumerate(obj.get("distribution_buckets", [])):
                    labels.append(f"Z{i + 1}")
                    times.append(b.get("time", 0))
        if not labels:
            return None

        zone_colors = ["#4cc9f0", "#4361ee", "#7209b7", "#f72585", "#e63946"]
        mins = [t // 60 for t in times]
        texts = [f"{t // 60}m {t % 60}s" for t in times]
        fig = go.Figure(go.Bar(
            x=labels, y=mins,
            marker_color=zone_colors[: len(labels)],
            text=texts, textposition="outside",
        ))
        fig.update_layout(title="Time in HR Zones", xaxis_title="Zone", yaxis_title="min")
        return self._chart(fig, "chart-zones")

    def chart_streams(self, streams, sport):
        """All stream-based charts; returns dict of name → HTML string."""
        if not streams:
            return {}

        is_run = "run" in sport.lower()
        dist_data = streams.get("distance", {}).get("data", [])
        dist_km = [d / 1000 for d in dist_data]
        charts = {}

        # Heart rate
        if "heartrate" in streams:
            hr = streams["heartrate"]["data"]
            fig = go.Figure(go.Scatter(
                x=dist_km, y=hr, fill="tozeroy",
                line=dict(color="#f72585", width=1.5), name="HR",
            ))
            fig.update_layout(title="Heart Rate", xaxis_title="km", yaxis_title="bpm")
            charts["hr"] = self._chart(fig, "chart-hr")

        # Pace / Speed
        if "velocity_smooth" in streams:
            vel = streams["velocity_smooth"]["data"]
            if is_run:
                y = [1000 / (v * 60) if v > 0.5 else None for v in vel]
                ylabel, title = "min/km", "Pace"
                yaxis_extra = dict(autorange="reversed")
            else:
                y = [v * 3.6 for v in vel]
                ylabel, title = "km/h", "Speed"
                yaxis_extra = {}
            fig = go.Figure(go.Scatter(
                x=dist_km, y=y, line=dict(color="#4cc9f0", width=1.5), name=ylabel,
            ))
            fig.update_layout(
                title=title, xaxis_title="km", yaxis_title=ylabel, yaxis=yaxis_extra
            )
            charts["pace"] = self._chart(fig, "chart-pace")

        # Elevation
        if "altitude" in streams:
            alt = streams["altitude"]["data"]
            fig = go.Figure(go.Scatter(
                x=dist_km, y=alt, fill="tozeroy",
                fillcolor="rgba(74,222,128,0.2)",
                line=dict(color="#4ade80", width=1.5), name="Alt",
            ))
            fig.update_layout(title="Elevation Profile", xaxis_title="km", yaxis_title="m")
            charts["elevation"] = self._chart(fig, "chart-elevation")

        # Cadence
        if "cadence" in streams:
            cad = streams["cadence"]["data"]
            ylabel = "spm" if is_run else "rpm"
            fig = go.Figure(go.Scatter(
                x=dist_km, y=cad, line=dict(color="#a78bfa", width=1.5), name="Cadence",
            ))
            fig.update_layout(title="Cadence", xaxis_title="km", yaxis_title=ylabel)
            charts["cadence"] = self._chart(fig, "chart-cadence")

        # Power
        if "watts" in streams:
            fig = go.Figure(go.Scatter(
                x=dist_km, y=streams["watts"]["data"],
                line=dict(color="#fb923c", width=1.5), name="Watts",
            ))
            fig.update_layout(title="Power", xaxis_title="km", yaxis_title="W")
            charts["power"] = self._chart(fig, "chart-power")

        # Grade
        if "grade_smooth" in streams:
            grade = streams["grade_smooth"]["data"]
            fig = go.Figure(go.Scatter(
                x=dist_km, y=grade, fill="tozeroy",
                fillcolor="rgba(251,146,60,0.15)",
                line=dict(color="#fb923c", width=1), name="Grade",
            ))
            fig.update_layout(title="Gradient", xaxis_title="km", yaxis_title="%")
            charts["grade"] = self._chart(fig, "chart-grade")

        # HR vs Pace scatter (aerobic efficiency) — downsample
        if "heartrate" in streams and "velocity_smooth" in streams:
            hr = streams["heartrate"]["data"][::8]
            vel = streams["velocity_smooth"]["data"][::8]
            dk = dist_km[::8]
            if is_run:
                x = [1000 / (v * 60) if v > 0.5 else None for v in vel]
                xlabel = "min/km"
                x_extra = dict(autorange="reversed")
            else:
                x = [v * 3.6 for v in vel]
                xlabel = "km/h"
                x_extra = {}
            fig = go.Figure(go.Scattergl(
                x=x, y=hr, mode="markers",
                marker=dict(
                    color=dk, colorscale="Plasma", size=4, opacity=0.6,
                    colorbar=dict(title="km", thickness=10),
                ),
                name="HR vs Pace",
            ))
            fig.update_layout(
                title="HR vs Pace (Aerobic Efficiency)",
                xaxis_title=xlabel, xaxis=x_extra,
                yaxis_title="bpm",
            )
            charts["hr_scatter"] = self._chart(fig, "chart-hr-scatter")

        return charts

    def chart_laps(self, laps, sport):
        """Lap comparison bar chart."""
        if not laps:
            return None
        is_run = "run" in (sport or "").lower()
        names = [f"Lap {l.get('lap_index', i + 1)}" for i, l in enumerate(laps)]

        if is_run:
            vals = [
                (l.get("elapsed_time", 0) / 60) / max(l.get("distance", 1) / 1000, 0.001)
                for l in laps
            ]
            ylabel, yaxis_extra = "min/km", dict(autorange="reversed")
        else:
            vals = [l.get("average_speed", 0) * 3.6 for l in laps]
            ylabel, yaxis_extra = "km/h", {}

        avg_hr = [l.get("average_heartrate") for l in laps]
        has_hr = any(v is not None for v in avg_hr)

        fig = go.Figure()
        fig.add_trace(go.Bar(x=names, y=vals, name=ylabel, marker_color="#4361ee"))
        if has_hr:
            fig.add_trace(go.Scatter(
                x=names, y=avg_hr, name="Avg HR", yaxis="y2",
                line=dict(color="#f72585", width=2), mode="lines+markers",
            ))
            fig.update_layout(
                yaxis2=dict(
                    title="bpm", overlaying="y", side="right",
                    showgrid=False, color="#f72585",
                ),
            )
        fig.update_layout(
            title="Lap Breakdown",
            xaxis_title="Lap", yaxis_title=ylabel,
            yaxis=yaxis_extra, barmode="group",
        )
        return self._chart(fig, "chart-laps")

    # ── Trend / Dashboard Charts ─────────────────────────────────────────────

    def chart_trends(self, activities):
        """Returns dict of chart name → HTML for the /dashboard page."""
        if not activities:
            return {}
        charts = {}

        sport_colors = {
            "Run": "#f72585", "TrailRun": "#b5179e", "Ride": "#4361ee",
            "VirtualRide": "#3a0ca3", "Swim": "#4cc9f0", "Walk": "#4ade80",
            "Hike": "#86efac", "WeightTraining": "#fb923c",
        }

        # Sport breakdown — donut
        counts = Counter(
            a.get("sport_type") or a.get("type", "Other") for a in activities
        )
        fig = go.Figure(go.Pie(
            labels=list(counts.keys()), values=list(counts.values()), hole=0.45,
            marker_colors=[sport_colors.get(s, "#94a3b8") for s in counts],
        ))
        fig.update_layout(title="Activities by Type", **{**self._DARK, "height": 320})
        charts["sport_pie"] = pio.to_html(
            fig, full_html=False, include_plotlyjs=False, div_id="chart-sport-pie",
            config={"displayModeBar": False},
        )

        # Weekly stacked distance — last 20 weeks
        weekly: dict = defaultdict(lambda: defaultdict(float))
        for a in activities:
            try:
                dt = datetime.strptime(a["start_date_local"][:10], "%Y-%m-%d")
                wk = dt.strftime("%G-W%V")
                sp = a.get("sport_type") or a.get("type", "Other")
                weekly[wk][sp] += a.get("distance", 0) / 1000
            except Exception:
                pass

        weeks = sorted(weekly)[-20:]
        sports = sorted({s for w in weekly.values() for s in w})
        fig2 = go.Figure()
        for sp in sports:
            fig2.add_trace(go.Bar(
                name=sp, x=weeks,
                y=[weekly[w].get(sp, 0) for w in weeks],
                marker_color=sport_colors.get(sp, "#94a3b8"),
            ))
        fig2.update_layout(
            title="Weekly Distance — last 20 weeks",
            barmode="stack", xaxis_title="Week", yaxis_title="km",
            **{**self._DARK, "height": 340},
        )
        charts["weekly"] = pio.to_html(
            fig2, full_html=False, include_plotlyjs=False, div_id="chart-weekly",
            config={"displayModeBar": False},
        )

        # Monthly stacked distance — last 12 months
        monthly: dict = defaultdict(lambda: defaultdict(float))
        for a in activities:
            try:
                dt = datetime.strptime(a["start_date_local"][:10], "%Y-%m-%d")
                mo = dt.strftime("%Y-%m")
                sp = a.get("sport_type") or a.get("type", "Other")
                monthly[mo][sp] += a.get("distance", 0) / 1000
            except Exception:
                pass

        months = sorted(monthly)[-12:]
        fig3 = go.Figure()
        for sp in sports:
            fig3.add_trace(go.Bar(
                name=sp, x=months,
                y=[monthly[m].get(sp, 0) for m in months],
                marker_color=sport_colors.get(sp, "#94a3b8"),
            ))
        fig3.update_layout(
            title="Monthly Distance — last 12 months",
            barmode="stack", xaxis_title="Month", yaxis_title="km",
            **{**self._DARK, "height": 340},
        )
        charts["monthly"] = pio.to_html(
            fig3, full_html=False, include_plotlyjs=False, div_id="chart-monthly",
            config={"displayModeBar": False},
        )

        # Average HR trend — last 30 activities with HR data (runs)
        runs_with_hr = [
            a for a in activities
            if "run" in (a.get("sport_type") or a.get("type", "")).lower()
            and a.get("average_heartrate")
        ]
        runs_with_hr = sorted(runs_with_hr, key=lambda a: a.get("start_date_local", ""))[-40:]
        if runs_with_hr:
            dates = [a["start_date_local"][:10] for a in runs_with_hr]
            hr_vals = [a["average_heartrate"] for a in runs_with_hr]
            dist_vals = [a.get("distance", 0) / 1000 for a in runs_with_hr]

            fig4 = go.Figure()
            fig4.add_trace(go.Scatter(
                x=dates, y=hr_vals, name="Avg HR",
                line=dict(color="#f72585", width=2), mode="lines+markers",
            ))
            fig4.add_trace(go.Bar(
                x=dates, y=dist_vals, name="Distance (km)", yaxis="y2",
                marker_color="rgba(67,97,238,0.3)",
            ))
            fig4.update_layout(
                title="Run HR Trend (last 40 runs with HR)",
                xaxis_title="Date", yaxis_title="bpm",
                yaxis2=dict(
                    title="km", overlaying="y", side="right",
                    showgrid=False, color="#4361ee",
                ),
                **{**self._DARK, "height": 340},
            )
            charts["hr_trend"] = pio.to_html(
                fig4, full_html=False, include_plotlyjs=False, div_id="chart-hr-trend",
                config={"displayModeBar": False},
            )

        # Scatter: distance vs avg HR (by sport) — all activities
        acts_with_hr = [a for a in activities if a.get("average_heartrate") and a.get("distance")]
        if acts_with_hr:
            for sp in sports:
                sp_acts = [a for a in acts_with_hr
                           if (a.get("sport_type") or a.get("type", "")) == sp]
                if not sp_acts:
                    continue
            fig5 = go.Figure()
            for sp in sports:
                sp_acts = [a for a in acts_with_hr
                           if (a.get("sport_type") or a.get("type", "")) == sp]
                if not sp_acts:
                    continue
                fig5.add_trace(go.Scatter(
                    x=[a["distance"] / 1000 for a in sp_acts],
                    y=[a["average_heartrate"] for a in sp_acts],
                    mode="markers",
                    name=sp,
                    marker=dict(
                        color=sport_colors.get(sp, "#94a3b8"),
                        size=6, opacity=0.7,
                    ),
                ))
            fig5.update_layout(
                title="Distance vs Avg HR",
                xaxis_title="Distance (km)", yaxis_title="Avg HR (bpm)",
                **{**self._DARK, "height": 340},
            )
            charts["dist_hr_scatter"] = pio.to_html(
                fig5, full_html=False, include_plotlyjs=False,
                div_id="chart-dist-hr-scatter",
                config={"displayModeBar": False},
            )

        return charts


# ─── Routes ──────────────────────────────────────────────────────────────────

strava = StravaAPI()


@app.route('/')
def index():
    activities = strava.get_activities()

    sport_filter = request.args.get('sport', '')
    search = request.args.get('q', '').strip().lower()
    sort = request.args.get('sort', 'newest')

    # Build sport list before filtering
    all_sports = sorted({
        a.get('sport_type') or a.get('type', '') for a in activities
    } - {''})

    filtered = activities
    if sport_filter:
        filtered = [
            a for a in filtered
            if (a.get('sport_type') or a.get('type', '')) == sport_filter
        ]
    if search:
        filtered = [a for a in filtered if search in a.get('name', '').lower()]

    if sort == 'oldest':
        filtered = sorted(filtered, key=lambda a: a.get('start_date_local', ''))
    elif sort == 'longest':
        filtered = sorted(filtered, key=lambda a: a.get('distance', 0), reverse=True)
    elif sort == 'elevation':
        filtered = sorted(filtered, key=lambda a: a.get('total_elevation_gain', 0), reverse=True)
    # 'newest' is default — API returns newest first already

    return render_template(
        'index.html',
        activities=filtered,
        all_sports=all_sports,
        sport_filter=sport_filter,
        search=search,
        sort=sort,
        total=len(activities),
    )


@app.route('/sync')
def sync():
    acts = strava.get_activities(force_refresh=True)
    flash(f"Synced {len(acts)} activities from Strava.")
    return redirect(url_for('index'))


@app.route('/activity/<int:activity_id>')
def activity(activity_id):
    detail = strava.get_activity_detail(activity_id)
    if not detail:
        return "Activity not found or API error.", 404

    sport = detail.get('type', '')
    streams = strava.get_activity_streams(activity_id)
    zones = strava.get_activity_zones(activity_id)
    laps = strava.get_activity_laps(activity_id)

    is_run = 'run' in sport.lower()
    avg_speed = detail.get('average_speed', 0)
    if is_run and avg_speed:
        pace_str = fmt_pace(avg_speed) + ' /km'
    elif avg_speed:
        pace_str = f"{avg_speed * 3.6:.1f} km/h"
    else:
        pace_str = '--'

    return render_template(
        'activity.html',
        detail=detail,
        sport=sport,
        is_run=is_run,
        pace_str=pace_str,
        stream_charts=strava.chart_streams(streams, sport),
        zones_chart=strava.chart_zones(zones),
        laps_chart=strava.chart_laps(laps, sport),
        laps=laps or [],
        activity_id=activity_id,
    )


@app.route('/dashboard')
def dashboard():
    activities = strava.get_activities()

    totals = {
        'count': len(activities),
        'distance': sum(a.get('distance', 0) for a in activities) / 1000,
        'time': sum(a.get('moving_time', 0) for a in activities) / 3600,
        'elevation': sum(a.get('total_elevation_gain', 0) for a in activities),
    }

    return render_template(
        'dashboard.html',
        charts=strava.chart_trends(activities),
        totals=totals,
    )


if __name__ == '__main__':
    app.run(debug=True)
